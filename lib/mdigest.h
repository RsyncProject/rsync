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

#if defined USE_OPENSSL && !defined USE_MD5_ASM
#define md5_context MD5_CTX
#define md5_begin MD5_Init
#define md5_update MD5_Update
#define md5_result(cptr, digest) MD5_Final(digest, cptr)
#else
#define md5_context md_context
void md5_begin(md_context *ctx);
void md5_update(md_context *ctx, const uchar *input, uint32 length);
void md5_result(md_context *ctx, uchar digest[MD5_DIGEST_LEN]);
#endif
