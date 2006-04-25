/*
 * Support the max connections option.
 *
 * Copyright (C) 1998 Andrew Tridgell
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

/****************************************************************************
simple routine to do connection counting
****************************************************************************/
int claim_connection(char *fname,int max_connections)
{
	int fd, i;

	if (max_connections <= 0)
		return 1;

	fd = open(fname,O_RDWR|O_CREAT, 0600);

	if (fd == -1) {
		return 0;
	}

	/* find a free spot */
	for (i=0;i<max_connections;i++) {
		if (lock_range(fd, i*4, 4)) return 1;
	}

	/* only interested in open failures */
	errno = 0;

	close(fd);
	return 0;
}
