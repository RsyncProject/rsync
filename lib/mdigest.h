/* The include file for both the MD4 and MD5 routines. */

#ifdef USE_OPENSSL
#include "openssl/md4.h"
#include "openssl/md5.h"
#endif
#include "md-defines.h"

typedef struct {
	uint32 A, B, C, D;
	uint32 totalN;          /* bit count, lower 32 bits */
	uint32 totalN2;         /* bit count, upper 32 bits */
	uchar buffer[CSUM_CHUNK];
} md_context;

void mdfour_begin(md_context *md);
void mdfour_update(md_context *md, const uchar *in, uint32 length);
void mdfour_result(md_context *md, uchar digest[MD4_DIGEST_LEN]);

#ifndef USE_OPENSSL
#define MD5_CTX md_context
#define MD5_Init md5_begin
#define MD5_Update md5_update
#define MD5_Final(digest, cptr) md5_result(cptr, digest)

void md5_begin(md_context *ctx);
void md5_update(md_context *ctx, const uchar *input, uint32 length);
void md5_result(md_context *ctx, uchar digest[MD5_DIGEST_LEN]);
#endif
