/*
 * Utility routines used in rsync.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2015 Wayne Davison
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
#include "ifuncs.h"
#include "itypes.h"
#include "inums.h"

extern int checksum_len;

/**
 * Sleep for a specified number of milliseconds.
 *
 * Always returns TRUE.  (In the future it might return FALSE if
 * interrupted.)
 **/
int msleep(int t)
{
#ifdef HAVE_USLEEP
	usleep(t*1000);
#else
	int tdiff = 0;
	struct timeval tval, t1, t2;

	gettimeofday(&t1, NULL);

	while (tdiff < t) {
		tval.tv_sec = (t-tdiff)/1000;
		tval.tv_usec = 1000*((t-tdiff)%1000);

		errno = 0;
		select(0,NULL,NULL, NULL, &tval);

		gettimeofday(&t2, NULL);
		tdiff = (t2.tv_sec - t1.tv_sec)*1000 +
			(t2.tv_usec - t1.tv_usec)/1000;
		if (tdiff < 0)
			t1 = t2; /* Time went backwards, so start over. */
	}
#endif

	return True;
}

#define MALLOC_MAX 0x40000000

void *_new_array(unsigned long num, unsigned int size, int use_calloc)
{
	if (num >= MALLOC_MAX/size)
		return NULL;
	return use_calloc ? calloc(num, size) : malloc(num * size);
}

void *_realloc_array(void *ptr, unsigned int size, size_t num)
{
	if (num >= MALLOC_MAX/size)
		return NULL;
	if (!ptr)
		return malloc(size * num);
	return realloc(ptr, size * num);
}

const char *sum_as_hex(const char *sum)
{
	static char buf[MAX_DIGEST_LEN*2+1];
	int i, x1, x2;
	char *c = buf + checksum_len*2;

	assert(c - buf < (int)sizeof buf);

	*c = '\0';

	for (i = checksum_len; --i >= 0; ) {
		x1 = CVAL(sum, i);
		x2 = x1 >> 4;
		x1 &= 0xF;
		*--c = x1 <= 9 ? x1 + '0' : x1 + 'a' - 10;
		*--c = x2 <= 9 ? x2 + '0' : x2 + 'a' - 10;
	}

	return buf;
}

NORETURN void out_of_memory(const char *str)
{
	rprintf(FERROR, "ERROR: out of memory in %s [%s]\n", str, who_am_i());
	exit_cleanup(RERR_MALLOC);
}

NORETURN void overflow_exit(const char *str)
{
	rprintf(FERROR, "ERROR: buffer overflow in %s [%s]\n", str, who_am_i());
	exit_cleanup(RERR_MALLOC);
}
