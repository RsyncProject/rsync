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

extern int verbose;
extern int dry_run;
extern int am_daemon;
extern int am_server;
extern int am_sender;
extern int local_server;
extern int quiet;
extern int module_id;
extern int msg_fd_out;
extern int protocol_version;
extern int preserve_times;
extern int log_format_has_o_or_i;
extern int daemon_log_format_has_o_or_i;
extern char *auth_user;
extern char *log_format;

static int log_initialised;
static int logfile_was_closed;
static char *logfname;
static FILE *logfile;
struct stats stats;

int log_got_error = 0;

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
	{ RERR_VANISHED   , "some files vanished before they could be transferred" },
	{ RERR_TIMEOUT    , "timeout in data send/receive" },
	{ RERR_CMD_FAILED , "remote shell failed" },
	{ RERR_CMD_KILLED , "remote shell killed" },
	{ RERR_CMD_RUN    , "remote command could not be run" },
	{ RERR_CMD_NOTFOUND,"remote command not found" },
	{ RERR_DEL_LIMIT  , "the --max-delete limit stopped deletions" },
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
	if (logfile_was_closed)
		logfile_reopen();
	if (logfile) {
		fprintf(logfile,"%s [%d] %s",
			timestring(time(NULL)), (int)getpid(), buf);
		fflush(logfile);
	} else {
		syslog(priority, "%s", buf);
	}
}

static void syslog_init()
{
	static int been_here = 0;
	int options = LOG_PID;

	if (been_here)
		return;
	been_here = 1;

#ifdef LOG_NDELAY
	options |= LOG_NDELAY;
#endif

#ifdef LOG_DAEMON
	openlog("rsyncd", options, lp_syslog_facility());
#else
	openlog("rsyncd", options);
#endif

#ifndef LOG_NDELAY
	logit(LOG_INFO, "rsyncd started\n");
#endif
}

static void logfile_open(void)
{
	extern int orig_umask;
	int old_umask = umask(022 | orig_umask);
	logfile = fopen(logfname, "a");
	umask(old_umask);
	if (!logfile) {
		int fopen_errno = errno;
		/* Rsync falls back to using syslog on failure. */
		syslog_init();
		rsyserr(FERROR, fopen_errno,
			"failed to open log-file %s", logfname);
		rprintf(FINFO, "Ignoring \"log file\" setting.\n");
	}
}

void log_init(void)
{
	time_t t;

	if (log_initialised)
		return;
	log_initialised = 1;

	/* this looks pointless, but it is needed in order for the
	 * C library on some systems to fetch the timezone info
	 * before the chroot */
	t = time(NULL);
	localtime(&t);

	/* optionally use a log file instead of syslog */
	logfname = lp_log_file();
	if (logfname && *logfname)
		logfile_open();
	else
		syslog_init();
}

void logfile_close(void)
{
	if (logfile) {
		logfile_was_closed = 1;
		fclose(logfile);
		logfile = NULL;
	}
}

void logfile_reopen(void)
{
	if (logfile_was_closed) {
		logfile_was_closed = 0;
		logfile_open();
	}
}

/* this is the underlying (unformatted) rsync debugging function. Call
 * it with FINFO, FERROR or FLOG */
void rwrite(enum logcode code, char *buf, int len)
{
	FILE *f = NULL;
	/* recursion can happen with certain fatal conditions */

	if (quiet && code == FINFO)
		return;

	if (len < 0)
		exit_cleanup(RERR_MESSAGEIO);

	buf[len] = 0;

	if (am_server && msg_fd_out >= 0) {
		/* Pass the message to our sibling. */
		send_msg((enum msgcode)code, buf, len);
		return;
	}

	if (code == FCLIENT)
		code = FINFO;
	else if (am_daemon) {
		static int in_block;
		char msg[2048];
		int priority = code == FERROR ? LOG_WARNING : LOG_INFO;

		if (in_block)
			return;
		in_block = 1;
		if (!log_initialised)
			log_init();
		strlcpy(msg, buf, MIN((int)sizeof msg, len + 1));
		logit(priority, msg);
		in_block = 0;

		if (code == FLOG || !am_server)
			return;
	} else if (code == FLOG)
		return;

	if (am_server) {
		/* Pass the message to the non-server side. */
		if (io_multiplex_write((enum msgcode)code, buf, len))
			return;
		if (am_daemon) {
			/* TODO: can we send the error to the user somehow? */
			return;
		}
	}

	if (code == FERROR) {
		log_got_error = 1;
		f = stderr;
	}

	if (code == FINFO)
		f = am_server ? stderr : stdout;

	if (!f)
		exit_cleanup(RERR_MESSAGEIO);

	if (fwrite(buf, len, 1, f) != 1)
		exit_cleanup(RERR_MESSAGEIO);

	if (buf[len-1] == '\r' || buf[len-1] == '\n')
		fflush(f);
}


/* This is the rsync debugging function. Call it with FINFO, FERROR or
 * FLOG. */
void rprintf(enum logcode code, const char *format, ...)
{
	va_list ap;
	char buf[MAXPATHLEN+512];
	size_t len;

	va_start(ap, format);
	len = vsnprintf(buf, sizeof buf, format, ap);
	va_end(ap);

	/* Deal with buffer overruns.  Instead of panicking, just
	 * truncate the resulting string.  (Note that configure ensures
	 * that we have a vsnprintf() that doesn't ever return -1.) */
	if (len > sizeof buf - 1) {
		const char ellipsis[] = "[...]";

		/* Reset length, and zero-terminate the end of our buffer */
		len = sizeof buf - 1;
		buf[len] = '\0';

		/* Copy the ellipsis to the end of the string, but give
		 * us one extra character:
		 *
		 *                  v--- null byte at buf[sizeof buf - 1]
		 *        abcdefghij0
		 *     -> abcd[...]00  <-- now two null bytes at end
		 *
		 * If the input format string has a trailing newline,
		 * we copy it into that extra null; if it doesn't, well,
		 * all we lose is one byte.  */
		strncpy(buf+len-sizeof ellipsis, ellipsis, sizeof ellipsis);
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
	char buf[MAXPATHLEN+512];
	size_t len;

	strcpy(buf, RSYNC_NAME ": ");
	len = (sizeof RSYNC_NAME ": ") - 1;

	va_start(ap, format);
	len += vsnprintf(buf + len, sizeof buf - len, format, ap);
	va_end(ap);

	if (len < sizeof buf) {
		len += snprintf(buf + len, sizeof buf - len,
				": %s (%d)\n", strerror(errcode), errcode);
	}
	if (len >= sizeof buf)
		exit_cleanup(RERR_MESSAGEIO);

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
static void log_formatted(enum logcode code, char *format, char *op,
			  struct file_struct *file, struct stats *initial_stats,
			  int iflags, char *hlink)
{
	char buf[MAXPATHLEN+1024], buf2[MAXPATHLEN], fmt[32];
	char *p, *s, *n;
	size_t len, total;
	int64 b;

	*fmt = '%';

	/* We expand % codes one by one in place in buf.  We don't
	 * copy in the terminating null of the inserted strings, but
	 * rather keep going until we reach the null of the format. */
	total = strlcpy(buf, format, sizeof buf);
	if (total > MAXPATHLEN) {
		rprintf(FERROR, "log-format string is WAY too long!\n");
		exit_cleanup(RERR_MESSAGEIO);
	}
	buf[total++] = '\n';
	buf[total] = '\0';

	for (p = buf; (p = strchr(p, '%')) != NULL; ) {
		s = p++;
		n = fmt + 1;
		if (*p == '-')
			*n++ = *p++;
		while (isdigit(*(uchar*)p) && n - fmt < (int)(sizeof fmt) - 8)
			*n++ = *p++;
		if (!*p)
			break;
		*n = '\0';
		n = NULL;

		switch (*p) {
		case 'h': if (am_daemon) n = client_name(0); break;
		case 'a': if (am_daemon) n = client_addr(0); break;
		case 'l':
			strlcat(fmt, ".0f", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt,
				 (double)file->length);
			n = buf2;
			break;
		case 'p':
			strlcat(fmt, "ld", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt,
				 (long)getpid());
			n = buf2;
			break;
		case 'o': n = op; break;
		case 'f':
			n = safe_fname(f_name(file));
			if (am_sender && file->dir.root) {
				pathjoin(buf2, sizeof buf2,
					 file->dir.root, n);
				/* The buffer from safe_fname() has more
				 * room than MAXPATHLEN, so this is safe. */
				if (fmt[1])
					strcpy(n, buf2);
				else
					n = buf2;
			}
			clean_fname(n, 0);
			if (*n == '/')
				n++;
			break;
		case 'n':
			n = safe_fname(f_name(file));
			if (S_ISDIR(file->mode)) {
				/* The buffer from safe_fname() has more
				 * room than MAXPATHLEN, so this is safe. */
				strcat(n, "/");
			}
			break;
		case 'L':
			if (hlink && *hlink) {
				n = safe_fname(hlink);
				strcpy(buf2, " => ");
			} else if (S_ISLNK(file->mode) && file->u.link) {
				n = safe_fname(file->u.link);
				strcpy(buf2, " -> ");
			} else {
				n = "";
				if (!fmt[1])
					break;
				strcpy(buf2, "    ");
			}
			strlcat(fmt, "s", sizeof fmt);
			snprintf(buf2 + 4, sizeof buf2 - 4, fmt, n);
			n = buf2;
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
			strlcat(fmt, ".0f", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt, (double)b);
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
			strlcat(fmt, ".0f", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt, (double)b);
			n = buf2;
			break;
		case 'i':
			if (iflags & ITEM_DELETED) {
				n = "*deleting";
				break;
			}
			n = buf2 + MAXPATHLEN - 32;
			n[0] = iflags & ITEM_LOCAL_CHANGE
			      ? iflags & ITEM_XNAME_FOLLOWS ? 'h' : 'c'
			     : !(iflags & ITEM_TRANSFER) ? '.'
			     : !local_server && *op == 's' ? '<' : '>';
			n[1] = S_ISDIR(file->mode) ? 'd'
			     : IS_DEVICE(file->mode) ? 'D'
			     : S_ISLNK(file->mode) ? 'L' : 'f';
			n[2] = !(iflags & ITEM_REPORT_CHECKSUM) ? '.' : 'c';
			n[3] = !(iflags & ITEM_REPORT_SIZE) ? '.' : 's';
			n[4] = !(iflags & ITEM_REPORT_TIME) ? '.'
			     : !preserve_times || IS_DEVICE(file->mode)
					       || S_ISLNK(file->mode) ? 'T' : 't';
			n[5] = !(iflags & ITEM_REPORT_PERMS) ? '.' : 'p';
			n[6] = !(iflags & ITEM_REPORT_OWNER) ? '.' : 'o';
			n[7] = !(iflags & ITEM_REPORT_GROUP) ? '.' : 'g';
			n[8] = !(iflags & ITEM_REPORT_XATTRS) ? '.' : 'a';
			n[9] = '\0';

			if (iflags & (ITEM_IS_NEW|ITEM_MISSING_DATA)) {
				char ch = iflags & ITEM_IS_NEW ? '+' : '?';
				int i;
				for (i = 2; n[i]; i++)
					n[i] = ch;
			} else if (n[0] == '.' || n[0] == 'h') {
				int i;
				for (i = 2; n[i]; i++) {
					if (n[i] != '.')
						break;
				}
				if (!n[i]) {
					for (i = 2; n[i]; i++)
						n[i] = ' ';
				}
			}
			break;
		}

		/* "n" is the string to be inserted in place of this % code. */
		if (!n)
			continue;
		if (n != buf2 && fmt[1]) {
			strlcat(fmt, "s", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt, n);
			n = buf2;
		}
		len = strlen(n);

		/* Subtract the length of the escape from the string's size. */
		total -= p - s + 1;

		if (len + total >= (size_t)sizeof buf) {
			rprintf(FERROR,
				"buffer overflow expanding %%%c -- exiting\n",
				p[0]);
			exit_cleanup(RERR_MESSAGEIO);
		}

		/* Shuffle the rest of the string along to make space for n */
		if (len != (size_t)(p - s + 1))
			memmove(s + len, p + 1, total - (s - buf) + 1);
		total += len;

		/* Insert the contents of string "n", but NOT its null. */
		if (len)
			memcpy(s, n, len);

		/* Skip over inserted string; continue looking */
		p = s + len;
	}

	rwrite(code, buf, total);
}

/* Return 1 if the format escape is in the log-format string (e.g. look for
 * the 'b' in the "%9b" format escape). */
int log_format_has(const char *format, char esc)
{
	const char *p;

	if (!format)
		return 0;

	for (p = format; (p = strchr(p, '%')) != NULL; ) {
		if (*++p == '-')
			p++;
		while (isdigit(*(uchar*)p))
			p++;
		if (!*p)
			break;
		if (*p == esc)
			return 1;
	}
	return 0;
}

/* log the transfer of a file */
void log_item(struct file_struct *file, struct stats *initial_stats,
	      int iflags, char *hlink)
{
	char *s_or_r = am_sender ? "send" : "recv";

	if (lp_transfer_logging(module_id)) {
		log_formatted(FLOG, lp_log_format(module_id), s_or_r,
			      file, initial_stats, iflags, hlink);
	} else if (log_format && !am_server) {
		log_formatted(FINFO, log_format, s_or_r,
			      file, initial_stats, iflags, hlink);
	}
}

void maybe_log_item(struct file_struct *file, int iflags, int itemizing,
		    char *buf)
{
	int significant_flags = iflags & SIGNIFICANT_ITEM_FLAGS;
	int see_item = itemizing && (significant_flags || *buf || verbose > 1);
	int local_change = iflags & ITEM_LOCAL_CHANGE
	    && (!(iflags & ITEM_XNAME_FOLLOWS) || significant_flags);
	if (am_server) {
		if (am_daemon && !dry_run && see_item)
			log_item(file, &stats, iflags, buf);
	} else if (see_item || local_change || *buf
	    || (S_ISDIR(file->mode) && significant_flags))
		log_item(file, &stats, iflags, buf);
}

void log_delete(char *fname, int mode)
{
	static struct file_struct file;
	int len = strlen(fname);
	char *fmt;

	file.mode = mode;
	file.basename = fname;

	if (!verbose && !log_format)
		;
	else if (am_server && protocol_version >= 29 && len < MAXPATHLEN) {
		if (S_ISDIR(mode))
			len++; /* directories include trailing null */
		send_msg(MSG_DELETED, fname, len);
	} else {
		fmt = log_format_has_o_or_i ? log_format : "deleting %n";
		log_formatted(FCLIENT, fmt, "del.", &file, &stats,
			      ITEM_DELETED, NULL);
	}

	if (!am_daemon || dry_run || !lp_transfer_logging(module_id))
		return;

	fmt = daemon_log_format_has_o_or_i ? lp_log_format(module_id) : "deleting %n";
	log_formatted(FLOG, fmt, "del.", &file, &stats, ITEM_DELETED, NULL);
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
		rprintf(FLOG,"sent %.0f bytes  received %.0f bytes  total size %.0f\n",
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
