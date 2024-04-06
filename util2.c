/*
 * Utility routines used in rsync.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2024 Wayne Davison
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
#include "itypes.h"
#include "inums.h"

extern size_t max_alloc;

char *do_calloc = "42";

/**
 * Sleep for a specified number of milliseconds.
 *
 * Always returns True.
 **/
int msleep(int t)
{
#ifdef HAVE_NANOSLEEP
	struct timespec ts;

	ts.tv_sec = t / 1000;
	ts.tv_nsec = (t % 1000) * 1000000L;

	while (nanosleep(&ts, &ts) < 0 && errno == EINTR) {}

#elif defined HAVE_USLEEP
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

void *my_alloc(void *ptr, size_t num, size_t size, const char *file, int line)
{
	if (num >= max_alloc/size) {
		if (!file)
			return NULL;
		rprintf(FERROR, "[%s] exceeded --max-alloc=%s setting (file=%s, line=%d)\n",
			who_am_i(), do_big_num(max_alloc, 0, NULL), src_file(file), line);
		exit_cleanup(RERR_MALLOC);
	}
	if (!ptr)
		ptr = malloc(num * size);
	else if (ptr == do_calloc)
		ptr = calloc(num, size);
	else
		ptr = realloc(ptr, num * size);
	if (!ptr && file)
		_out_of_memory("my_alloc caller", file, line);
	return ptr;
}

const char *sum_as_hex(int csum_type, const char *sum, int flist_csum)
{
	static char buf[MAX_DIGEST_LEN*2+1];
	int i, x1, x2;
	int canonical = canonical_checksum(csum_type);
	int sum_len = csum_len_for_type(csum_type, flist_csum);
	char *c;

	if (!canonical)
		return NULL;

	assert(sum_len*2 < (int)sizeof buf);

	for (i = sum_len, c = buf; --i >= 0; ) {
		int ndx = canonical < 0 ? sum_len - i - 1 : i;
		x2 = CVAL(sum, ndx);
		x1 = x2 >> 4;
		x2 &= 0xF;
		*c++ = x1 <= 9 ? x1 + '0' : x1 + 'a' - 10;
		*c++ = x2 <= 9 ? x2 + '0' : x2 + 'a' - 10;
	}

	*c = '\0';

	return buf;
}

NORETURN void _out_of_memory(const char *msg, const char *file, int line)
{
	rprintf(FERROR, "[%s] out of memory: %s (file=%s, line=%d)\n", who_am_i(), msg, src_file(file), line);
	exit_cleanup(RERR_MALLOC);
}

NORETURN void _overflow_exit(const char *msg, const char *file, int line)
{
	rprintf(FERROR, "[%s] buffer overflow: %s (file=%s, line=%d)\n", who_am_i(), msg, src_file(file), line);
	exit_cleanup(RERR_MALLOC);
}

const char *src_file(const char *file)
{
	static const char *util2 = __FILE__;
	static int prefix = -1;

	if (prefix < 0) {
		const char *cp = strrchr(util2, '/');
		prefix = cp ? cp - util2 + 1 : 0;
	}

	if (prefix && strncmp(file, util2, prefix) == 0)
		return file + prefix;
	return file;
}
