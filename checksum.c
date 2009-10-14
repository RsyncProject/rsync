/*
 * Routines to support checksumming of bytes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2009 Wayne Davison
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

extern int checksum_seed;
extern int protocol_version;

/*
  a simple 32 bit checksum that can be upadted from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
uint32 get_checksum1(char *buf1, int32 len)
{
    int32 i;
    uint32 s1, s2;
    schar *buf = (schar *)buf1;

    s1 = s2 = 0;
    for (i = 0; i < (len-4); i+=4) {
	s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3] +
	  10*CHAR_OFFSET;
	s1 += (buf[i+0] + buf[i+1] + buf[i+2] + buf[i+3] + 4*CHAR_OFFSET);
    }
    for (; i < len; i++) {
	s1 += (buf[i]+CHAR_OFFSET); s2 += s1;
    }
    return (s1 & 0xffff) + (s2 << 16);
}


void get_checksum2(char *buf, int32 len, char *sum)
{
	md_context m;

	if (protocol_version >= 30) {
		uchar seedbuf[4];
		md5_begin(&m);
		md5_update(&m, (uchar *)buf, len);
		if (checksum_seed) {
			SIVALu(seedbuf, 0, checksum_seed);
			md5_update(&m, seedbuf, 4);
		}
		md5_result(&m, (uchar *)sum);
	} else {
		int32 i;
		static char *buf1;
		static int32 len1;

		mdfour_begin(&m);

		if (len > len1) {
			if (buf1)
				free(buf1);
			buf1 = new_array(char, len+4);
			len1 = len;
			if (!buf1)
				out_of_memory("get_checksum2");
		}

		memcpy(buf1, buf, len);
		if (checksum_seed) {
			SIVAL(buf1,len,checksum_seed);
			len += 4;
		}

		for (i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK)
			mdfour_update(&m, (uchar *)(buf1+i), CSUM_CHUNK);

		/*
		 * Prior to version 27 an incorrect MD4 checksum was computed
		 * by failing to call mdfour_tail() for block sizes that
		 * are multiples of 64.  This is fixed by calling mdfour_update()
		 * even when there are no more bytes.
		 */
		if (len - i > 0 || protocol_version >= 27)
			mdfour_update(&m, (uchar *)(buf1+i), len-i);

		mdfour_result(&m, (uchar *)sum);
	}
}

void file_checksum(char *fname, char *sum, OFF_T size)
{
	struct map_struct *buf;
	OFF_T i, len = size;
	md_context m;
	int32 remainder;
	int fd;

	memset(sum, 0, MAX_DIGEST_LEN);

	fd = do_open(fname, O_RDONLY, 0);
	if (fd == -1)
		return;

	buf = map_file(fd, size, MAX_MAP_SIZE, CSUM_CHUNK);

	if (protocol_version >= 30) {
		md5_begin(&m);

		for (i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
			md5_update(&m, (uchar *)map_ptr(buf, i, CSUM_CHUNK),
				   CSUM_CHUNK);
		}

		remainder = (int32)(len - i);
		if (remainder > 0)
			md5_update(&m, (uchar *)map_ptr(buf, i, remainder), remainder);

		md5_result(&m, (uchar *)sum);
	} else {
		mdfour_begin(&m);

		for (i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
			mdfour_update(&m, (uchar *)map_ptr(buf, i, CSUM_CHUNK),
				      CSUM_CHUNK);
		}

		/* Prior to version 27 an incorrect MD4 checksum was computed
		 * by failing to call mdfour_tail() for block sizes that
		 * are multiples of 64.  This is fixed by calling mdfour_update()
		 * even when there are no more bytes. */
		remainder = (int32)(len - i);
		if (remainder > 0 || protocol_version >= 27)
			mdfour_update(&m, (uchar *)map_ptr(buf, i, remainder), remainder);

		mdfour_result(&m, (uchar *)sum);
	}

	close(fd);
	unmap_file(buf);
}

static int32 sumresidue;
static md_context md;

void sum_init(int seed)
{
	char s[4];

	if (protocol_version >= 30)
		md5_begin(&md);
	else {
		mdfour_begin(&md);
		sumresidue = 0;
		SIVAL(s, 0, seed);
		sum_update(s, 4);
	}
}

/**
 * Feed data into an MD4 accumulator, md.  The results may be
 * retrieved using sum_end().  md is used for different purposes at
 * different points during execution.
 *
 * @todo Perhaps get rid of md and just pass in the address each time.
 * Very slightly clearer and slower.
 **/
void sum_update(const char *p, int32 len)
{
	if (protocol_version >= 30) {
		md5_update(&md, (uchar *)p, len);
		return;
	}

	if (len + sumresidue < CSUM_CHUNK) {
		memcpy(md.buffer + sumresidue, p, len);
		sumresidue += len;
		return;
	}

	if (sumresidue) {
		int32 i = CSUM_CHUNK - sumresidue;
		memcpy(md.buffer + sumresidue, p, i);
		mdfour_update(&md, (uchar *)md.buffer, CSUM_CHUNK);
		len -= i;
		p += i;
	}

	while (len >= CSUM_CHUNK) {
		mdfour_update(&md, (uchar *)p, CSUM_CHUNK);
		len -= CSUM_CHUNK;
		p += CSUM_CHUNK;
	}

	sumresidue = len;
	if (sumresidue)
		memcpy(md.buffer, p, sumresidue);
}

int sum_end(char *sum)
{
	if (protocol_version >= 30) {
		md5_result(&md, (uchar *)sum);
		return MD5_DIGEST_LEN;
	}

	if (sumresidue || protocol_version >= 27)
		mdfour_update(&md, (uchar *)md.buffer, sumresidue);

	mdfour_result(&md, (uchar *)sum);

	return MD4_DIGEST_LEN;
}
