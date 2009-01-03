/*
 * Test harness for unsafe_symlink().  Not linked into rsync itself.
 *
 * Copyright (C) 2002 Martin Pool
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

/* Prints either "safe" or "unsafe" depending on the two arguments.
 * Always returns 0 unless something extraordinary happens. */

#include "rsync.h"

int dry_run = 0;
int am_root = 0;
int read_only = 0;
int list_only = 0;
int verbose = 0;
int preserve_perms = 0;
int preserve_executability = 0;

int
main(int argc, char **argv)
{
	if (argc != 3) {
		fprintf(stderr, "usage: t_unsafe LINKDEST SRCDIR\n");
		return 1;
	}

	printf("%s\n",
	       unsafe_symlink(argv[1], argv[2]) ? "unsafe" : "safe");

	return 0;
}
