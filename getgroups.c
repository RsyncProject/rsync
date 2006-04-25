/*
 * Print out the gids of all groups for the current user.  This is like
 * `id -G` on Linux, but it's too hard to find a portable equivalent.
 *
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003, 2004 Wayne Davison
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

int
main(UNUSED(int argc), UNUSED(char *argv[]))
{
	int n, i;
	gid_t *list;
	gid_t gid = MY_GID();
	int gid_in_list = 0;

#ifdef HAVE_GETGROUPS
	if ((n = getgroups(0, NULL)) < 0) {
		perror("getgroups");
		return 1;
	}
#else
	n = 0;
#endif

	list = (gid_t*)malloc(sizeof (gid_t) * (n + 1));
	if (!list) {
		fprintf(stderr, "out of memory!\n");
		exit(1);
	}

#ifdef HAVE_GETGROUPS
	if (n > 0)
		n = getgroups(n, list);
#endif

	for (i = 0; i < n; i++)  {
		printf("%lu ", (unsigned long)list[i]);
		if (list[i] == gid)
			gid_in_list = 1;
	}
	/* The default gid might not be in the list on some systems. */
	if (!gid_in_list)
		printf("%lu", (unsigned long)gid);
	printf("\n");

	return 0;
}
