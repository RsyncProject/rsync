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

/* this is the rsync debugging function. Call it with FINFO or FERROR */
void rprintf(int fd, const char *format, ...)
{
	va_list ap;  
	char buf[1024];
	int len;
	FILE *f=NULL;
	extern int am_daemon;

	va_start(ap, format);

#if HAVE_VSNPRINTF
	len = vsnprintf(buf, sizeof(buf)-1, format, ap);
#else
	len = vsprintf(buf, format, ap);
#endif
	va_end(ap);

	if (len < 0) exit_cleanup(1);

	if (len > sizeof(buf)-1) exit_cleanup(1);

	buf[len] = 0;

	if (am_daemon) {
		static int initialised;
		int priority = LOG_INFO;
		if (fd == FERROR) priority = LOG_WARNING;

		if (!initialised) {
			initialised = 1;
#ifdef LOG_DAEMON
			openlog("rsyncd", LOG_PID, lp_syslog_facility());
#else
			openlog("rsyncd", LOG_PID);
#endif
		}
		
		syslog(priority, "%s", buf);
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

	if (!f) exit_cleanup(1);

	if (fwrite(buf, len, 1, f) != 1) exit_cleanup(1);
}

void rflush(int fd)
{
	FILE *f = NULL;
	extern int am_daemon;
	
	if (am_daemon) {
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

	if (!f) exit_cleanup(1);
	fflush(f);
}

