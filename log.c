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

static int log_initialised;
static char *logfname;
static FILE *logfile;
static int log_error_fd = -1;
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

struct err_list {
	struct err_list *next;
	char *buf;
	int len;
	int written; /* how many bytes we have written so far */
};

static struct err_list *err_list_head;
static struct err_list *err_list_tail;

/* add an error message to the pending error list */
static void err_list_add(int code, char *buf, int len)
{
	struct err_list *el;
	el = new(struct err_list);
	if (!el) exit_cleanup(RERR_MALLOC);
	el->next = NULL;
	el->buf = new_array(char, len+4);
	if (!el->buf) exit_cleanup(RERR_MALLOC);
	memcpy(el->buf+4, buf, len);
	SIVAL(el->buf, 0, ((code+MPLEX_BASE)<<24) | len);
	el->len = len+4;
	el->written = 0;
	if (err_list_tail) {
		err_list_tail->next = el;
	} else {
		err_list_head = el;
	}
	err_list_tail = el;
}


/* try to push errors off the error list onto the wire */
void err_list_push(void)
{
	if (log_error_fd == -1) return;

	while (err_list_head) {
		struct err_list *el = err_list_head;
		int n = write(log_error_fd, el->buf+el->written, el->len - el->written);
		/* don't check for an error if the best way of handling the error is
		   to ignore it */
		if (n == -1) break;
		if (n > 0) {
			el->written += n;
		}
		if (el->written == el->len) {
			free(el->buf);
			err_list_head = el->next;
			if (!err_list_head) err_list_tail = NULL;
			free(el);
		}
	}
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
	   C library on some systems to fetch the timezone info
	   before the chroot */
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

/* setup the error file descriptor - used when we are a server
   that is receiving files */
void set_error_fd(int fd)
{
	log_error_fd = fd;
	set_nonblocking(log_error_fd);
}

/* this is the underlying (unformatted) rsync debugging function. Call
   it with FINFO, FERROR or FLOG */
void rwrite(enum logcode code, char *buf, int len)
{
	FILE *f=NULL;
	extern int am_daemon;
	extern int am_server;
	extern int quiet;
	/* recursion can happen with certain fatal conditions */

	if (quiet && code == FINFO) return;

	if (len < 0) exit_cleanup(RERR_MESSAGEIO);

	buf[len] = 0;

	if (code == FLOG) {
		if (am_daemon) logit(LOG_INFO, buf);
		return;
	}

	/* first try to pass it off to our sibling */
	if (am_server && log_error_fd != -1) {
		err_list_add(code, buf, len);
		err_list_push();
		return;
	}

	/* next, if we are a server but not in daemon mode, and multiplexing
	 *  is enabled, pass it to the other side.  */
	if (am_server && !am_daemon && io_multiplex_write(code, buf, len)) {
		return;
	}

	/* otherwise, if in daemon mode and either we are not a server
	 *  (that is, we are not running --daemon over a remote shell) or
	 *  the log has already been initialised, log the message on this
	 *  side because we don't want the client to see most errors for
	 *  security reasons.  We do want early messages when running daemon
	 *  mode over a remote shell to go to the remote side; those will
	 *  fall through to the next case. */
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
	extern int am_daemon;
	
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
		extern int am_server;
		if (am_server) 
			f = stderr;
		else
			f = stdout;
	} 

	if (!f) exit_cleanup(RERR_MESSAGEIO);
	fflush(f);
}



/* a generic logging routine for send/recv, with parameter
   substitiution */
static void log_formatted(enum logcode code,
			  char *format, char *op, struct file_struct *file,
			  struct stats *initial_stats)
{
	extern int module_id;
	extern char *auth_user;
	char buf[1024];
	char buf2[1024];
	char *p, *s, *n;
	size_t l;
	extern struct stats stats;		
	extern int am_sender;
	extern int am_daemon;
	int64 b;

	/* We expand % codes one by one in place in buf.  We don't
	 * copy in the terminating nul of the inserted strings, but
	 * rather keep going until we reach the nul of the format.
	 * Just to make sure we don't clobber that nul and therefore
	 * accidentally keep going, we zero the buffer now. */
	memset(buf, 0, sizeof buf);
	strlcpy(buf, format, sizeof(buf));
	
	for (s=&buf[0]; 
	     s && (p=strchr(s,'%')); ) {
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
			snprintf(buf2, sizeof(buf2), "%s/%s", 
				 file->basedir?file->basedir:"", 
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
	extern int module_id;
	extern int am_server;
	extern char *log_format;

	if (lp_transfer_logging(module_id)) {
		log_formatted(FLOG, lp_log_format(module_id), "send", file, initial_stats);
	} else if (log_format && !am_server) {
		log_formatted(FINFO, log_format, "send", file, initial_stats);
	}
}

/* log the incoming transfer of a file */
void log_recv(struct file_struct *file, struct stats *initial_stats)
{
	extern int module_id;
	extern int am_server;
	extern char *log_format;

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
		extern struct stats stats;		
		rprintf(FLOG,"wrote %.0f bytes  read %.0f bytes  total size %.0f\n",
			(double)stats.total_written,
			(double)stats.total_read,
			(double)stats.total_size);
	} else {
                const char *name;

                name = rerr_name(code);
                if (!name)
                        name = "unexplained error";
                
		rprintf(FERROR,"rsync error: %s (code %d) at %s(%d)\n", 
			name, code, file, line);
	}
}

/*
 * Log the incoming transfer of a file for interactive use,
 * this will be called at the end where the client was run.
 * Called when a file starts to be transferred.
 */
void log_transfer(struct file_struct *file, const char *fname)
{
	extern int verbose;

	if (!verbose) return;

	rprintf(FINFO, "%s\n", fname);
}
