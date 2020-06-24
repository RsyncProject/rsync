/* Keep this simple so both C and ASM can use it */

#define MD4_DIGEST_LEN 16
#define MD5_DIGEST_LEN 16
#define MAX_DIGEST_LEN MD5_DIGEST_LEN

#define CSUM_CHUNK 64

#define CSUM_NONE 0
#define CSUM_MD4_ARCHAIC 1
#define CSUM_MD4_BUSTED 2
#define CSUM_MD4_OLD 3
#define CSUM_MD4 4
#define CSUM_MD5 5
#define CSUM_XXH64 6
#define CSUM_XXH3_64 7
#define CSUM_XXH3_128 8
