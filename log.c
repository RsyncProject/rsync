/* -*- c-file-style: "linux"; -*-

   Copyright (C) 1998-2001 by Andrew Tridgell <tridge@samba.org>
   Copyright (C) 2000-2001 by Martin Pool <mbp@samba.org>

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/*
  Logging and utility functions.
  tridge, May 1998

  Mapping to human-readable messages added by Martin Pool
  <mbp@samba.org>, Oct 2000.
  */
#include "rsync.h"

extern int am_daemon;
extern int am_server;
extern int am_sender;
extern int quiet;
extern int module_id;
extern int msg_fd_out;
extern char *auth_user;
extern char *log_format;

static int log_initialised;
static char *logfname;
static FILE *logfile;
struct stats stats;

int log_got_error=0;

struct {
        int code;
        char const *name;
} const rerr_names[] = {
	{ RERR_SYNTAX     , "syntax or usage error" },
	{ RERR_PROTOCOL   , "protocol incompatibility" },
	{ RERR_FILESELECT , "errors selecting input/output files, dirs" },
	{ RERR_UNSUPPORTED, "requested action not supported" },
	{ RERR_STARTCLIENT, "error starting client-server protocol" },
	{ RERR_SOCKETIO   , "error in socket IO" },
	{ RERR_FILEIO     , "error in file IO" },
	{ RERR_STREAMIO   , "error in rsync protocol data stream" },
	{ RERR_MESSAGEIO  , "errors with program diagnostics" },
	{ RERR_IPC        , "error in IPC code" },
	{ RERR_SIGNAL     , "received SIGUSR1 or SIGINT" },
	{ RERR_WAITCHILD  , "some error returned by waitpid()" },
	{ RERR_MALLOC     , "error allocating core memory buffers" },
	{ RERR_PARTIAL    , "some files could not be transferred" },
	{ RERR_VANISHED   , "some files vanished before they could be transfered" },
	{ RERR_TIMEOUT    , "timeout in data send/receive" },
	{ RERR_CMD_FAILED , "remote shell failed" },
	{ RERR_CMD_KILLED , "remote shell killed" },
	{ RERR_CMD_RUN,     "remote command could not be run" },
	{ RERR_CMD_NOTFOUND, "remote command not found" },
	{ 0, NULL }
};



/*
 * Map from rsync error code to name, or return NULL.
 */
static char const *rerr_name(int code)
{
	int i;
	for (i = 0; rerr_names[i].name; i++) {
		if (rerr_names[i].code == code)
			return rerr_names[i].name;
	}
	return NULL;
}


static void logit(int priority, char *buf)
{
	if (logfname) {
		if (!logfile)
			log_open();
		fprintf(logfile,"%s [%d] %s",
			timestring(time(NULL)), (int)getpid(), buf);
		fflush(logfile);
	} else {
		syslog(priority, "%s", buf);
	}
}

void log_init(void)
{
	int options = LOG_PID;
	time_t t;

	if (log_initialised) return;
	log_initialised = 1;

	/* this looks pointless, but it is needed in order for the
	 * C library on some systems to fetch the timezone info
	 * before the chroot */
	t = time(NULL);
	localtime(&t);

	/* optionally use a log file instead of syslog */
	logfname = lp_log_file();
	if (logfname) {
		if (*logfname) {
			log_open();
			return;
		}
		logfname = NULL;
	}

#ifdef LOG_NDELAY
	options |= LOG_NDELAY;
#endif

#ifdef LOG_DAEMON
	openlog("rsyncd", options, lp_syslog_facility());
#else
	openlog("rsyncd", options);
#endif

#ifndef LOG_NDELAY
	logit(LOG_INFO,"rsyncd started\n");
#endif
}

void log_open(void)
{
	if (logfname && !logfile) {
		extern int orig_umask;
		int old_umask = umask(022 | orig_umask);
		logfile = fopen(logfname, "a");
		umask(old_umask);
	}
}

void log_close(void)
{
	if (logfile) {
		fclose(logfile);
		logfile = NULL;
	}
}

/* this is the underlying (unformatted) rsync debugging function. Call
 * it with FINFO, FERROR or FLOG */
void rwrite(enum logcode code, char *buf, int len)
{
	FILE *f=NULL;
	/* recursion can happen with certain fatal conditions */

	if (quiet && code == FINFO)
		return;

	if (len < 0)
		exit_cleanup(RERR_MESSAGEIO);

	buf[len] = 0;

	if (code == FLOG) {
		if (am_daemon) logit(LOG_INFO, buf);
		return;
	}

	if (am_server) {
		/* Pass it to non-server side, perhaps through our sibling. */
		if (msg_fd_out >= 0) {
			send_msg((enum msgcode)code, buf, len);
			return;
		}
		if (!am_daemon
		    && io_multiplex_write((enum msgcode)code, buf, len))
			return;
	}

	/* otherwise, if in daemon mode and either we are not a server
	 *  (that is, we are not running --daemon over a remote shell) or
	 *  the log has already been initialised, log the message on this
	 *  side because we don't want the client to see most errors for
	 *  security reasons.  We do want early messages when running daemon
	 *  mode over a remote shell to go to the remote side; those will
	 *  fall through to the next case.
	 * Note that this is only for the time before multiplexing is enabled.
	 */
	if (am_daemon && (!am_server || log_initialised)) {
		static int depth;
		int priority = LOG_INFO;
		if (code == FERROR) priority = LOG_WARNING;

		if (depth) return;

		depth++;

		log_init();
		logit(priority, buf);

		depth--;
		return;
	}

	if (code == FERROR) {
		log_got_error = 1;
		f = stderr;
	}

	if (code == FINFO) {
		if (am_server)
			f = stderr;
		else
			f = stdout;
	}

	if (!f) exit_cleanup(RERR_MESSAGEIO);

	if (fwrite(buf, len, 1, f) != 1) exit_cleanup(RERR_MESSAGEIO);

	if (buf[len-1] == '\r' || buf[len-1] == '\n') fflush(f);
}
		

/* This is the rsync debugging function. Call it with FINFO, FERROR or
 * FLOG. */
void rprintf(enum logcode code, const char *format, ...)
{
	va_list ap;
	char buf[1024];
	int len;

	va_start(ap, format);
	/* Note: might return -1 */
	len = vsnprintf(buf, sizeof(buf), format, ap);
	va_end(ap);

	/* Deal with buffer overruns.  Instead of panicking, just
	 * truncate the resulting string.  Note that some vsnprintf()s
	 * return -1 on truncation, e.g., glibc 2.0.6 and earlier. */
	if ((size_t) len > sizeof(buf)-1  ||  len < 0) {
		const char ellipsis[] = "[...]";

		/* Reset length, and zero-terminate the end of our buffer */
		len = sizeof(buf)-1;
		buf[len] = '\0';

		/* Copy the ellipsis to the end of the string, but give
		 * us one extra character:
		 *
		 *                  v--- null byte at buf[sizeof(buf)-1]
		 *        abcdefghij0
		 *     -> abcd[...]00  <-- now two null bytes at end
		 *
		 * If the input format string has a trailing newline,
		 * we copy it into that extra null; if it doesn't, well,
		 * all we lose is one byte.  */
		strncpy(buf+len-sizeof(ellipsis), ellipsis, sizeof(ellipsis));
		if (format[strlen(format)-1] == '\n') {
			buf[len-1] = '\n';
		}
	}

	rwrite(code, buf, len);
}


/* This is like rprintf, but it also tries to print some
 * representation of the error code.  Normally errcode = errno.
 *
 * Unlike rprintf, this always adds a newline and there should not be
 * one in the format string.
 *
 * Note that since strerror might involve dynamically loading a
 * message catalog we need to call it once before chroot-ing. */
void rsyserr(enum logcode code, int errcode, const char *format, ...)
{
	va_list ap;
	char buf[1024];
	int len;
	size_t sys_len;
	char *sysmsg;

	va_start(ap, format);
	/* Note: might return <0 */
	len = vsnprintf(buf, sizeof(buf), format, ap);
	va_end(ap);

	/* TODO: Put in RSYNC_NAME at the start. */

	if ((size_t) len > sizeof(buf)-1)
		exit_cleanup(RERR_MESSAGEIO);

	sysmsg = strerror(errcode);
	sys_len = strlen(sysmsg);
	if ((size_t) len + 3 + sys_len > sizeof(buf) - 1)
		exit_cleanup(RERR_MESSAGEIO);

	strcpy(buf + len, ": ");
	len += 2;
	strcpy(buf + len, sysmsg);
	len += sys_len;
	strcpy(buf + len, "\n");
	len++;

	rwrite(code, buf, len);
}



void rflush(enum logcode code)
{
	FILE *f = NULL;
	
	if (am_daemon) {
		return;
	}

	if (code == FLOG) {
		return;
	}

	if (code == FERROR) {
		f = stderr;
	}

	if (code == FINFO) {
		if (am_server)
			f = stderr;
		else
			f = stdout;
	}

	if (!f) exit_cleanup(RERR_MESSAGEIO);
	fflush(f);
}



/* a generic logging routine for send/recv, with parameter
 * substitiution */
static void log_formatted(enum logcode code,
			  char *format, char *op, struct file_struct *file,
			  struct stats *initial_stats)
{
	char buf[1024];
	char buf2[1024];
	char *p, *s, *n;
	size_t l;
	int64 b;

	/* We expand % codes one by one in place in buf.  We don't
	 * copy in the terminating nul of the inserted strings, but
	 * rather keep going until we reach the nul of the format.
	 * Just to make sure we don't clobber that nul and therefore
	 * accidentally keep going, we zero the buffer now. */
	memset(buf, 0, sizeof buf);
	strlcpy(buf, format, sizeof(buf));
	
	for (s = &buf[0]; s && (p = strchr(s,'%')); ) {
		n = NULL;
		s = p + 1;

		switch (p[1]) {
		case 'h': if (am_daemon) n = client_name(0); break;
		case 'a': if (am_daemon) n = client_addr(0); break;
		case 'l':
			snprintf(buf2,sizeof(buf2),"%.0f",
				 (double)file->length);
			n = buf2;
			break;
		case 'p':
			snprintf(buf2,sizeof(buf2),"%d",
				 (int)getpid());
			n = buf2;
			break;
		case 'o': n = op; break;
		case 'f':
			pathjoin(buf2, sizeof buf2,
				 file->basedir ? file->basedir : "",
				 f_name(file));
			clean_fname(buf2);
			n = buf2;
			if (*n == '/') n++;
			break;
		case 'm': n = lp_name(module_id); break;
		case 't': n = timestring(time(NULL)); break;
		case 'P': n = lp_path(module_id); break;
		case 'u': n = auth_user; break;
		case 'b':
			if (am_sender) {
				b = stats.total_written -
					initial_stats->total_written;
			} else {
				b = stats.total_read -
					initial_stats->total_read;
			}
			snprintf(buf2,sizeof(buf2),"%.0f", (double)b);
			n = buf2;
			break;
		case 'c':
			if (!am_sender) {
				b = stats.total_written -
					initial_stats->total_written;
			} else {
				b = stats.total_read -
					initial_stats->total_read;
			}
			snprintf(buf2,sizeof(buf2),"%.0f", (double)b);
			n = buf2;
			break;
		}

		/* n is the string to be inserted in place of this %
		 * code; l is its length not including the trailing
		 * NUL */
		if (!n)
			continue;

		l = strlen(n);

		if (l + ((int)(s - &buf[0])) >= sizeof(buf)) {
			rprintf(FERROR,"buffer overflow expanding %%%c - exiting\n",
				p[0]);
			exit_cleanup(RERR_MESSAGEIO);
		}

		/* Shuffle the rest of the string along to make space for n */
		if (l != 2) {
			memmove(s+(l-1), s+1, strlen(s+1)+1);
		}

		/* Copy in n but NOT its nul, because the format sting
		 * probably continues after this. */
		memcpy(p, n, l);

		/* Skip over inserted string; continue looking */
		s = p+l;
	}

	rprintf(code,"%s\n", buf);
}

/* log the outgoing transfer of a file */
void log_send(struct file_struct *file, struct stats *initial_stats)
{
	if (lp_transfer_logging(module_id)) {
		log_formatted(FLOG, lp_log_format(module_id), "send", file, initial_stats);
	} else if (log_format && !am_server) {
		log_formatted(FINFO, log_format, "send", file, initial_stats);
	}
}

/* log the incoming transfer of a file */
void log_recv(struct file_struct *file, struct stats *initial_stats)
{
	if (lp_transfer_logging(module_id)) {
		log_formatted(FLOG, lp_log_format(module_id), "recv", file, initial_stats);
	} else if (log_format && !am_server) {
		log_formatted(FINFO, log_format, "recv", file, initial_stats);
	}
}




/*
 * Called when the transfer is interrupted for some reason.
 *
 * Code is one of the RERR_* codes from errcode.h, or terminating
 * successfully.
 */
void log_exit(int code, const char *file, int line)
{
	if (code == 0) {
		rprintf(FLOG,"wrote %.0f bytes  read %.0f bytes  total size %.0f\n",
			(double)stats.total_written,
			(double)stats.total_read,
			(double)stats.total_size);
	} else {
		const char *name;

		name = rerr_name(code);
		if (!name)
			name = "unexplained error";

		/* VANISHED is not an error, only a warning */
		if (code == RERR_VANISHED) {
			rprintf(FINFO, "rsync warning: %s (code %d) at %s(%d)\n", 
				name, code, file, line);
		} else {
			rprintf(FERROR, "rsync error: %s (code %d) at %s(%d)\n",
				name, code, file, line);
		}
	}
}
