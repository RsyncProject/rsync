/* -*- c-file-style: "linux" -*-
 *
 * Copyright (C) 2001 by Martin Pool
 * 
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License version
 * 2 as published by the Free Software Foundation.
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

/**
 * \section tls
 *
 * tls -- Trivial recursive ls, for comparing two directories after
 * running an rsync.
 *
 * The problem with using the system's own ls is that some features
 * have little quirks that make directories look different when for
 * our purposes they're the same -- for example, the BSD braindamage
 * about setting the mode on symlinks based on your current umask.
 *
 * There are some restrictions compared to regular ls: all the names
 * on the command line must be directories rather than files; you
 * can't give wildcards either.
 *
 * At the moment we don't sort the output, but all the files have full
 * names, so you can run it through sort(1).
 *
 * We need to recurse downwards and show all the interesting
 * information and no more.
 *
 * \todo Use readdir64 if available?
 */



#include "rsync.h"

#define PROGRAM "tls"

/* These are to make syscall.o shut up. */
int dry_run = 0;
int read_only = 1;
int list_only = 0;


static void failed (char const *what,
		    char const *where)
{
	fprintf (stderr, PROGRAM ": %s %s: %s\n",
		 what, where, strerror (errno));
	exit (1);
}



static void list_dir (char const *dn)
{
	DIR *d;
	struct dirent *de;

	if (!(d = opendir (dn)))
		failed ("opendir", dn);

	while ((de = readdir (d))) {
		char *dname = d_name (de);
		if (!strcmp (dname, ".")  ||  !strcmp (dname, ".."))
			continue;
		printf ("%s\n", dname);
	}
	
	if (closedir (d) == -1)
		failed ("closedir", dn);
}


int main (int argc, char *argv[])
{
	if (argc < 2) {
		fprintf (stderr, "usage: " PROGRAM " DIR ...\n"
			 "Trivial file listing program for portably checking rsync\n");
		return 1;
	}

	for (argv++; *argv; argv++) {
		list_dir (*argv);
	}

	return 0;
}
