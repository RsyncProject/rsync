/*
 * Copyright (C) 2002 by Martin Pool
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
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

#include "rsync.h"

/* These are to make syscall.o shut up. */
int dry_run = 0;
int read_only = 1;
int list_only = 0;
int preserve_perms = 0;

/**
 * @file trimslash.c
 *
 * Test harness; not linked into release.
 **/
int main(int argc, char **argv)
{
	int i;
	
	if (argc <= 1) {
		fprintf(stderr, "trimslash: needs at least one argument\n");
		return 1;
	}

	for (i = 1; i < argc; i++) {
		trim_trailing_slashes(argv[i]);	/* modify in place */
		printf("%s\n", argv[i]);
	}
	return 0;
}
