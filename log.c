/* 
   Copyright (C) Andrew Tridgell 1998
   
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
  logging and utility functions

  tridge, May 1998
  */
#include "rsync.h"

static FILE *logfile;


static void logit(int priority, char *buf)
{
	if (logfile) {
		fprintf(logfile,"%s [%d] %s", 
			timestring(time(NULL)), (int)getpid(), buf);
		fflush(logfile);
	} else {
		syslog(priority, "%s", buf);
	}
}

void log_open(void)
{
	static int initialised;
	int options = LOG_PID;
	time_t t;
	char *logf;

	if (initialised) return;
	initialised = 1;

	/* this looks pointless, but it is needed in order for the
	   C library on some systems to fetch the timezone info
	   before the chroot */
	t = time(NULL);
	localtime(&t);

	/* optionally use a log file instead of syslog */
	logf = lp_log_file();
	if (logf && *logf) {
		extern int orig_umask;
		int old_umask = umask(022 | orig_umask);
		logfile = fopen(logf, "a");
		umask(old_umask);
		return;
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
		

/* this is the rsync debugging function. Call it with FINFO, FERROR or FLOG */
 void rprintf(int fd, const char *format, ...)
{
	va_list ap;  
	char buf[1024];
	int len;
	FILE *f=NULL;
	extern int am_daemon;
	extern int quiet;
	/* recursion can happen with certain fatal conditions */

	if (quiet != 0 && fd == FINFO) return;

	va_start(ap, format);
	len = vslprintf(buf, sizeof(buf), format, ap);
	va_end(ap);

	if (len < 0) exit_cleanup(RERR_MESSAGEIO);

	if (len > sizeof(buf)-1) exit_cleanup(RERR_MESSAGEIO);

	buf[len] = 0;

	if (fd == FLOG) {
		if (am_daemon) logit(LOG_INFO, buf);
		return;
	}

	if (am_daemon) {
		static int depth;
		int priority = LOG_INFO;
		if (fd == FERROR) priority = LOG_WARNING;

		if (depth) return;

		depth++;

		log_open();
		if (!io_multiplex_write(fd, buf, strlen(buf))) {
			logit(priority, buf);
		}

		depth--;
		return;
	}

	if (fd == FERROR) {
		f = stderr;
	} 

	if (fd == FINFO) {
		extern int am_server;
		if (am_server) 
			f = stderr;
		else
			f = stdout;
	} 

	if (!f) exit_cleanup(RERR_MESSAGEIO);

	if (fwrite(buf, len, 1, f) != 1) exit_cleanup(RERR_MESSAGEIO);

	if (buf[len-1] == '\r' || buf[len-1] == '\n') fflush(f);
}

void rflush(int fd)
{
	FILE *f = NULL;
	extern int am_daemon;
	
	if (am_daemon) {
		return;
	}

	if (fd == FLOG) {
		return;
	} 

	if (fd == FERROR) {
		f = stderr;
	} 

	if (fd == FINFO) {
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
static void log_formatted(int fd,
			  char *format, char *op, struct file_struct *file,
			  struct stats *initial_stats)
{
	extern int module_id;
	extern char *auth_user;
	char buf[1024];
	char buf2[1024];
	char *p, *s, *n;
	int l;
	extern struct stats stats;		
	extern int am_sender;
	extern int am_daemon;
	int64 b;

	strlcpy(buf, format, sizeof(buf));
	
	for (s=&buf[0]; 
	     s && (p=strchr(s,'%')); ) {
		n = NULL;
		s = p + 1;

		switch (p[1]) {
		case 'h': if (am_daemon) n = client_name(0); break;
		case 'a': if (am_daemon) n = client_addr(0); break;
		case 'l': 
			slprintf(buf2,sizeof(buf2),"%.0f", 
				 (double)file->length); 
			n = buf2;
			break;
		case 'p': 
			slprintf(buf2,sizeof(buf2),"%d", 
				 (int)getpid()); 
			n = buf2;
			break;
		case 'o': n = op; break;
		case 'f': 
			slprintf(buf2, sizeof(buf2), "%s/%s", 
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
			slprintf(buf2,sizeof(buf2),"%.0f", (double)b); 
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
			slprintf(buf2,sizeof(buf2),"%.0f", (double)b); 
			n = buf2;
			break;
		}

		if (!n) continue;

		l = strlen(n);

		if ((l-1) + ((int)(s - &buf[0])) > sizeof(buf)) {
			rprintf(FERROR,"buffer overflow expanding %%%c - exiting\n",
				p[0]);
			exit_cleanup(RERR_MESSAGEIO);
		}

		if (l != 2) {
			memmove(s+(l-1), s+1, strlen(s+1)+1);
		}
		memcpy(p, n, l);

		s = p+l;
	}

	rprintf(fd,"%s\n", buf);
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

/* called when the transfer is interrupted for some reason */
void log_exit(int code, const char *file, int line)
{
	if (code == 0) {
		extern struct stats stats;		
		rprintf(FLOG,"wrote %.0f bytes  read %.0f bytes  total size %.0f\n",
			(double)stats.total_written,
			(double)stats.total_read,
			(double)stats.total_size);
	} else {
		rprintf(FLOG,"transfer interrupted (code %d) at %s(%d)\n", 
			code, file, line);
	}
}

/* log the incoming transfer of a file for interactive use, this
   will be called at the end where the client was run 
   
   it i called when a file starts to be transferred
*/
void log_transfer(struct file_struct *file, const char *fname)
{
	extern int verbose;

	if (!verbose) return;

	rprintf(FINFO,"%s\n", fname);
}

