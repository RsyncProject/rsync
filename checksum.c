/*
 * Routines to support checksumming of bytes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2018 Wayne Davison
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
extern int proper_seed_order;
extern char *checksum_choice;

#define CSUM_NONE 0
#define CSUM_MD4_ARCHAIC 1
#define CSUM_MD4_BUSTED 2
#define CSUM_MD4_OLD 3
#define CSUM_MD4 4
#define CSUM_MD5 5

int xfersum_type = 0; /* used for the file transfer checksums */
int checksum_type = 0; /* used for the pre-transfer (--checksum) checksums */

/* Returns 1 if --whole-file must be enabled. */
int parse_checksum_choice(void)
{
	char *cp = checksum_choice ? strchr(checksum_choice, ',') : NULL;
	if (cp) {
		xfersum_type = parse_csum_name(checksum_choice, cp - checksum_choice);
		checksum_type = parse_csum_name(cp+1, -1);
	} else
		xfersum_type = checksum_type = parse_csum_name(checksum_choice, -1);
	return xfersum_type == CSUM_NONE;
}

int parse_csum_name(const char *name, int len)
{
	if (len < 0 && name)
		len = strlen(name);

	if (!name || (len == 4 && strncasecmp(name, "auto", 4) == 0)) {
		if (protocol_version >= 30)
			return CSUM_MD5;
		if (protocol_version >= 27)
			return CSUM_MD4_OLD;
		if (protocol_version >= 21)
			return CSUM_MD4_BUSTED;
		return CSUM_MD4_ARCHAIC;
	}
	if (len == 3 && strncasecmp(name, "md4", 3) == 0)
		return CSUM_MD4;
	if (len == 3 && strncasecmp(name, "md5", 3) == 0)
		return CSUM_MD5;
	if (len == 4 && strncasecmp(name, "none", 4) == 0)
		return CSUM_NONE;

	rprintf(FERROR, "unknown checksum name: %s\n", name);
	exit_cleanup(RERR_UNSUPPORTED);
}

int csum_len_for_type(int cst, BOOL flist_csum)
{
	switch (cst) {
	  case CSUM_NONE:
		return 1;
	  case CSUM_MD4_ARCHAIC:
		/* The oldest checksum code is rather weird: the file-list code only sent
		 * 2-byte checksums, but all other checksums were full MD4 length. */
		return flist_csum ? 2 : MD4_DIGEST_LEN;
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
		return MD4_DIGEST_LEN;
	  case CSUM_MD5:
		return MD5_DIGEST_LEN;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
	return 0;
}

int canonical_checksum(int csum_type)
{
    return csum_type >= CSUM_MD4 ? 1 : 0;
}

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

	switch (xfersum_type) {
	  case CSUM_MD5: {
		uchar seedbuf[4];
		md5_begin(&m);
		if (proper_seed_order) {
			if (checksum_seed) {
				SIVALu(seedbuf, 0, checksum_seed);
				md5_update(&m, seedbuf, 4);
			}
			md5_update(&m, (uchar *)buf, len);
		} else {
			md5_update(&m, (uchar *)buf, len);
			if (checksum_seed) {
				SIVALu(seedbuf, 0, checksum_seed);
				md5_update(&m, seedbuf, 4);
			}
		}
		md5_result(&m, (uchar *)sum);
		break;
	  }
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC: {
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
		if (len - i > 0 || xfersum_type > CSUM_MD4_BUSTED)
			mdfour_update(&m, (uchar *)(buf1+i), len-i);

		mdfour_result(&m, (uchar *)sum);
		break;
	  }
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
}

void file_checksum(const char *fname, const STRUCT_STAT *st_p, char *sum)
{
	struct map_struct *buf;
	OFF_T i, len = st_p->st_size;
	md_context m;
	int32 remainder;
	int fd;

	memset(sum, 0, MAX_DIGEST_LEN);

	fd = do_open(fname, O_RDONLY, 0);
	if (fd == -1)
		return;

	buf = map_file(fd, len, MAX_MAP_SIZE, CSUM_CHUNK);

	switch (checksum_type) {
	  case CSUM_MD5:
		md5_begin(&m);

		for (i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
			md5_update(&m, (uchar *)map_ptr(buf, i, CSUM_CHUNK),
				   CSUM_CHUNK);
		}

		remainder = (int32)(len - i);
		if (remainder > 0)
			md5_update(&m, (uchar *)map_ptr(buf, i, remainder), remainder);

		md5_result(&m, (uchar *)sum);
		break;
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
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
		if (remainder > 0 || checksum_type > CSUM_MD4_BUSTED)
			mdfour_update(&m, (uchar *)map_ptr(buf, i, remainder), remainder);

		mdfour_result(&m, (uchar *)sum);
		break;
	  default:
		rprintf(FERROR, "invalid checksum-choice for the --checksum option (%d)\n", checksum_type);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	close(fd);
	unmap_file(buf);
}

static int32 sumresidue;
static md_context md;
static int cursum_type;

void sum_init(int csum_type, int seed)
{
	char s[4];

	if (csum_type < 0)
		csum_type = parse_csum_name(NULL, 0);
	cursum_type = csum_type;

	switch (csum_type) {
	  case CSUM_MD5:
		md5_begin(&md);
		break;
	  case CSUM_MD4:
		mdfour_begin(&md);
		sumresidue = 0;
		break;
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		mdfour_begin(&md);
		sumresidue = 0;
		SIVAL(s, 0, seed);
		sum_update(s, 4);
		break;
	  case CSUM_NONE:
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
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
	switch (cursum_type) {
	  case CSUM_MD5:
		md5_update(&md, (uchar *)p, len);
		break;
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		if (len + sumresidue < CSUM_CHUNK) {
			memcpy(md.buffer + sumresidue, p, len);
			sumresidue += len;
			break;
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
		break;
	  case CSUM_NONE:
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
}

/* NOTE: all the callers of sum_end() pass in a pointer to a buffer that is
 * MAX_DIGEST_LEN in size, so even if the csum-len is shorter that that (i.e.
 * CSUM_MD4_ARCHAIC), we don't have to worry about limiting the data we write
 * into the "sum" buffer. */
int sum_end(char *sum)
{
	switch (cursum_type) {
	  case CSUM_MD5:
		md5_result(&md, (uchar *)sum);
		break;
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
		mdfour_update(&md, (uchar *)md.buffer, sumresidue);
		mdfour_result(&md, (uchar *)sum);
		break;
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		if (sumresidue)
			mdfour_update(&md, (uchar *)md.buffer, sumresidue);
		mdfour_result(&md, (uchar *)sum);
		break;
	  case CSUM_NONE:
		*sum = '\0';
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}

	return csum_len_for_type(cursum_type, 0);
}
