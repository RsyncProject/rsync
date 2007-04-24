/*
 * Test harness for unsafe_symlink().  Not linked into rsync itself.
 *
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003-2007 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
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

/* Prints either "safe" or "unsafe" depending on the two arguments.
 * Always returns 0 unless something extraordinary happens. */

#include "rsync.h"

int dry_run = 0;
int am_root = 0;
int read_only = 0;
int list_only = 0;
int verbose = 0;
int preserve_perms = 0;

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
