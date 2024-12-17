/*
 * Routines to support checksumming of bytes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2023 Wayne Davison
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
#include <xxhash.h>
# if XXH_VERSION_NUMBER >= 800
#  define SUPPORT_XXH3 1
# endif
#endif

extern int am_server;
extern int whole_file;
extern int checksum_seed;
extern int protocol_version;
extern int proper_seed_order;
extern const char *checksum_choice;

#define NNI_BUILTIN (1<<0)
#define NNI_EVP (1<<1)
#define NNI_EVP_OK (1<<2)

struct name_num_item valid_checksums_items[] = {
#ifdef SUPPORT_XXH3
	{ CSUM_XXH3_128, 0, "xxh128", NULL },
	{ CSUM_XXH3_64, 0, "xxh3", NULL },
#endif
#ifdef SUPPORT_XXHASH
	{ CSUM_XXH64, 0, "xxh64", NULL },
	{ CSUM_XXH64, 0, "xxhash", NULL },
#endif
	{ CSUM_MD5, NNI_BUILTIN|NNI_EVP, "md5", NULL },
	{ CSUM_MD4, NNI_BUILTIN|NNI_EVP, "md4", NULL },
#ifdef SHA_DIGEST_LENGTH
	{ CSUM_SHA1, NNI_EVP, "sha1", NULL },
#endif
	{ CSUM_NONE, 0, "none", NULL },
	{ 0, 0, NULL, NULL }
};

struct name_num_obj valid_checksums = {
	"checksum", NULL, 0, 0, valid_checksums_items
};

struct name_num_item valid_auth_checksums_items[] = {
#ifdef SHA512_DIGEST_LENGTH
	{ CSUM_SHA512, NNI_EVP, "sha512", NULL },
#endif
#ifdef SHA256_DIGEST_LENGTH
	{ CSUM_SHA256, NNI_EVP, "sha256", NULL },
#endif
#ifdef SHA_DIGEST_LENGTH
	{ CSUM_SHA1, NNI_EVP, "sha1", NULL },
#endif
	{ CSUM_MD5, NNI_BUILTIN|NNI_EVP, "md5", NULL },
	{ CSUM_MD4, NNI_BUILTIN|NNI_EVP, "md4", NULL },
	{ 0, 0, NULL, NULL }
};

struct name_num_obj valid_auth_checksums = {
	"daemon auth checksum", NULL, 0, 0, valid_auth_checksums_items
};

/* These cannot make use of openssl, so they're marked just as built-in */
struct name_num_item implied_checksum_md4 =
    { CSUM_MD4, NNI_BUILTIN, "md4", NULL };
struct name_num_item implied_checksum_md5 =
    { CSUM_MD5, NNI_BUILTIN, "md5", NULL };

struct name_num_item *xfer_sum_nni; /* used for the transfer checksum2 computations */
int xfer_sum_len;
struct name_num_item *file_sum_nni; /* used for the pre-transfer --checksum computations */
int file_sum_len, file_sum_extra_cnt;

#ifdef USE_OPENSSL
const EVP_MD *xfer_sum_evp_md;
const EVP_MD *file_sum_evp_md;
EVP_MD_CTX *ctx_evp = NULL;
#endif

static int initialized_choices = 0;

struct name_num_item *parse_csum_name(const char *name, int len)
{
	struct name_num_item *nni;

	if (len < 0 && name)
		len = strlen(name);

	init_checksum_choices();

	if (!name || (len == 4 && strncasecmp(name, "auto", 4) == 0)) {
		if (protocol_version >= 30) {
			if (!proper_seed_order)
				return &implied_checksum_md5;
			name = "md5";
			len = 3;
		} else {
			if (protocol_version >= 27)
				implied_checksum_md4.num = CSUM_MD4_OLD;
			else if (protocol_version >= 21)
				implied_checksum_md4.num = CSUM_MD4_BUSTED;
			else
				implied_checksum_md4.num = CSUM_MD4_ARCHAIC;
			return &implied_checksum_md4;
		}
	}

	nni = get_nni_by_name(&valid_checksums, name, len);

	if (!nni) {
		rprintf(FERROR, "unknown checksum name: %s\n", name);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	return nni;
}

#ifdef USE_OPENSSL
static const EVP_MD *csum_evp_md(struct name_num_item *nni)
{
	const EVP_MD *emd;
	if (!(nni->flags & NNI_EVP))
		return NULL;

#ifdef USE_MD5_ASM
	if (nni->num == CSUM_MD5)
		emd = NULL;
	else
#endif
		emd = EVP_get_digestbyname(nni->name);
	if (emd && !(nni->flags & NNI_EVP_OK)) { /* Make sure it works before we advertise it */
		if (!ctx_evp && !(ctx_evp = EVP_MD_CTX_create()))
			out_of_memory("csum_evp_md");
		/* Some routines are marked as legacy and are not enabled in the openssl.cnf file.
		 * If we can't init the emd, we'll fall back to our built-in code. */
		if (EVP_DigestInit_ex(ctx_evp, emd, NULL) == 0)
			emd = NULL;
		else
			nni->flags = (nni->flags & ~NNI_BUILTIN) | NNI_EVP_OK;
	}
	if (!emd)
		nni->flags &= ~NNI_EVP;
	return emd;
}
#endif

void parse_checksum_choice(int final_call)
{
	if (valid_checksums.negotiated_nni)
		xfer_sum_nni = file_sum_nni = valid_checksums.negotiated_nni;
	else {
		char *cp = checksum_choice ? strchr(checksum_choice, ',') : NULL;
		if (cp) {
			xfer_sum_nni = parse_csum_name(checksum_choice, cp - checksum_choice);
			file_sum_nni = parse_csum_name(cp+1, -1);
		} else
			xfer_sum_nni = file_sum_nni = parse_csum_name(checksum_choice, -1);
		if (am_server && checksum_choice)
			validate_choice_vs_env(NSTR_CHECKSUM, xfer_sum_nni->num, file_sum_nni->num);
	}
	xfer_sum_len = csum_len_for_type(xfer_sum_nni->num, 0);
	file_sum_len = csum_len_for_type(file_sum_nni->num, 0);
#ifdef USE_OPENSSL
	xfer_sum_evp_md = csum_evp_md(xfer_sum_nni);
	file_sum_evp_md = csum_evp_md(file_sum_nni);
#endif

	file_sum_extra_cnt = (file_sum_len + EXTRA_LEN - 1) / EXTRA_LEN;

	if (xfer_sum_nni->num == CSUM_NONE)
		whole_file = 1;

	/* Snag the checksum name for both write_batch's option output & the following debug output. */
	if (valid_checksums.negotiated_nni)
		checksum_choice = valid_checksums.negotiated_nni->name;
	else if (checksum_choice == NULL)
		checksum_choice = xfer_sum_nni->name;

	if (final_call && DEBUG_GTE(NSTR, am_server ? 3 : 1)) {
		rprintf(FINFO, "%s%s checksum: %s\n",
			am_server ? "Server" : "Client",
			valid_checksums.negotiated_nni ? " negotiated" : "",
			checksum_choice);
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
#ifdef SHA_DIGEST_LENGTH
	  case CSUM_SHA1:
		return SHA_DIGEST_LENGTH;
#endif
#ifdef SHA256_DIGEST_LENGTH
	  case CSUM_SHA256:
		return SHA256_DIGEST_LENGTH;
#endif
#ifdef SHA512_DIGEST_LENGTH
	  case CSUM_SHA512:
		return SHA512_DIGEST_LENGTH;
#endif
	  case CSUM_XXH64:
	  case CSUM_XXH3_64:
		return 64/8;
	  case CSUM_XXH3_128:
		return 128/8;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
	return 0;
}

/* Returns 0 if the checksum is not canonical (i.e. it includes a seed value).
 * Returns 1 if the public sum order matches our internal sum order.
 * Returns -1 if the public sum order is the reverse of our internal sum order.
 */
int canonical_checksum(int csum_type)
{
	switch (csum_type) {
	  case CSUM_NONE:
	  case CSUM_MD4_ARCHAIC:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
		break;
	  case CSUM_MD4:
	  case CSUM_MD5:
	  case CSUM_SHA1:
	  case CSUM_SHA256:
	  case CSUM_SHA512:
		return -1;
	  case CSUM_XXH64:
	  case CSUM_XXH3_64:
	  case CSUM_XXH3_128:
		return 1;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
	return 0;
}

#ifndef USE_ROLL_SIMD /* See simd-checksum-*.cpp. */
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

/* The "sum" buffer must be at least MAX_DIGEST_LEN bytes! */
void get_checksum2(char *buf, int32 len, char *sum)
{
#ifdef USE_OPENSSL
	if (xfer_sum_evp_md) {
		static EVP_MD_CTX *evp = NULL;
		uchar seedbuf[4];
		if (!evp && !(evp = EVP_MD_CTX_create()))
			out_of_memory("get_checksum2");
		EVP_DigestInit_ex(evp, xfer_sum_evp_md, NULL);
		if (checksum_seed) {
			SIVALu(seedbuf, 0, checksum_seed);
			EVP_DigestUpdate(evp, seedbuf, 4);
		}
		EVP_DigestUpdate(evp, (uchar *)buf, len);
		EVP_DigestFinal_ex(evp, (uchar *)sum, NULL);
	} else
#endif
	switch (xfer_sum_nni->num) {
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		SIVAL64(sum, 0, XXH64(buf, len, checksum_seed));
		break;
#endif
#ifdef SUPPORT_XXH3
	  case CSUM_XXH3_64:
		SIVAL64(sum, 0, XXH3_64bits_withSeed(buf, len, checksum_seed));
		break;
	  case CSUM_XXH3_128: {
		XXH128_hash_t digest = XXH3_128bits_withSeed(buf, len, checksum_seed);
		SIVAL64(sum, 0, digest.low64);
		SIVAL64(sum, 8, digest.high64);
		break;
	  }
#endif
	  case CSUM_MD5: {
		md_context m5;
		uchar seedbuf[4];
		md5_begin(&m5);
		if (proper_seed_order) {
			if (checksum_seed) {
				SIVALu(seedbuf, 0, checksum_seed);
				md5_update(&m5, seedbuf, 4);
			}
			md5_update(&m5, (uchar *)buf, len);
		} else {
			md5_update(&m5, (uchar *)buf, len);
			if (checksum_seed) {
				SIVALu(seedbuf, 0, checksum_seed);
				md5_update(&m5, seedbuf, 4);
			}
		}
		md5_result(&m5, (uchar *)sum);
		break;
	  }
	  case CSUM_MD4:
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
		if (len - i > 0 || xfer_sum_nni->num > CSUM_MD4_BUSTED)
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
	int32 remainder;
	int fd;

	fd = do_open_checklinks(fname);
	if (fd == -1) {
		memset(sum, 0, file_sum_len);
		return;
	}

	buf = map_file(fd, len, MAX_MAP_SIZE, CHUNK_SIZE);

#ifdef USE_OPENSSL
	if (file_sum_evp_md) {
		static EVP_MD_CTX *evp = NULL;
		if (!evp && !(evp = EVP_MD_CTX_create()))
			out_of_memory("file_checksum");

		EVP_DigestInit_ex(evp, file_sum_evp_md, NULL);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			EVP_DigestUpdate(evp, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			EVP_DigestUpdate(evp, (uchar *)map_ptr(buf, i, remainder), remainder);

		EVP_DigestFinal_ex(evp, (uchar *)sum, NULL);
	} else
#endif
	switch (file_sum_nni->num) {
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64: {
		static XXH64_state_t* state = NULL;
		if (!state && !(state = XXH64_createState()))
			out_of_memory("file_checksum");

		XXH64_reset(state, 0);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			XXH64_update(state, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			XXH64_update(state, (uchar *)map_ptr(buf, i, remainder), remainder);

		SIVAL64(sum, 0, XXH64_digest(state));
		break;
	  }
#endif
#ifdef SUPPORT_XXH3
	  case CSUM_XXH3_64: {
		static XXH3_state_t* state = NULL;
		if (!state && !(state = XXH3_createState()))
			out_of_memory("file_checksum");

		XXH3_64bits_reset(state);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			XXH3_64bits_update(state, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			XXH3_64bits_update(state, (uchar *)map_ptr(buf, i, remainder), remainder);

		SIVAL64(sum, 0, XXH3_64bits_digest(state));
		break;
	  }
	  case CSUM_XXH3_128: {
		XXH128_hash_t digest;
		static XXH3_state_t* state = NULL;
		if (!state && !(state = XXH3_createState()))
			out_of_memory("file_checksum");

		XXH3_128bits_reset(state);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			XXH3_128bits_update(state, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			XXH3_128bits_update(state, (uchar *)map_ptr(buf, i, remainder), remainder);

		digest = XXH3_128bits_digest(state);
		SIVAL64(sum, 0, digest.low64);
		SIVAL64(sum, 8, digest.high64);
		break;
	  }
#endif
	  case CSUM_MD5: {
		md_context m5;

		md5_begin(&m5);

		for (i = 0; i + CHUNK_SIZE <= len; i += CHUNK_SIZE)
			md5_update(&m5, (uchar *)map_ptr(buf, i, CHUNK_SIZE), CHUNK_SIZE);

		remainder = (int32)(len - i);
		if (remainder > 0)
			md5_update(&m5, (uchar *)map_ptr(buf, i, remainder), remainder);

		md5_result(&m5, (uchar *)sum);
		break;
	  }
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC: {
		md_context m;

		mdfour_begin(&m);

		for (i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK)
			mdfour_update(&m, (uchar *)map_ptr(buf, i, CSUM_CHUNK), CSUM_CHUNK);

		/* Prior to version 27 an incorrect MD4 checksum was computed
		 * by failing to call mdfour_tail() for block sizes that
		 * are multiples of 64.  This is fixed by calling mdfour_update()
		 * even when there are no more bytes. */
		remainder = (int32)(len - i);
		if (remainder > 0 || file_sum_nni->num > CSUM_MD4_BUSTED)
			mdfour_update(&m, (uchar *)map_ptr(buf, i, remainder), remainder);

		mdfour_result(&m, (uchar *)sum);
		break;
	  }
	  default:
		rprintf(FERROR, "Invalid checksum-choice for --checksum: %s (%d)\n",
			file_sum_nni->name, file_sum_nni->num);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	close(fd);
	unmap_file(buf);
}

static int32 sumresidue;
static md_context ctx_md;
#ifdef SUPPORT_XXHASH
static XXH64_state_t* xxh64_state;
#endif
#ifdef SUPPORT_XXH3
static XXH3_state_t* xxh3_state;
#endif
static struct name_num_item *cur_sum_nni;
int cur_sum_len;

#ifdef USE_OPENSSL
static const EVP_MD *cur_sum_evp_md;
#endif

/* Initialize a hash digest accumulator.  Data is supplied via
 * sum_update() and the resulting binary digest is retrieved via
 * sum_end().  This only supports one active sum at a time. */
int sum_init(struct name_num_item *nni, int seed)
{
	char s[4];

	if (!nni)
		nni = parse_csum_name(NULL, 0);
	cur_sum_nni = nni;
	cur_sum_len = csum_len_for_type(nni->num, 0);
#ifdef USE_OPENSSL
	cur_sum_evp_md = csum_evp_md(nni);
#endif

#ifdef USE_OPENSSL
	if (cur_sum_evp_md) {
		if (!ctx_evp && !(ctx_evp = EVP_MD_CTX_create()))
			out_of_memory("file_checksum");
		EVP_DigestInit_ex(ctx_evp, cur_sum_evp_md, NULL);
	} else
#endif
	switch (cur_sum_nni->num) {
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		if (!xxh64_state && !(xxh64_state = XXH64_createState()))
			out_of_memory("sum_init");
		XXH64_reset(xxh64_state, 0);
		break;
#endif
#ifdef SUPPORT_XXH3
	  case CSUM_XXH3_64:
		if (!xxh3_state && !(xxh3_state = XXH3_createState()))
			out_of_memory("sum_init");
		XXH3_64bits_reset(xxh3_state);
		break;
	  case CSUM_XXH3_128:
		if (!xxh3_state && !(xxh3_state = XXH3_createState()))
			out_of_memory("sum_init");
		XXH3_128bits_reset(xxh3_state);
		break;
#endif
	  case CSUM_MD5:
		md5_begin(&ctx_md);
		break;
	  case CSUM_MD4:
		mdfour_begin(&ctx_md);
		sumresidue = 0;
		break;
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		mdfour_begin(&ctx_md);
		sumresidue = 0;
		SIVAL(s, 0, seed);
		sum_update(s, 4);
		break;
	  case CSUM_NONE:
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}

	return cur_sum_len;
}

/* Feed data into a hash digest accumulator. */
void sum_update(const char *p, int32 len)
{
#ifdef USE_OPENSSL
	if (cur_sum_evp_md) {
		EVP_DigestUpdate(ctx_evp, (uchar *)p, len);
	} else
#endif
	switch (cur_sum_nni->num) {
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		XXH64_update(xxh64_state, p, len);
		break;
#endif
#ifdef SUPPORT_XXH3
	  case CSUM_XXH3_64:
		XXH3_64bits_update(xxh3_state, p, len);
		break;
	  case CSUM_XXH3_128:
		XXH3_128bits_update(xxh3_state, p, len);
		break;
#endif
	  case CSUM_MD5:
		md5_update(&ctx_md, (uchar *)p, len);
		break;
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		if (len + sumresidue < CSUM_CHUNK) {
			memcpy(ctx_md.buffer + sumresidue, p, len);
			sumresidue += len;
			break;
		}

		if (sumresidue) {
			int32 i = CSUM_CHUNK - sumresidue;
			memcpy(ctx_md.buffer + sumresidue, p, i);
			mdfour_update(&ctx_md, (uchar *)ctx_md.buffer, CSUM_CHUNK);
			len -= i;
			p += i;
		}

		while (len >= CSUM_CHUNK) {
			mdfour_update(&ctx_md, (uchar *)p, CSUM_CHUNK);
			len -= CSUM_CHUNK;
			p += CSUM_CHUNK;
		}

		sumresidue = len;
		if (sumresidue)
			memcpy(ctx_md.buffer, p, sumresidue);
		break;
	  case CSUM_NONE:
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
}

/* The sum buffer only needs to be as long as the current checksum's digest
 * len, not MAX_DIGEST_LEN. Note that for CSUM_MD4_ARCHAIC that is the full
 * MD4_DIGEST_LEN even if the file-list code is going to ignore all but the
 * first 2 bytes of it. */
void sum_end(char *sum)
{
#ifdef USE_OPENSSL
	if (cur_sum_evp_md) {
		EVP_DigestFinal_ex(ctx_evp, (uchar *)sum, NULL);
	} else
#endif
	switch (cur_sum_nni->num) {
#ifdef SUPPORT_XXHASH
	  case CSUM_XXH64:
		SIVAL64(sum, 0, XXH64_digest(xxh64_state));
		break;
#endif
#ifdef SUPPORT_XXH3
	  case CSUM_XXH3_64:
		SIVAL64(sum, 0, XXH3_64bits_digest(xxh3_state));
		break;
	  case CSUM_XXH3_128: {
		XXH128_hash_t digest = XXH3_128bits_digest(xxh3_state);
		SIVAL64(sum, 0, digest.low64);
		SIVAL64(sum, 8, digest.high64);
		break;
	  }
#endif
	  case CSUM_MD5:
		md5_result(&ctx_md, (uchar *)sum);
		break;
	  case CSUM_MD4:
	  case CSUM_MD4_OLD:
		mdfour_update(&ctx_md, (uchar *)ctx_md.buffer, sumresidue);
		mdfour_result(&ctx_md, (uchar *)sum);
		break;
	  case CSUM_MD4_BUSTED:
	  case CSUM_MD4_ARCHAIC:
		if (sumresidue)
			mdfour_update(&ctx_md, (uchar *)ctx_md.buffer, sumresidue);
		mdfour_result(&ctx_md, (uchar *)sum);
		break;
	  case CSUM_NONE:
		*sum = '\0';
		break;
	  default: /* paranoia to prevent missing case values */
		exit_cleanup(RERR_UNSUPPORTED);
	}
}

#if defined SUPPORT_XXH3 || defined USE_OPENSSL
static void verify_digest(struct name_num_item *nni, BOOL check_auth_list)
{
#ifdef SUPPORT_XXH3
	static int xxh3_result = 0;
#endif
#ifdef USE_OPENSSL
	static int prior_num = 0, prior_flags = 0, prior_result = 0;
#endif

#ifdef SUPPORT_XXH3
	if (nni->num == CSUM_XXH3_64 || nni->num == CSUM_XXH3_128) {
		if (!xxh3_result) {
			char buf[32816];
			int j;
			for (j = 0; j < (int)sizeof buf; j++)
				buf[j] = ' ' + (j % 96);
			sum_init(nni, 0);
			sum_update(buf, 32816);
			sum_update(buf, 31152);
			sum_update(buf, 32474);
			sum_update(buf, 9322);
			xxh3_result = XXH3_64bits_digest(xxh3_state) != 0xadbcf16d4678d1de ? -1 : 1;
		}
		if (xxh3_result < 0)
			nni->num = CSUM_gone;
		return;
	}
#endif

#ifdef USE_OPENSSL
	if (BITS_SETnUNSET(nni->flags, NNI_EVP, NNI_BUILTIN|NNI_EVP_OK)) {
		if (nni->num == prior_num && nni->flags == prior_flags) {
			nni->flags = prior_result;
			if (!(nni->flags & NNI_EVP))
				nni->num = CSUM_gone;
		} else {
			prior_num = nni->num;
			prior_flags = nni->flags;
			if (!csum_evp_md(nni))
				nni->num = CSUM_gone;
			prior_result = nni->flags;
			if (check_auth_list && (nni = get_nni_by_num(&valid_auth_checksums, prior_num)) != NULL)
				verify_digest(nni, False);
		}
	}
#endif
}
#endif

void init_checksum_choices()
{
#if defined SUPPORT_XXH3 || defined USE_OPENSSL
	struct name_num_item *nni;
#endif

	if (initialized_choices)
		return;

#if defined USE_OPENSSL && OPENSSL_VERSION_NUMBER < 0x10100000L
	OpenSSL_add_all_algorithms();
#endif

#if defined SUPPORT_XXH3 || defined USE_OPENSSL
	for (nni = valid_checksums.list; nni->name; nni++)
		verify_digest(nni, True);

	for (nni = valid_auth_checksums.list; nni->name; nni++)
		verify_digest(nni, False);
#endif

	initialized_choices = 1;
}
