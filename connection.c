/*
 * Support the max connections option.
 *
 * Copyright (C) 1998 Andrew Tridgell
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

/* A simple routine to do connection counting.  This returns 1 on success
 * and 0 on failure, with errno also being set if the open() failed (errno
 * will be 0 if the lock request failed). */
int claim_connection(char *fname, int max_connections)
{
	int fd, i;

	if (max_connections == 0)
		return 1;

	if ((fd = open(fname, O_RDWR|O_CREAT, 0600)) < 0)
		return 0;

	/* Find a free spot. */
	for (i = 0; i < max_connections; i++) {
		if (lock_range(fd, i*4, 4))
			return 1;
	}

	close(fd);

	/* A lock failure needs to return an errno of 0. */
	errno = 0;
	return 0;
}
