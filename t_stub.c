/*
 * This file contains really simple implementations for rsync global
 * functions, so that module test harnesses can run standalone.
 *
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2009 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"

int modify_window = 0;
int module_id = -1;
int relative_paths = 0;
int human_readable = 0;
int module_dirlen = 0;
int preserve_xattrs = 0;
mode_t orig_umask = 002;
char *partial_dir;
char *module_dir;
struct filter_list_struct daemon_filter_list;

 void rprintf(UNUSED(enum logcode code), const char *format, ...)
{
	va_list ap;
	va_start(ap, format);
	vfprintf(stderr, format, ap);
	va_end(ap);
}

 void rsyserr(UNUSED(enum logcode code), int errcode, const char *format, ...)
{
	va_list ap;
	fputs(RSYNC_NAME ": ", stderr);
	va_start(ap, format);
	vfprintf(stderr, format, ap);
	va_end(ap);
	fprintf(stderr, ": %s (%d)\n", strerror(errcode), errcode);
}

 void _exit_cleanup(int code, const char *file, int line)
{
	fprintf(stderr, "exit(%d): %s(%d)\n",
		code, file, line);
	exit(code);
}

 int check_filter(UNUSED(struct filter_list_struct *listp), UNUSED(enum logcode code),
		  UNUSED(const char *name), UNUSED(int name_is_dir))
{
	/* This function doesn't really get called in this test context, so
	 * just return 0. */
	return 0;
}

 int make_bak_dir(UNUSED(const char *fullpath))
{
	return -1;
}

 int copy_xattrs(UNUSED(const char *source), UNUSED(const char *dest))
{
	return -1;
}

 char *lp_name(UNUSED(int mod))
{
	return NULL;
}

 BOOL lp_use_chroot(UNUSED(int mod))
{
	return 0;
}

 const char *who_am_i(void)
{
	return "tester";
}
