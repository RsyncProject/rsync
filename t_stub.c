/*  -*- c-file-style: "linux" -*-
 * 
 * Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
 *
 * This program is free software; you can redistribute it and/or modify
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

#include "rsync.h"

/**
 * @file t_stub.c
 *
 * This file contains really simple implementations for rsync global
 * functions, so that module test harnesses can run standalone.
 **/

int modify_window = 0;
int module_id = -1;
struct exclude_list_struct server_exclude_list;

 void rprintf(UNUSED(enum logcode code), const char *format, ...)
{
	va_list ap;
	va_start(ap, format);
	vfprintf(stderr, format, ap);
	va_end(ap);
}

 void _exit_cleanup(int code, const char *file, int line)
{
	fprintf(stderr, "exit(%d): %s(%d)\n",
		code, file, line);
	exit(code);
}

 int check_exclude(UNUSED(struct exclude_list_struct *listp), UNUSED(char *name),
		   UNUSED(int name_is_dir))
{
	/* This function doesn't really get called in this test context, so
	 * just return 0. */
	return 0;
}

 char *lp_name(UNUSED(int mod))
{
    return NULL;
}

 BOOL lp_use_chroot(UNUSED(int mod))
{
    return 0;
}

 char *lp_path(UNUSED(int mod))
{
    return NULL;
}

