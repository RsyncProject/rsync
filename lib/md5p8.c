/*
 * MD5-based hash friendly to parallel processing, reference implementation
 *
 * Author: Jorrit Jongma, 2020
 *
 * Released in the public domain falling back to the MIT license
 * ( http://www.opensource.org/licenses/MIT ) in case public domain does not
 * apply in your country.
 */
/*
 * MD5P8 is an MD5-based hash friendly to parallel processing. The input
 * stream is divided into 8 independent streams. For each 512 bytes of input,
 * the first 64 bytes are send to the first stream, the second 64 bytes to
 * the second stream, etc. The input stream is padded with zeros to the next
 * multiple of 512 bytes, then a normal MD5 hash is computed on a buffer
 * containing the A, B, C, and D states of the 8 individual streams, followed
 * by the (unpadded) length of the input.
 *
 * On non-SIMD accelerated CPUs the performance of MD5P8 is slightly lower
 * than normal MD5 (particularly on files smaller than 10 kB), but with
 * SIMD-based parallel processing it can be two to six times as fast. Even in
 * the best-case scenario, xxHash is still at least twice as fast and should
 * be preferred when available.
 */

#include "rsync.h"

#ifdef HAVE_SIMD
#define MD5P8_Init MD5P8_Init_c
#define MD5P8_Update MD5P8_Update_c
#define MD5P8_Final MD5P8_Final_c
#endif

/* each MD5_CTX needs to be 8-byte aligned */
#define MD5P8_Contexts_c(ctx, index) ((MD5_CTX*)((((uintptr_t)((ctx)->context_storage) + 7) & ~7) + (index)*((sizeof(MD5_CTX) + 7) & ~7)))

void MD5P8_Init(MD5P8_CTX *ctx)
{
    int i;
    for (i = 0; i < 8; i++) {
        MD5_Init(MD5P8_Contexts_c(ctx, i));
    }
    ctx->used = 0;
    ctx->next = 0;
}

void MD5P8_Update(MD5P8_CTX *ctx, const uchar *input, uint32 length)
{
    uint32 pos = 0;

    if ((ctx->used) || (length < 64)) {
        int cpy = MIN(length, 64 - ctx->used);
        memmove(&ctx->buffer[ctx->used], input, cpy);
        ctx->used += cpy;
        length -= cpy;
        pos += cpy;

        if (ctx->used == 64) {
            MD5_Update(MD5P8_Contexts_c(ctx, ctx->next), ctx->buffer, 64);
            ctx->used = 0;
            ctx->next = (ctx->next + 1) % 8;
        }
    }

    while (length >= 64) {
        MD5_Update(MD5P8_Contexts_c(ctx, ctx->next), &input[pos], 64);
        ctx->next = (ctx->next + 1) % 8;
        pos += 64;
        length -= 64;
    }

    if (length) {
        memcpy(ctx->buffer, &input[pos], length);
        ctx->used = length;
    }
}

void MD5P8_Final(uchar digest[MD5_DIGEST_LEN], MD5P8_CTX *ctx)
{
    int i;
    uint32 low = 0, high = 0, sub = ctx->used ? 64 - ctx->used : 0;
    if (ctx->used) {
        uchar tmp[64];
        memset(tmp, 0, 64);
        MD5P8_Update(ctx, tmp, 64 - ctx->used);
    }
    memset(ctx->buffer, 0, 64);
    while (ctx->next != 0) {
        MD5P8_Update(ctx, ctx->buffer, 64);
        sub += 64;
    }

    uchar state[34*4] = {0};

    for (i = 0; i < 8; i++) {
        MD5_CTX* md = MD5P8_Contexts_c(ctx, i);
#ifdef USE_OPENSSL
        if (low + md->Nl < low) high++;
        low += md->Nl;
        high += md->Nh;
#else
        if (low + md->totalN < low) high++;
        low += md->totalN;
        high += md->totalN2;
#endif
        SIVALu(state, i*16, md->A);
        SIVALu(state, i*16 + 4, md->B);
        SIVALu(state, i*16 + 8, md->C);
        SIVALu(state, i*16 + 12, md->D);
    }

#ifndef USE_OPENSSL
	high = (low >> 29) | (high << 3);
	low = (low << 3);
#endif

    sub <<= 3;
    if (low - sub > low) high--;
    low -= sub;

    SIVALu(state, 32*4, low);
    SIVALu(state, 33*4, high);

    MD5_CTX md;
    MD5_Init(&md);
    MD5_Update(&md, state, 34*4);
    MD5_Final(digest, &md);
}
