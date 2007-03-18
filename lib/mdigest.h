/* The include file for both the MD4 and MD5 routines. */

#define MD4_DIGEST_LEN 16
#define MD5_DIGEST_LEN 16
#define MAX_DIGEST_LEN MD5_DIGEST_LEN

#define CSUM_CHUNK 64

typedef struct {
	uint32 A, B, C, D;
	uint32 totalN;          /* bit count, lower 32 bits */
	uint32 totalN2;         /* bit count, upper 32 bits */
	uchar buffer[CSUM_CHUNK];
} md_context;

void mdfour_begin(md_context *md);
void mdfour_update(md_context *md, const uchar *in, uint32 length);
void mdfour_result(md_context *md, uchar digest[MD4_DIGEST_LEN]);

void get_mdfour(uchar digest[MD4_DIGEST_LEN], const uchar *in, int length);

void md5_begin(md_context *ctx);
void md5_update(md_context *ctx, const uchar *input, uint32 length);
void md5_result(md_context *ctx, uchar digest[MD5_DIGEST_LEN]);

void get_md5(uchar digest[MD5_DIGEST_LEN], const uchar *input, int n);
