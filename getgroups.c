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

/**
 * @file getgroups.c
 *
 * Print out the gids of all groups for the current user.  This is
 * like `id -G` on Linux, but it's too hard to find a portable
 * equivalent.
 **/

#include "rsync.h"

#ifndef NGROUPS
/* It ought to be defined, but just in case. */
#  define NGROUPS 32
#endif

int main(int argc, char *argv[])
{
	int n, i;
	gid_t list[NGROUPS];

	if ((n = getgroups(NGROUPS, list)) == -1) {
		perror("getgroups");
		return 1;
	}

	for (i = 0; i < n; i++) 
		printf("%u ", list[i]);
	printf("\n");
		
	return 0;
}
