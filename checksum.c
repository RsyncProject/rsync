/*
 * Routines to support checksumming of bytes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2020 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * In addition, as a special exception, the copyright holders give
 * permission to dynamically link rsync with the OpenSSL and xxhash
 * libraries when those libraries are being distributed in compliance
 * with their license terms, and to distribute a dynamically linked
 * combination of rsync and these libraries.  This is also considered
 * to be covered under the GPL's System Libraries exception.
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
#ifdef SUPPORT_XXHASH
#include "xxhash.h"
#endif
#ifdef USE_OPENSSL
#include "openssl/md4.h"
#include "openssl/md5.h"
#endif

extern int am_server;
extern int local_server;
extern int whole_file;
extern int read_batch;
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
#define CSUM_XXH64 6

struct name_num_obj valid_checksums = {
	"checksum", NULL, NULL, 0, 0, {
#ifdef SUPPORT_XXHASH
		{ CSUM_XXH64, "xxh64", NULL },
		{ CSUM_XXH64, "xxhash", NULL },
#endif
		{ CSUM_MD5, "md5", NULL },
		{ CSUM_MD4, "md4", NULL },
		{ CSUM_NONE, "none", NULL },
		{ 0, NULL, NULL }
	}
};

#ifndef USE_OPENSSL
#define MD5_CTX md_context
#define MD5_Init md5_begin
#define MD5_Update md5_update
#define MD5_Final(digest, cptr) md5_result(cptr, digest)
#endif

int xfersum_type = 0; /* used for the file transfer checksums */
int checksum_type = 0; /* used for the pre-transfer (--checksum) checksums */

static int parse_csum_name(const char *name, int len)
{
	struct name_num_item *nni;

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

	nni = get_nni_by_name(&valid_checksums, name, len);

	if (!nni) {
		rprintf(FERROR, "unknown checksum name: %s\n", name);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	return nni->num;
}

static const char *checksum_name(int num)
{
	struct name_num_item *nni = get_nni_by_num(&valid_checksums, num);

	return nni ? nni->name : num < CSUM_MD4 ? "MD4" : "UNKNOWN";
}

void parse_checksum_choice(int final_call)
{
	if (!valid_checksums.negotiated_name) {
		char *cp = checksum_choice ? strchr(checksum_choice, ',') : NULL;
		if (cp) {
			xfersum_type = parse_csum_name(checksum_choice, cp - checksum_choice);
			checksum_type = parse_csum_name(cp+1, -1);
		} else
			xfersum_type = checksum_type = parse_csum_name(checksum_choice, -1);
	}

	if (xfersum_type == CSUM_NONE)
		whole_file = 1;

	if (final_call && DEBUG_GTE(NSTR, am_server ? 2 : 1)) {
		const char *c_s = am_server ? "Server" : "Client";
		if (valid_checksums.negotiated_name)
			rprintf(FINFO, "%s negotiated checksum: %s\n", c_s, valid_checksums.negotiated_name);
		else if (xfersum_type == checksum_type) {
			rprintf(FINFO, "%s %s checksum: %s\n", c_s,
				checksum_choice ? "chosen" : "protocol-based",
				checksum_name(xfersum_type));
		} else {
			rprintf(FINFO, "%s chosen transfer checksum: %s\n",
				c_s, checksum_name(xfersum_type));
			rprintf(FINFO, "%s chosen pre-transfer checksum: %s\n",
				c_s, checksum_name(checksum_type));
		}
	}
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
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		return 64/8;
#endif
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
	return 0;
}

int canonical_checksum(int csum_type)
{
	return csum_type >= CSUM_MD4 ? 1 : 0;
}

#ifndef HAVE_SIMD /* See simd-checksum-*.cpp. */
/*
  a simple 32 bit checksum that can be updated from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
uint32 get_checksum1(char *buf1, int32 len)
{
	int32 i;
	uint32 s1, s2;
	schar *buf = (schar *)buf1;

	s1 = s2 = 0;
	for (i = 0; i < (len-4); i+=4) {
		s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3] + 10*CHAR_OFFSET;
		s1 += (buf[i+0] + buf[i+1] + buf[i+2] + buf[i+3] + 4*CHAR_OFFSET);
	}
	for (; i < len; i++) {
		s1 += (buf[i]+CHAR_OFFSET); s2 += s1;
	}
	return (s1 & 0xffff) + (s2 << 16);
}
#endif

void get_checksum2(char *buf, int32 len, char *sum)
{
	switch (xfersum_type) {
	  case CSUM_MD5: {
		MD5_CTX m5;
		uchar seedbuf[4];
		MD5_Init(&m5);
		if (proper_seed_order) {
			if (checksum_seed) {
				SIVALu(seedbuf, 0, checksum_seed);
				MD5_Update(&m5, seedbuf, 4);
			}
			MD5_Update(&m5, (uchar *)buf, len);
		} else {
			MD5_Update(&m5, (uchar *)buf, len);
			if (checksum_seed) {
				SIVALu(seedbuf, 0, checksum_seed);
				MD5_Update(&m5, seedbuf, 4);
			}
		}
		MD5_Final((uchar *)sum, &m5);
		break;
	  }
	  case CSUM_MD4:
#ifdef USE_OPENSSL
	  {
		MD4_CTX m4;
		MD4_Init(&m4);
		MD4_Update(&m4, (uchar *)buf, len);
		if (checksum_seed) {
			uchar seedbuf[4];
			SIVALu(seedbuf, 0, checksum_seed);
			MD4_Update(&m4, seedbuf, 4);
		}
		MD4_Final((uchar *)sum, &m4);
		break;
	  }
#endif
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC: {
		md_context m;
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
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		SIVAL64(sum, 0, XXH64(buf, len, checksum_seed));
		break;
#endif
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
}

void file_checksum(const char *fname, const STRUCT_STAT *st_p, char *sum)
{
	struct map_struct *buf;
	OFF_T i, len = st_p->st_size;
	int32 remainder;
	int fd;

	memset(sum, 0, MAX_DIGEST_LEN);

	fd = do_open(fname, O_RDONLY, 0);
	if (fd == -1)
		return;

	buf = map_file(fd, len, MAX_MAP_SIZE, CHUNK_SIZE);

	switch (checksum_type) {
	  case CSUM_MD5: {
		MD5_CTX m5;

		MD5_Init(&m5);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			MD5_Update(&m5, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			MD5_Update(&m5, (uchar *)map_ptr(buf, i, remainder), remainder);

		MD5_Final((uchar *)sum, &m5);
		break;
	  }
	  case CSUM_MD4:
#ifdef USE_OPENSSL
	  {
		MD4_CTX m4;

		MD4_Init(&m4);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			MD4_Update(&m4, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			MD4_Update(&m4, (uchar *)map_ptr(buf, i, remainder), remainder);

		MD4_Final((uchar *)sum, &m4);
		break;
	  }
#endif
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC: {
		md_context m;

		mdfour_begin(&m);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			mdfour_update(&m, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		/* Prior to version 27 an incorrect MD4 checksum was computed
		 * by failing to call mdfour_tail() for block sizes that
		 * are multiples of 64.  This is fixed by calling mdfour_update()
		 * even when there are no more bytes. */
		remainder = (int32)(len - i);
		if (remainder > 0 || checksum_type > CSUM_MD4_BUSTED)
			mdfour_update(&m, (uchar *)map_ptr(buf, i, remainder), remainder);

		mdfour_result(&m, (uchar *)sum);
		break;
	  }
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64: {
		XXH64_state_t* state = XXH64_createState();
		if (state == NULL)
			out_of_memory("file_checksum XXH64");

		if (XXH64_reset(state, 0) == XXH_ERROR) {
			rprintf(FERROR, "error resetting XXH64 seed");
			exit_cleanup(RERR_STREAMIO);
		}

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE) {
			XXH_errorcode const updateResult =
			    XXH64_update(state, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);
			if (updateResult == XXH_ERROR) {
				rprintf(FERROR, "error computing XXH64 hash");
				exit_cleanup(RERR_STREAMIO);
			}
		}

		remainder = (int32)(len - i);
		if (remainder > 0)
			XXH64_update(state, (uchar *)map_ptr(buf, i, CHUNK_SIZE), remainder);
		SIVAL64(sum, 0, XXH64_digest(state));

		XXH64_freeState(state);
		break;
	  }
#endif
	  default:
		rprintf(FERROR, "Invalid checksum-choice for --checksum: %s (%d)\n",
			checksum_name(checksum_type), checksum_type);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	close(fd);
	unmap_file(buf);
}

static int32 sumresidue;
static union {
	md_context md;
#ifdef USE_OPENSSL
	MD4_CTX m4;
#endif
	MD5_CTX m5;
} ctx;
#ifdef SUPPORT_XXHASH
static XXH64_state_t* xxh64_state;
#endif
static int cursum_type;

void sum_init(int csum_type, int seed)
{
	char s[4];

	if (csum_type < 0)
		csum_type = parse_csum_name(NULL, 0);
	cursum_type = csum_type;

	switch (csum_type) {
	  case CSUM_MD5:
		MD5_Init(&ctx.m5);
		break;
	  case CSUM_MD4:
#ifdef USE_OPENSSL
		MD4_Init(&ctx.m4);
#else
		mdfour_begin(&ctx.md);
		sumresidue = 0;
#endif
		break;
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		mdfour_begin(&ctx.md);
		sumresidue = 0;
		SIVAL(s, 0, seed);
		sum_update(s, 4);
		break;
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		if (xxh64_state == NULL) {
			xxh64_state = XXH64_createState();
			if (xxh64_state == NULL)
				out_of_memory("sum_init xxh64");
		}
		if (XXH64_reset(xxh64_state, 0) == XXH_ERROR) {
			rprintf(FERROR, "error resetting XXH64 state");
			exit_cleanup(RERR_STREAMIO);
		}
		break;
#endif
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
		MD5_Update(&ctx.m5, (uchar *)p, len);
		break;
	  case CSUM_MD4:
#ifdef USE_OPENSSL
		MD4_Update(&ctx.m4, (uchar *)p, len);
		break;
#endif
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		if (len + sumresidue < CSUM_CHUNK) {
			memcpy(ctx.md.buffer + sumresidue, p, len);
			sumresidue += len;
			break;
		}

		if (sumresidue) {
			int32 i = CSUM_CHUNK - sumresidue;
			memcpy(ctx.md.buffer + sumresidue, p, i);
			mdfour_update(&ctx.md, (uchar *)ctx.md.buffer, CSUM_CHUNK);
			len -= i;
			p += i;
		}

		while (len >= CSUM_CHUNK) {
			mdfour_update(&ctx.md, (uchar *)p, CSUM_CHUNK);
			len -= CSUM_CHUNK;
			p += CSUM_CHUNK;
		}

		sumresidue = len;
		if (sumresidue)
			memcpy(ctx.md.buffer, p, sumresidue);
		break;
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		if (XXH64_update(xxh64_state, p, len) == XXH_ERROR) {
			rprintf(FERROR, "error computing XXH64 hash");
			exit_cleanup(RERR_STREAMIO);
		}
		break;
#endif
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
		MD5_Final((uchar *)sum, &ctx.m5);
		break;
	  case CSUM_MD4:
#ifdef USE_OPENSSL
		MD4_Final((uchar *)sum, &ctx.m4);
		break;
#endif
	  case CSUM_MD4_OLD:
		mdfour_update(&ctx.md, (uchar *)ctx.md.buffer, sumresidue);
		mdfour_result(&ctx.md, (uchar *)sum);
		break;
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		if (sumresidue)
			mdfour_update(&ctx.md, (uchar *)ctx.md.buffer, sumresidue);
		mdfour_result(&ctx.md, (uchar *)sum);
		break;
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		SIVAL64(sum, 0, XXH64_digest(xxh64_state));
		break;
#endif
	  case CSUM_NONE:
		*sum = '\0';
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}

	return csum_len_for_type(cursum_type, 0);
}
