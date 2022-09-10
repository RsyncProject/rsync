/* Keep this simple so both C and ASM can use it */

/* These allow something like CFLAGS=-DDISABLE_SHA512_DIGEST */
#ifdef DISABLE_SHA256_DIGEST
#undef SHA256_DIGEST_LENGTH
#endif
#ifdef DISABLE_SHA512_DIGEST
#undef SHA512_DIGEST_LENGTH
#endif

#define MD4_DIGEST_LEN 16
#define MD5_DIGEST_LEN 16
#if defined SHA512_DIGEST_LENGTH
#define MAX_DIGEST_LEN SHA512_DIGEST_LENGTH
#elif defined SHA256_DIGEST_LENGTH
#define MAX_DIGEST_LEN SHA256_DIGEST_LENGTH
#elif defined SHA_DIGEST_LENGTH
#define MAX_DIGEST_LEN SHA_DIGEST_LENGTH
#else
#define MAX_DIGEST_LEN MD5_DIGEST_LEN
#endif

#define CSUM_CHUNK 64

#define CSUM_gone -1
#define CSUM_NONE 0
#define CSUM_MD4_ARCHAIC 1
#define CSUM_MD4_BUSTED 2
#define CSUM_MD4_OLD 3
#define CSUM_MD4 4
#define CSUM_MD5 5
#define CSUM_XXH64 6
#define CSUM_XXH3_64 7
#define CSUM_XXH3_128 8
#define CSUM_SHA1 9
#define CSUM_SHA256 10
#define CSUM_SHA512 11
