/*
 * Logging and utility functions.
 *
 * Copyright (C) 1998-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2000-2001 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003, 2004, 2005, 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#include "rsync.h"
#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
#include <iconv.h>
#endif

extern int verbose;
extern int dry_run;
extern int am_daemon;
extern int am_server;
extern int am_sender;
extern int local_server;
extern int quiet;
extern int module_id;
extern int msg_fd_out;
extern int allow_8bit_chars;
extern int protocol_version;
extern int preserve_times;
extern int stdout_format_has_i;
extern int stdout_format_has_o_or_i;
extern int logfile_format_has_i;
extern int logfile_format_has_o_or_i;
extern mode_t orig_umask;
extern char *auth_user;
extern char *stdout_format;
extern char *logfile_format;
extern char *logfile_name;
#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
extern iconv_t ic_chck;
#endif
extern char curr_dir[];
extern unsigned int module_dirlen;

static int log_initialised;
static int logfile_was_closed;
static FILE *logfile_fp;
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
	{ RERR_CRASHED    , "sibling process crashed" },
	{ RERR_TERMINATED , "sibling process terminated abnormally" },
	{ RERR_SIGNAL1    , "received SIGUSR1" },
	{ RERR_SIGNAL     , "received SIGINT, SIGTERM, or SIGHUP" },
	{ RERR_WAITCHILD  , "waitpid() failed" },
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
	if (logfile_fp) {
		fprintf(logfile_fp, "%s [%d] %s",
			timestring(time(NULL)), (int)getpid(), buf);
		fflush(logfile_fp);
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
	openlog("rsyncd", options, lp_syslog_facility(module_id));
#else
	openlog("rsyncd", options);
#endif

#ifndef LOG_NDELAY
	logit(LOG_INFO, "rsyncd started\n");
#endif
}

static void logfile_open(void)
{
	mode_t old_umask = umask(022 | orig_umask);
	logfile_fp = fopen(logfile_name, "a");
	umask(old_umask);
	if (!logfile_fp) {
		int fopen_errno = errno;
		/* Rsync falls back to using syslog on failure. */
		syslog_init();
		rsyserr(FERROR, fopen_errno,
			"failed to open log-file %s", logfile_name);
		rprintf(FINFO, "Ignoring \"log file\" setting.\n");
	}
}

void log_init(int restart)
{
	if (log_initialised) {
		if (!restart)
			return;
		if (strcmp(logfile_name, lp_log_file(module_id)) != 0) {
			if (logfile_fp) {
				fclose(logfile_fp);
				logfile_fp = NULL;
			} else
				closelog();
			logfile_name = NULL;
		} else if (*logfile_name)
			return; /* unchanged, non-empty "log file" names */
		else if (lp_syslog_facility(-1) != lp_syslog_facility(module_id))
			closelog();
		else
			return; /* unchanged syslog settings */
	} else
		log_initialised = 1;

	/* This looks pointless, but it is needed in order for the
	 * C library on some systems to fetch the timezone info
	 * before the chroot. */
	timestring(time(NULL));

	/* Optionally use a log file instead of syslog.  (Non-daemon
	 * rsyncs will have already set logfile_name, as needed.) */
	if (am_daemon && !logfile_name)
		logfile_name = lp_log_file(module_id);
	if (logfile_name && *logfile_name)
		logfile_open();
	else
		syslog_init();
}

void logfile_close(void)
{
	if (logfile_fp) {
		logfile_was_closed = 1;
		fclose(logfile_fp);
		logfile_fp = NULL;
	}
}

void logfile_reopen(void)
{
	if (logfile_was_closed) {
		logfile_was_closed = 0;
		logfile_open();
	}
}

static void filtered_fwrite(FILE *f, const char *buf, int len, int use_isprint)
{
	const char *s, *end = buf + len;
	for (s = buf; s < end; s++) {
		if ((s < end - 4
		  && *s == '\\' && s[1] == '#'
		  && isdigit(*(uchar*)(s+2))
		  && isdigit(*(uchar*)(s+3))
		  && isdigit(*(uchar*)(s+4)))
		 || (*s != '\t'
		  && ((use_isprint && !isprint(*(uchar*)s))
		   || *(uchar*)s < ' '))) {
			if (s != buf && fwrite(buf, s - buf, 1, f) != 1)
				exit_cleanup(RERR_MESSAGEIO);
			fprintf(f, "\\#%03o", *(uchar*)s);
			buf = s + 1;
		}
	}
	if (buf != end && fwrite(buf, end - buf, 1, f) != 1)
		exit_cleanup(RERR_MESSAGEIO);
}

/* this is the underlying (unformatted) rsync debugging function. Call
 * it with FINFO, FERROR or FLOG.  Note: recursion can happen with
 * certain fatal conditions. */
void rwrite(enum logcode code, char *buf, int len)
{
	int trailing_CR_or_NL;
	FILE *f = NULL;

	if (len < 0)
		exit_cleanup(RERR_MESSAGEIO);

	if (am_server && msg_fd_out >= 0) {
		/* Pass the message to our sibling. */
		send_msg((enum msgcode)code, buf, len);
		return;
	}

	if (code == FSOCKERR) /* This gets simplified for a non-sibling. */
		code = FERROR;

	if (code == FCLIENT)
		code = FINFO;
	else if (am_daemon || logfile_name) {
		static int in_block;
		char msg[2048];
		int priority = code == FERROR ? LOG_WARNING : LOG_INFO;

		if (in_block)
			return;
		in_block = 1;
		if (!log_initialised)
			log_init(0);
		strlcpy(msg, buf, MIN((int)sizeof msg, len + 1));
		logit(priority, msg);
		in_block = 0;

		if (code == FLOG || (am_daemon && !am_server))
			return;
	} else if (code == FLOG)
		return;

	if (quiet && code != FERROR)
		return;

	if (am_server) {
		/* Pass the message to the non-server side. */
		if (send_msg((enum msgcode)code, buf, len))
			return;
		if (am_daemon) {
			/* TODO: can we send the error to the user somehow? */
			return;
		}
	}

	switch (code) {
	case FERROR:
		log_got_error = 1;
		f = stderr;
		break;
	case FINFO:
		f = am_server ? stderr : stdout;
		break;
	default:
		exit_cleanup(RERR_MESSAGEIO);
	}

	trailing_CR_or_NL = len && (buf[len-1] == '\n' || buf[len-1] == '\r')
			  ? buf[--len] : 0;

#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
	if (ic_chck != (iconv_t)-1) {
		char convbuf[1024];
		char *in_buf = buf, *out_buf = convbuf;
		size_t in_cnt = len, out_cnt = sizeof convbuf - 1;

		iconv(ic_chck, NULL, 0, NULL, 0);
		while (iconv(ic_chck, &in_buf,&in_cnt,
				 &out_buf,&out_cnt) == (size_t)-1) {
			if (out_buf != convbuf) {
				filtered_fwrite(f, convbuf, out_buf - convbuf, 0);
				out_buf = convbuf;
				out_cnt = sizeof convbuf - 1;
			}
			if (errno == E2BIG)
				continue;
			fprintf(f, "\\#%03o", *(uchar*)in_buf++);
			in_cnt--;
		}
		if (out_buf != convbuf)
			filtered_fwrite(f, convbuf, out_buf - convbuf, 0);
	} else
#endif
		filtered_fwrite(f, buf, len, !allow_8bit_chars);

	if (trailing_CR_or_NL) {
		fputc(trailing_CR_or_NL, f);
		fflush(f);
	}
}

/* This is the rsync debugging function. Call it with FINFO, FERROR or
 * FLOG. */
void rprintf(enum logcode code, const char *format, ...)
{
	va_list ap;
	char buf[BIGPATHBUFLEN];
	size_t len;

	va_start(ap, format);
	len = vsnprintf(buf, sizeof buf, format, ap);
	va_end(ap);

	/* Deal with buffer overruns.  Instead of panicking, just
	 * truncate the resulting string.  (Note that configure ensures
	 * that we have a vsnprintf() that doesn't ever return -1.) */
	if (len > sizeof buf - 1) {
		static const char ellipsis[] = "[...]";

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
		memcpy(buf+len-sizeof ellipsis, ellipsis, sizeof ellipsis);
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
	char buf[BIGPATHBUFLEN];
	size_t len;

	strlcpy(buf, RSYNC_NAME ": ", sizeof buf);
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

	if (am_daemon || code == FLOG)
		return;

	if (code == FERROR || am_server)
		f = stderr;
	else
		f = stdout;

	fflush(f);
}

/* A generic logging routine for send/recv, with parameter substitiution. */
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
		case 'h':
			if (am_daemon)
				n = client_name(0);
			break;
		case 'a':
			if (am_daemon)
				n = client_addr(0);
			break;
		case 'l':
			strlcat(fmt, ".0f", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt,
				 (double)file->length);
			n = buf2;
			break;
		case 'U':
			strlcat(fmt, "ld", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt,
				 (long)file->uid);
			n = buf2;
			break;
		case 'G':
			if (file->gid == GID_NONE)
				n = "DEFAULT";
			else {
				strlcat(fmt, "ld", sizeof fmt);
				snprintf(buf2, sizeof buf2, fmt,
					 (long)file->gid);
				n = buf2;
			}
			break;
		case 'p':
			strlcat(fmt, "ld", sizeof fmt);
			snprintf(buf2, sizeof buf2, fmt,
				 (long)getpid());
			n = buf2;
			break;
		case 'M':
			n = timestring(file->modtime);
			{
				char *cp = n;
				while ((cp = strchr(cp, ' ')) != NULL)
					*cp = '-';
			}
			break;
		case 'B':
			n = buf2 + MAXPATHLEN - PERMSTRING_SIZE;
			permstring(n - 1, file->mode); /* skip the type char */
			break;
		case 'o':
			n = op;
			break;
		case 'f':
			n = f_name(file, NULL);
			if (am_sender && file->dir.root) {
				pathjoin(buf2, sizeof buf2,
					 file->dir.root, n);
				clean_fname(buf2, 0);
				if (fmt[1])
					strlcpy(n, buf2, MAXPATHLEN);
				else
					n = buf2;
			} else if (*n != '/') {
				pathjoin(buf2, sizeof buf2,
					 curr_dir + module_dirlen, n);
				clean_fname(buf2, 0);
				if (fmt[1])
					strlcpy(n, buf2, MAXPATHLEN);
				else
					n = buf2;
			} else
				clean_fname(n, 0);
			if (*n == '/')
				n++;
			break;
		case 'n':
			n = f_name(file, NULL);
			if (S_ISDIR(file->mode))
				strlcat(n, "/", MAXPATHLEN);
			break;
		case 'L':
			if (hlink && *hlink) {
				n = hlink;
				strlcpy(buf2, " => ", sizeof buf2);
			} else if (S_ISLNK(file->mode) && file->u.link) {
				n = file->u.link;
				strlcpy(buf2, " -> ", sizeof buf2);
			} else {
				n = "";
				if (!fmt[1])
					break;
				strlcpy(buf2, "    ", sizeof buf2);
			}
			strlcat(fmt, "s", sizeof fmt);
			snprintf(buf2 + 4, sizeof buf2 - 4, fmt, n);
			n = buf2;
			break;
		case 'm':
			n = lp_name(module_id);
			break;
		case 't':
			n = timestring(time(NULL));
			break;
		case 'P':
			n = lp_path(module_id);
			break;
		case 'u':
			n = auth_user;
			break;
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
			     : IS_SPECIAL(file->mode) ? 'S'
			     : IS_DEVICE(file->mode) ? 'D'
			     : S_ISLNK(file->mode) ? 'L' : 'f';
			n[2] = !(iflags & ITEM_REPORT_CHECKSUM) ? '.' : 'c';
			n[3] = !(iflags & ITEM_REPORT_SIZE) ? '.' : 's';
			n[4] = !(iflags & ITEM_REPORT_TIME) ? '.'
			     : !preserve_times || S_ISLNK(file->mode) ? 'T' : 't';
			n[5] = !(iflags & ITEM_REPORT_PERMS) ? '.' : 'p';
			n[6] = !(iflags & ITEM_REPORT_OWNER) ? '.' : 'o';
			n[7] = !(iflags & ITEM_REPORT_GROUP) ? '.' : 'g';
			n[8] = '.';
			n[9] = '\0';

			if (iflags & (ITEM_IS_NEW|ITEM_MISSING_DATA)) {
				char ch = iflags & ITEM_IS_NEW ? '+' : '?';
				int i;
				for (i = 2; n[i]; i++)
					n[i] = ch;
			} else if (n[0] == '.' || n[0] == 'h'
			        || (n[0] == 'c' && n[1] == 'f')) {
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

/* Log the transfer of a file.  If the code is FCLIENT, the output just goes
 * to stdout.  If it is FLOG, it just goes to the log file.  Otherwise we
 * output to both. */
void log_item(enum logcode code, struct file_struct *file,
	      struct stats *initial_stats, int iflags, char *hlink)
{
	char *s_or_r = am_sender ? "send" : "recv";

	if (code != FLOG && stdout_format && !am_server) {
		log_formatted(FCLIENT, stdout_format, s_or_r,
			      file, initial_stats, iflags, hlink);
	}
	if (code != FCLIENT && logfile_format && *logfile_format) {
		log_formatted(FLOG, logfile_format, s_or_r,
			      file, initial_stats, iflags, hlink);
	}
}

void maybe_log_item(struct file_struct *file, int iflags, int itemizing,
		    char *buf)
{
	int significant_flags = iflags & SIGNIFICANT_ITEM_FLAGS;
	int see_item = itemizing && (significant_flags || *buf
		|| stdout_format_has_i > 1 || (verbose > 1 && stdout_format_has_i));
	int local_change = iflags & ITEM_LOCAL_CHANGE && significant_flags;
	if (am_server) {
		if (logfile_name && !dry_run && see_item
		 && (significant_flags || logfile_format_has_i))
			log_item(FLOG, file, &stats, iflags, buf);
	} else if (see_item || local_change || *buf
	    || (S_ISDIR(file->mode) && significant_flags)) {
		enum logcode code = significant_flags || logfile_format_has_i ? FINFO : FCLIENT;
		log_item(code, file, &stats, iflags, buf);
	}
}

void log_delete(char *fname, int mode)
{
	static struct file_struct file;
	int len = strlen(fname);
	char *fmt;

	file.mode = mode;
	file.basename = fname;

	if (!verbose && !stdout_format)
		;
	else if (am_server && protocol_version >= 29 && len < MAXPATHLEN) {
		if (S_ISDIR(mode))
			len++; /* directories include trailing null */
		send_msg(MSG_DELETED, fname, len);
	} else {
		fmt = stdout_format_has_o_or_i ? stdout_format : "deleting %n";
		log_formatted(FCLIENT, fmt, "del.", &file, &stats,
			      ITEM_DELETED, NULL);
	}

	if (!logfile_name || dry_run || !logfile_format)
		return;

	fmt = logfile_format_has_o_or_i ? logfile_format : "deleting %n";
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
			rprintf(FINFO, "rsync warning: %s (code %d) at %s(%d) [%s=%s]\n",
				name, code, file, line, who_am_i(), RSYNC_VERSION);
		} else {
			rprintf(FERROR, "rsync error: %s (code %d) at %s(%d) [%s=%s]\n",
				name, code, file, line, who_am_i(), RSYNC_VERSION);
		}
	}
}
