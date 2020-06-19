/*
 * SSE2/AVX2-optimized routines to process multiple MD5 streams in parallel.
 *
 * Original author: Nicolas Noble, 2017
 * Modifications:   Jorrit Jongma, 2020
 *
 * The original code was released in the public domain by the original author,
 * falling back to the MIT license ( http://www.opensource.org/licenses/MIT )
 * in case public domain does not apply in your country. These modifications
 * are likewise released in the public domain, with the same MIT license
 * fallback.
 *
 * The original publication can be found at:
 *
 * https://github.com/nicolasnoble/sse-hash
 */
/*
 * Nicolas' original code has been extended to add AVX2 support, all non-SIMD
 * MD5 code has been removed and those code paths rerouted to use the MD5
 * code already present in rsync, and wrapper functions have been added. The
 * MD5P8 code is also new, and is the reason for the new stride parameter.
 *
 * This code allows multiple independent MD5 streams to be processed in
 * parallel, 4 with SSE2, 8 with AVX2. While single-stream performance is
 * lower than that of the original C routines for MD5, the processing of
 * additional streams is "for free".
 *
 * Single streams are rerouted to rsync's normal MD5 code as that is faster
 * for that case. A further optimization is possible by using SSE2 code on
 * AVX2-supporting CPUs when the number of streams is 2, 3, or 4. This is not
 * implemented here as it would require some restructuring, and in practise
 * the code here is only rarely called with less than the maximum amount of
 * streams (typically once at the end of each checksum2'd file).
 *
 * Benchmarks (in MB/s)            C     ASM  SSE2*1  SSE2*4  AVX2*1  AVX2*8
 * - Intel Atom D2700            302     334     166     664     N/A     N/A
 * - Intel i7-7700hq             351     376     289    1156     273    2184
 * - AMD ThreadRipper 2950x      728     784     568    2272     430    3440
 */

#ifdef __x86_64__
#ifdef __cplusplus

extern "C" {

#include "rsync.h"

}

#ifdef HAVE_SIMD

#ifndef BENCHMARK_SIMD_CHECKSUM2
#define PMD5_ALLOW_SSE2 // debugging
#define PMD5_ALLOW_AVX2 // debugging
#endif

#ifdef PMD5_ALLOW_AVX2
#ifndef PMD5_ALLOW_SSE2
#define PMD5_ALLOW_SSE2
#endif
#endif

#include <stdint.h>
#include <string.h>

#include <immintrin.h>

/* Some clang versions don't like it when you use static with multi-versioned functions: linker errors */
#ifdef __clang__
#define MVSTATIC
#else
#define MVSTATIC static
#endif

// Missing from the headers on gcc 6 and older, clang 8 and older
typedef long long __m128i_u __attribute__((__vector_size__(16), __may_alias__, __aligned__(1)));
typedef long long __m256i_u __attribute__((__vector_size__(32), __may_alias__, __aligned__(1)));

#define PMD5_SLOTS_DEFAULT 0
#define PMD5_SLOTS_SSE2 4
#define PMD5_SLOTS_AVX2 8
#define PMD5_SLOTS_MAX PMD5_SLOTS_AVX2

#ifdef PMD5_ALLOW_SSE2
__attribute__ ((target("sse2"))) MVSTATIC int pmd5_slots()
{
    return PMD5_SLOTS_SSE2;
}
#endif

#ifdef PMD5_ALLOW_AVX2
__attribute__ ((target("avx2"))) MVSTATIC int pmd5_slots()
{
    return PMD5_SLOTS_AVX2;
}
#endif

__attribute__ ((target("default"))) MVSTATIC int pmd5_slots()
{
    return PMD5_SLOTS_DEFAULT;
}

/* The parallel MD5 context structure. */
typedef struct {
    __m128i state_sse2[4];
    __m256i state_avx2[4];
    uint64_t len[PMD5_SLOTS_MAX];
} pmd5_context;

/* The status returned by the various functions below. */
typedef enum {
    PMD5_SUCCESS,
    PMD5_INVALID_SLOT,
    PMD5_UNALIGNED_UPDATE,
} pmd5_status;

/* Initializes all slots in the given pmd5 context. */
__attribute__ ((target("default"))) MVSTATIC pmd5_status pmd5_init_all(pmd5_context * ctx);

/* Initializes a single slot out in the given pmd5 context. */
static pmd5_status pmd5_init_slot(pmd5_context * ctx, int slot);

/* Makes an MD5 update on all slots in parallel, given the same exact length on all streams.
   The stream pointers will be incremented accordingly.
   It is valid for a stream pointer to be NULL. Garbage will then be hashed into its corresponding slot.
   The argument length NEEDS to be a multiple of 64. If not, an error is returned, and the context is corrupted.
   Stride defaults to 64 if 0 is passed. */
static pmd5_status pmd5_update_all_simple(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX], uint64_t length, uint64_t stride);

/* Makes an MD5 update on all slots in parallel, given different lengths.
   The stream pointers will be incremented accordingly.
   The lengths will be decreased accordingly. Not all data might be consumed.
   It is valid for a stream pointer to be NULL. Garbage will then be hashed into its corresponding slot.
   The argument lengths NEEDS to contain only multiples of 64. If not, an error is returned, and the context is corrupted. */
static pmd5_status pmd5_update_all(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX], uint64_t lengths[PMD5_SLOTS_MAX]);

/* Finishes all slots at once. Fills in all digests. */
static pmd5_status pmd5_finish_all(pmd5_context * ctx, uint8_t digests[PMD5_SLOTS_MAX][MD5_DIGEST_LEN]);

/* Finishes one slot. The other slots will be unnaffected. The finished slot can then continue to hash garbage using
   a NULL pointer as its stream argument, or needs to be reinitialized using pmd5_init_slot before being usable again. */
static pmd5_status pmd5_finish_slot(pmd5_context * ctx, uint8_t digest[MD5_DIGEST_LEN], int slot);

/* Finishes one slot. Extra data is allowed to be passed on as an argument. Length DOESN'T need to be a
   multiple of 64. The other slots will be unaffected. The finished slot can then continue to hash garbage using
   a NULL pointer as its stream argument, or needs to be reinitialized using pmd5_init_slot before being usable again. */
static pmd5_status pmd5_finish_slot_with_extra(pmd5_context * ctx, uint8_t digest[MD5_DIGEST_LEN], int slot, const uint8_t * data, uint64_t length);

/* Insert a normal MD5 context into a given slot of a given parallel MD5 context. */
static pmd5_status md5_to_pmd5(const MD5_CTX * ctx, pmd5_context * pctx, int slot);

/* Extract a normal MD5 context from a given slot of a given parallel MD5 context. */
static pmd5_status pmd5_to_md5(const pmd5_context * pctx, MD5_CTX * ctx, int slot);

#define S11  7
#define S12 12
#define S13 17
#define S14 22
#define S21  5
#define S22  9
#define S23 14
#define S24 20
#define S31  4
#define S32 11
#define S33 16
#define S34 23
#define S41  6
#define S42 10
#define S43 15
#define S44 21

#define T1  0xD76AA478
#define T2  0xE8C7B756
#define T3  0x242070DB
#define T4  0xC1BDCEEE
#define T5  0xF57C0FAF
#define T6  0x4787C62A
#define T7  0xA8304613
#define T8  0xFD469501
#define T9  0x698098D8
#define T10 0x8B44F7AF
#define T11 0xFFFF5BB1
#define T12 0x895CD7BE
#define T13 0x6B901122
#define T14 0xFD987193
#define T15 0xA679438E
#define T16 0x49B40821
#define T17 0xF61E2562
#define T18 0xC040B340
#define T19 0x265E5A51
#define T20 0xE9B6C7AA
#define T21 0xD62F105D
#define T22 0x02441453
#define T23 0xD8A1E681
#define T24 0xE7D3FBC8
#define T25 0x21E1CDE6
#define T26 0xC33707D6
#define T27 0xF4D50D87
#define T28 0x455A14ED
#define T29 0xA9E3E905
#define T30 0xFCEFA3F8
#define T31 0x676F02D9
#define T32 0x8D2A4C8A
#define T33 0xFFFA3942
#define T34 0x8771F681
#define T35 0x6D9D6122
#define T36 0xFDE5380C
#define T37 0xA4BEEA44
#define T38 0x4BDECFA9
#define T39 0xF6BB4B60
#define T40 0xBEBFBC70
#define T41 0x289B7EC6
#define T42 0xEAA127FA
#define T43 0xD4EF3085
#define T44 0x04881D05
#define T45 0xD9D4D039
#define T46 0xE6DB99E5
#define T47 0x1FA27CF8
#define T48 0xC4AC5665
#define T49 0xF4292244
#define T50 0x432AFF97
#define T51 0xAB9423A7
#define T52 0xFC93A039
#define T53 0x655B59C3
#define T54 0x8F0CCC92
#define T55 0xFFEFF47D
#define T56 0x85845DD1
#define T57 0x6FA87E4F
#define T58 0xFE2CE6E0
#define T59 0xA3014314
#define T60 0x4E0811A1
#define T61 0xF7537E82
#define T62 0xBD3AF235
#define T63 0x2AD7D2BB
#define T64 0xEB86D391

#define ROTL_SSE2(x, n) { \
    __m128i s; \
    s = _mm_srli_epi32(x, 32 - n); \
    x = _mm_slli_epi32(x, n); \
    x = _mm_or_si128(x, s); \
};

#define ROTL_AVX2(x, n) { \
    __m256i s; \
    s = _mm256_srli_epi32(x, 32 - n); \
    x = _mm256_slli_epi32(x, n); \
    x = _mm256_or_si256(x, s); \
};

#define F_SSE2(x, y, z) _mm_or_si128(_mm_and_si128(x, y), _mm_andnot_si128(x, z))
#define G_SSE2(x, y, z) _mm_or_si128(_mm_and_si128(x, z), _mm_andnot_si128(z, y))
#define H_SSE2(x, y, z) _mm_xor_si128(_mm_xor_si128(x, y), z)
#define I_SSE2(x, y, z) _mm_xor_si128(y, _mm_or_si128(x, _mm_andnot_si128(z, _mm_set1_epi32(0xffffffff))))

#define F_AVX2(x, y, z) _mm256_or_si256(_mm256_and_si256(x, y), _mm256_andnot_si256(x, z))
#define G_AVX2(x, y, z) _mm256_or_si256(_mm256_and_si256(x, z), _mm256_andnot_si256(z, y))
#define H_AVX2(x, y, z) _mm256_xor_si256(_mm256_xor_si256(x, y), z)
#define I_AVX2(x, y, z) _mm256_xor_si256(y, _mm256_or_si256(x, _mm256_andnot_si256(z, _mm256_set1_epi32(0xffffffff))))

#define SET_SSE2(step, a, b, c, d, x, s, ac) { \
    a = _mm_add_epi32(_mm_add_epi32(a, _mm_add_epi32(x, _mm_set1_epi32(T##ac))), step##_SSE2(b, c, d)); \
    ROTL_SSE2(a, s); \
    a = _mm_add_epi32(a, b); \
}

#define SET_AVX2(step, a, b, c, d, x, s, ac) { \
    a = _mm256_add_epi32(_mm256_add_epi32(a, _mm256_add_epi32(x, _mm256_set1_epi32(T##ac))), step##_AVX2(b, c, d)); \
    ROTL_AVX2(a, s); \
    a = _mm256_add_epi32(a, b); \
}

#define IA 0x67452301
#define IB 0xefcdab89
#define IC 0x98badcfe
#define ID 0x10325476

#define GET_MD5_DATA(dest, src, pos)         \
    dest =                                   \
        ((uint32_t) src[pos + 0]) <<  0 |    \
        ((uint32_t) src[pos + 1]) <<  8 |    \
        ((uint32_t) src[pos + 2]) << 16 |    \
        ((uint32_t) src[pos + 3]) << 24

#define GET_PMD5_DATA_SSE2(dest, src, pos) { \
    uint32_t v0, v1, v2, v3;                 \
    GET_MD5_DATA(v0, src[0], pos);           \
    GET_MD5_DATA(v1, src[1], pos);           \
    GET_MD5_DATA(v2, src[2], pos);           \
    GET_MD5_DATA(v3, src[3], pos);           \
    dest = _mm_setr_epi32(v0, v1, v2, v3);   \
}

#define GET_PMD5_DATA_AVX2(dest, src, pos) { \
    uint32_t v0, v1, v2, v3;                 \
    uint32_t v4, v5, v6, v7;                 \
    GET_MD5_DATA(v0, src[0], pos);           \
    GET_MD5_DATA(v1, src[1], pos);           \
    GET_MD5_DATA(v2, src[2], pos);           \
    GET_MD5_DATA(v3, src[3], pos);           \
    GET_MD5_DATA(v4, src[4], pos);           \
    GET_MD5_DATA(v5, src[5], pos);           \
    GET_MD5_DATA(v6, src[6], pos);           \
    GET_MD5_DATA(v7, src[7], pos);           \
    dest = _mm256_setr_epi32(v0, v1, v2, v3, \
                          v4, v5, v6, v7);   \
}

#define PUT_MD5_DATA(dest, val, pos) {       \
    dest[pos + 0] = (val >>  0) & 0xff;      \
    dest[pos + 1] = (val >>  8) & 0xff;      \
    dest[pos + 2] = (val >> 16) & 0xff;      \
    dest[pos + 3] = (val >> 24) & 0xff;      \
}

const static uint8_t md5_padding[64] = {
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};

#ifdef PMD5_ALLOW_SSE2
__attribute__ ((target("sse2"))) MVSTATIC pmd5_status pmd5_init_all(pmd5_context * ctx)
{
    int i;
    for (i = 0; i < PMD5_SLOTS_MAX; i++) {
        ctx->len[i] = 0;
    }

    ctx->state_sse2[0] = _mm_set1_epi32(IA);
    ctx->state_sse2[1] = _mm_set1_epi32(IB);
    ctx->state_sse2[2] = _mm_set1_epi32(IC);
    ctx->state_sse2[3] = _mm_set1_epi32(ID);

    return PMD5_SUCCESS;
}
#endif

#ifdef PMD5_ALLOW_AVX2
__attribute__ ((target("avx2"))) MVSTATIC pmd5_status pmd5_init_all(pmd5_context * ctx)
{
    int i;
    for (i = 0; i < PMD5_SLOTS_MAX; i++) {
        ctx->len[i] = 0;
    }

    ctx->state_avx2[0] = _mm256_set1_epi32(IA);
    ctx->state_avx2[1] = _mm256_set1_epi32(IB);
    ctx->state_avx2[2] = _mm256_set1_epi32(IC);
    ctx->state_avx2[3] = _mm256_set1_epi32(ID);

    return PMD5_SUCCESS;
}
#endif

__attribute__ ((target("default"))) MVSTATIC pmd5_status pmd5_init_all(pmd5_context * ctx)
{
    return PMD5_INVALID_SLOT;
}

#ifdef PMD5_ALLOW_SSE2
__attribute__ ((target("sse2"))) MVSTATIC pmd5_status pmd5_set_slot(pmd5_context * ctx, int slot, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if ((slot >= PMD5_SLOTS_SSE2) || (slot < 0))
        return PMD5_INVALID_SLOT;

    __attribute__ ((aligned(32))) uint32_t v[4][PMD5_SLOTS_SSE2];
    int i;

    for (i = 0; i < 4; i++) {
        _mm_store_si128((__m128i_u*)v[i], ctx->state_sse2[i]);
    }

    v[0][slot] = a;
    v[1][slot] = b;
    v[2][slot] = c;
    v[3][slot] = d;

    for (i = 0; i < 4; i++) {
        ctx->state_sse2[i] = _mm_loadu_si128((__m128i_u*)v[i]);
    }

    return PMD5_SUCCESS;
}
#endif

#ifdef PMD5_ALLOW_AVX2
__attribute__ ((target("avx2"))) MVSTATIC pmd5_status pmd5_set_slot(pmd5_context * ctx, int slot, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if ((slot >= PMD5_SLOTS_AVX2) || (slot < 0))
        return PMD5_INVALID_SLOT;

    __attribute__ ((aligned(32))) uint32_t v[4][PMD5_SLOTS_AVX2];
    int i;

    for (i = 0; i < 4; i++) {
        _mm256_store_si256((__m256i_u*)v[i], ctx->state_avx2[i]);
    }

    v[0][slot] = a;
    v[1][slot] = b;
    v[2][slot] = c;
    v[3][slot] = d;

    for (i = 0; i < 4; i++) {
        ctx->state_avx2[i] = _mm256_lddqu_si256((__m256i_u*)v[i]);
    }

    return PMD5_SUCCESS;
}
#endif

__attribute__ ((target("default"))) MVSTATIC pmd5_status pmd5_set_slot(pmd5_context * ctx, int slot, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return PMD5_INVALID_SLOT;
}

#ifdef PMD5_ALLOW_SSE2
__attribute__ ((target("sse2"))) MVSTATIC pmd5_status pmd5_get_slot(const pmd5_context * ctx, int slot, uint32_t* a, uint32_t* b, uint32_t* c, uint32_t* d)
{
    if ((slot >= PMD5_SLOTS_SSE2) || (slot < 0))
        return PMD5_INVALID_SLOT;

    __attribute__ ((aligned(32))) uint32_t v[4][PMD5_SLOTS_SSE2];
    int i;

    for (i = 0; i < 4; i++) {
        _mm_store_si128((__m128i_u*)v[i], ctx->state_sse2[i]);
    }

    *a = v[0][slot];
    *b = v[1][slot];
    *c = v[2][slot];
    *d = v[3][slot];

    return PMD5_SUCCESS;
}
#endif

#ifdef PMD5_ALLOW_AVX2
__attribute__ ((target("avx2"))) MVSTATIC pmd5_status pmd5_get_slot(const pmd5_context * ctx, int slot, uint32_t* a, uint32_t* b, uint32_t* c, uint32_t* d)
{
    if ((slot >= PMD5_SLOTS_AVX2) || (slot < 0))
        return PMD5_INVALID_SLOT;

    __attribute__ ((aligned(32))) uint32_t v[4][PMD5_SLOTS_AVX2];
    int i;

    for (i = 0; i < 4; i++) {
        _mm256_store_si256((__m256i_u*)v[i], ctx->state_avx2[i]);
    }

    *a = v[0][slot];
    *b = v[1][slot];
    *c = v[2][slot];
    *d = v[3][slot];

    return PMD5_SUCCESS;
}
#endif

__attribute__ ((target("default"))) MVSTATIC pmd5_status pmd5_get_slot(const pmd5_context * ctx, int slot, uint32_t* a, uint32_t* b, uint32_t* c, uint32_t* d)
{
    return PMD5_INVALID_SLOT;
}

static pmd5_status pmd5_init_slot(pmd5_context * ctx, int slot)
{
    return pmd5_set_slot(ctx, slot, IA, IB, IC, ID);
}

#ifdef PMD5_ALLOW_SSE2
__attribute__ ((target("sse2"))) MVSTATIC void pmd5_process(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX])
{
    __m128i W[MD5_DIGEST_LEN], a, b, c, d;

    GET_PMD5_DATA_SSE2(W[ 0], data,  0);
    GET_PMD5_DATA_SSE2(W[ 1], data,  4);
    GET_PMD5_DATA_SSE2(W[ 2], data,  8);
    GET_PMD5_DATA_SSE2(W[ 3], data, 12);
    GET_PMD5_DATA_SSE2(W[ 4], data, 16);
    GET_PMD5_DATA_SSE2(W[ 5], data, 20);
    GET_PMD5_DATA_SSE2(W[ 6], data, 24);
    GET_PMD5_DATA_SSE2(W[ 7], data, 28);
    GET_PMD5_DATA_SSE2(W[ 8], data, 32);
    GET_PMD5_DATA_SSE2(W[ 9], data, 36);
    GET_PMD5_DATA_SSE2(W[10], data, 40);
    GET_PMD5_DATA_SSE2(W[11], data, 44);
    GET_PMD5_DATA_SSE2(W[12], data, 48);
    GET_PMD5_DATA_SSE2(W[13], data, 52);
    GET_PMD5_DATA_SSE2(W[14], data, 56);
    GET_PMD5_DATA_SSE2(W[15], data, 60);

    a = ctx->state_sse2[0];
    b = ctx->state_sse2[1];
    c = ctx->state_sse2[2];
    d = ctx->state_sse2[3];

    SET_SSE2(F, a, b, c, d, W[ 0], S11,  1);
    SET_SSE2(F, d, a, b, c, W[ 1], S12,  2);
    SET_SSE2(F, c, d, a, b, W[ 2], S13,  3);
    SET_SSE2(F, b, c, d, a, W[ 3], S14,  4);
    SET_SSE2(F, a, b, c, d, W[ 4], S11,  5);
    SET_SSE2(F, d, a, b, c, W[ 5], S12,  6);
    SET_SSE2(F, c, d, a, b, W[ 6], S13,  7);
    SET_SSE2(F, b, c, d, a, W[ 7], S14,  8);
    SET_SSE2(F, a, b, c, d, W[ 8], S11,  9);
    SET_SSE2(F, d, a, b, c, W[ 9], S12, 10);
    SET_SSE2(F, c, d, a, b, W[10], S13, 11);
    SET_SSE2(F, b, c, d, a, W[11], S14, 12);
    SET_SSE2(F, a, b, c, d, W[12], S11, 13);
    SET_SSE2(F, d, a, b, c, W[13], S12, 14);
    SET_SSE2(F, c, d, a, b, W[14], S13, 15);
    SET_SSE2(F, b, c, d, a, W[15], S14, 16);

    SET_SSE2(G, a, b, c, d, W[ 1], S21, 17);
    SET_SSE2(G, d, a, b, c, W[ 6], S22, 18);
    SET_SSE2(G, c, d, a, b, W[11], S23, 19);
    SET_SSE2(G, b, c, d, a, W[ 0], S24, 20);
    SET_SSE2(G, a, b, c, d, W[ 5], S21, 21);
    SET_SSE2(G, d, a, b, c, W[10], S22, 22);
    SET_SSE2(G, c, d, a, b, W[15], S23, 23);
    SET_SSE2(G, b, c, d, a, W[ 4], S24, 24);
    SET_SSE2(G, a, b, c, d, W[ 9], S21, 25);
    SET_SSE2(G, d, a, b, c, W[14], S22, 26);
    SET_SSE2(G, c, d, a, b, W[ 3], S23, 27);
    SET_SSE2(G, b, c, d, a, W[ 8], S24, 28);
    SET_SSE2(G, a, b, c, d, W[13], S21, 29);
    SET_SSE2(G, d, a, b, c, W[ 2], S22, 30);
    SET_SSE2(G, c, d, a, b, W[ 7], S23, 31);
    SET_SSE2(G, b, c, d, a, W[12], S24, 32);

    SET_SSE2(H, a, b, c, d, W[ 5], S31, 33);
    SET_SSE2(H, d, a, b, c, W[ 8], S32, 34);
    SET_SSE2(H, c, d, a, b, W[11], S33, 35);
    SET_SSE2(H, b, c, d, a, W[14], S34, 36);
    SET_SSE2(H, a, b, c, d, W[ 1], S31, 37);
    SET_SSE2(H, d, a, b, c, W[ 4], S32, 38);
    SET_SSE2(H, c, d, a, b, W[ 7], S33, 39);
    SET_SSE2(H, b, c, d, a, W[10], S34, 40);
    SET_SSE2(H, a, b, c, d, W[13], S31, 41);
    SET_SSE2(H, d, a, b, c, W[ 0], S32, 42);
    SET_SSE2(H, c, d, a, b, W[ 3], S33, 43);
    SET_SSE2(H, b, c, d, a, W[ 6], S34, 44);
    SET_SSE2(H, a, b, c, d, W[ 9], S31, 45);
    SET_SSE2(H, d, a, b, c, W[12], S32, 46);
    SET_SSE2(H, c, d, a, b, W[15], S33, 47);
    SET_SSE2(H, b, c, d, a, W[ 2], S34, 48);

    SET_SSE2(I, a, b, c, d, W[ 0], S41, 49);
    SET_SSE2(I, d, a, b, c, W[ 7], S42, 50);
    SET_SSE2(I, c, d, a, b, W[14], S43, 51);
    SET_SSE2(I, b, c, d, a, W[ 5], S44, 52);
    SET_SSE2(I, a, b, c, d, W[12], S41, 53);
    SET_SSE2(I, d, a, b, c, W[ 3], S42, 54);
    SET_SSE2(I, c, d, a, b, W[10], S43, 55);
    SET_SSE2(I, b, c, d, a, W[ 1], S44, 56);
    SET_SSE2(I, a, b, c, d, W[ 8], S41, 57);
    SET_SSE2(I, d, a, b, c, W[15], S42, 58);
    SET_SSE2(I, c, d, a, b, W[ 6], S43, 59);
    SET_SSE2(I, b, c, d, a, W[13], S44, 60);
    SET_SSE2(I, a, b, c, d, W[ 4], S41, 61);
    SET_SSE2(I, d, a, b, c, W[11], S42, 62);
    SET_SSE2(I, c, d, a, b, W[ 2], S43, 63);
    SET_SSE2(I, b, c, d, a, W[ 9], S44, 64);

    ctx->state_sse2[0] = _mm_add_epi32(ctx->state_sse2[0], a);
    ctx->state_sse2[1] = _mm_add_epi32(ctx->state_sse2[1], b);
    ctx->state_sse2[2] = _mm_add_epi32(ctx->state_sse2[2], c);
    ctx->state_sse2[3] = _mm_add_epi32(ctx->state_sse2[3], d);
}
#endif

#ifdef PMD5_ALLOW_AVX2
__attribute__ ((target("avx2"))) MVSTATIC void pmd5_process(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX])
{
    __m256i W[MD5_DIGEST_LEN], a, b, c, d;

    GET_PMD5_DATA_AVX2(W[ 0], data,  0);
    GET_PMD5_DATA_AVX2(W[ 1], data,  4);
    GET_PMD5_DATA_AVX2(W[ 2], data,  8);
    GET_PMD5_DATA_AVX2(W[ 3], data, 12);
    GET_PMD5_DATA_AVX2(W[ 4], data, 16);
    GET_PMD5_DATA_AVX2(W[ 5], data, 20);
    GET_PMD5_DATA_AVX2(W[ 6], data, 24);
    GET_PMD5_DATA_AVX2(W[ 7], data, 28);
    GET_PMD5_DATA_AVX2(W[ 8], data, 32);
    GET_PMD5_DATA_AVX2(W[ 9], data, 36);
    GET_PMD5_DATA_AVX2(W[10], data, 40);
    GET_PMD5_DATA_AVX2(W[11], data, 44);
    GET_PMD5_DATA_AVX2(W[12], data, 48);
    GET_PMD5_DATA_AVX2(W[13], data, 52);
    GET_PMD5_DATA_AVX2(W[14], data, 56);
    GET_PMD5_DATA_AVX2(W[15], data, 60);

    a = ctx->state_avx2[0];
    b = ctx->state_avx2[1];
    c = ctx->state_avx2[2];
    d = ctx->state_avx2[3];

    SET_AVX2(F, a, b, c, d, W[ 0], S11,  1);
    SET_AVX2(F, d, a, b, c, W[ 1], S12,  2);
    SET_AVX2(F, c, d, a, b, W[ 2], S13,  3);
    SET_AVX2(F, b, c, d, a, W[ 3], S14,  4);
    SET_AVX2(F, a, b, c, d, W[ 4], S11,  5);
    SET_AVX2(F, d, a, b, c, W[ 5], S12,  6);
    SET_AVX2(F, c, d, a, b, W[ 6], S13,  7);
    SET_AVX2(F, b, c, d, a, W[ 7], S14,  8);
    SET_AVX2(F, a, b, c, d, W[ 8], S11,  9);
    SET_AVX2(F, d, a, b, c, W[ 9], S12, 10);
    SET_AVX2(F, c, d, a, b, W[10], S13, 11);
    SET_AVX2(F, b, c, d, a, W[11], S14, 12);
    SET_AVX2(F, a, b, c, d, W[12], S11, 13);
    SET_AVX2(F, d, a, b, c, W[13], S12, 14);
    SET_AVX2(F, c, d, a, b, W[14], S13, 15);
    SET_AVX2(F, b, c, d, a, W[15], S14, 16);

    SET_AVX2(G, a, b, c, d, W[ 1], S21, 17);
    SET_AVX2(G, d, a, b, c, W[ 6], S22, 18);
    SET_AVX2(G, c, d, a, b, W[11], S23, 19);
    SET_AVX2(G, b, c, d, a, W[ 0], S24, 20);
    SET_AVX2(G, a, b, c, d, W[ 5], S21, 21);
    SET_AVX2(G, d, a, b, c, W[10], S22, 22);
    SET_AVX2(G, c, d, a, b, W[15], S23, 23);
    SET_AVX2(G, b, c, d, a, W[ 4], S24, 24);
    SET_AVX2(G, a, b, c, d, W[ 9], S21, 25);
    SET_AVX2(G, d, a, b, c, W[14], S22, 26);
    SET_AVX2(G, c, d, a, b, W[ 3], S23, 27);
    SET_AVX2(G, b, c, d, a, W[ 8], S24, 28);
    SET_AVX2(G, a, b, c, d, W[13], S21, 29);
    SET_AVX2(G, d, a, b, c, W[ 2], S22, 30);
    SET_AVX2(G, c, d, a, b, W[ 7], S23, 31);
    SET_AVX2(G, b, c, d, a, W[12], S24, 32);

    SET_AVX2(H, a, b, c, d, W[ 5], S31, 33);
    SET_AVX2(H, d, a, b, c, W[ 8], S32, 34);
    SET_AVX2(H, c, d, a, b, W[11], S33, 35);
    SET_AVX2(H, b, c, d, a, W[14], S34, 36);
    SET_AVX2(H, a, b, c, d, W[ 1], S31, 37);
    SET_AVX2(H, d, a, b, c, W[ 4], S32, 38);
    SET_AVX2(H, c, d, a, b, W[ 7], S33, 39);
    SET_AVX2(H, b, c, d, a, W[10], S34, 40);
    SET_AVX2(H, a, b, c, d, W[13], S31, 41);
    SET_AVX2(H, d, a, b, c, W[ 0], S32, 42);
    SET_AVX2(H, c, d, a, b, W[ 3], S33, 43);
    SET_AVX2(H, b, c, d, a, W[ 6], S34, 44);
    SET_AVX2(H, a, b, c, d, W[ 9], S31, 45);
    SET_AVX2(H, d, a, b, c, W[12], S32, 46);
    SET_AVX2(H, c, d, a, b, W[15], S33, 47);
    SET_AVX2(H, b, c, d, a, W[ 2], S34, 48);

    SET_AVX2(I, a, b, c, d, W[ 0], S41, 49);
    SET_AVX2(I, d, a, b, c, W[ 7], S42, 50);
    SET_AVX2(I, c, d, a, b, W[14], S43, 51);
    SET_AVX2(I, b, c, d, a, W[ 5], S44, 52);
    SET_AVX2(I, a, b, c, d, W[12], S41, 53);
    SET_AVX2(I, d, a, b, c, W[ 3], S42, 54);
    SET_AVX2(I, c, d, a, b, W[10], S43, 55);
    SET_AVX2(I, b, c, d, a, W[ 1], S44, 56);
    SET_AVX2(I, a, b, c, d, W[ 8], S41, 57);
    SET_AVX2(I, d, a, b, c, W[15], S42, 58);
    SET_AVX2(I, c, d, a, b, W[ 6], S43, 59);
    SET_AVX2(I, b, c, d, a, W[13], S44, 60);
    SET_AVX2(I, a, b, c, d, W[ 4], S41, 61);
    SET_AVX2(I, d, a, b, c, W[11], S42, 62);
    SET_AVX2(I, c, d, a, b, W[ 2], S43, 63);
    SET_AVX2(I, b, c, d, a, W[ 9], S44, 64);

    ctx->state_avx2[0] = _mm256_add_epi32(ctx->state_avx2[0], a);
    ctx->state_avx2[1] = _mm256_add_epi32(ctx->state_avx2[1], b);
    ctx->state_avx2[2] = _mm256_add_epi32(ctx->state_avx2[2], c);
    ctx->state_avx2[3] = _mm256_add_epi32(ctx->state_avx2[3], d);
}
#endif

__attribute__ ((target("default"))) MVSTATIC void pmd5_process(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX])
{
}

static pmd5_status pmd5_update_all_simple(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX], uint64_t length, uint64_t stride)
{
    const uint8_t * ptrs[PMD5_SLOTS_MAX];

    if (!length) return PMD5_SUCCESS;

    int slots = pmd5_slots();

    if (!stride) stride = 64;

    int i;
    for (i = 0; i < slots; i++) {
        ptrs[i] = data[i];
        ctx->len[i] += length;
        if (!ptrs[i]) ptrs[i] = md5_padding;
    }

    while (length >= 64) {
        pmd5_process(ctx, ptrs);
        length -= 64;
        for (i = 0; i < slots; i++) {
            if (data[i]) ptrs[i] += stride;
        }
    }

    if (length) return PMD5_UNALIGNED_UPDATE;

    for (i = 0; i < slots; i++) {
        if (data[i]) data[i] = ptrs[i];
    }

    return PMD5_SUCCESS;
}

static pmd5_status pmd5_update_all(pmd5_context * ctx, const uint8_t * data[PMD5_SLOTS_MAX], uint64_t lengths[PMD5_SLOTS_MAX])
{
    uint64_t length = 0;
    int slots = pmd5_slots();

    int i;
    for (i = 0; i < slots; i++) {
        if ((length == 0) || (lengths[i] < length)) length = lengths[i];
    }

    for (i = 0; i < slots; i++) {
        lengths[i] -= length;
    }

    return pmd5_update_all_simple(ctx, data, length, 0);
}

static pmd5_status pmd5_finish_slot_with_extra(pmd5_context * pctx, uint8_t digest[MD5_DIGEST_LEN], int slot, const uint8_t * data, uint64_t length)
{
    MD5_CTX ctx;

    if ((slot >= pmd5_slots()) || (slot < 0))
        return PMD5_INVALID_SLOT;

    pmd5_to_md5(pctx, &ctx, slot);
    if (data && length) {
        MD5_Update(&ctx, data, length);
    }
    MD5_Final(digest, &ctx);

    return PMD5_SUCCESS;
}

static pmd5_status pmd5_finish_slot(pmd5_context * pctx, uint8_t digest[MD5_DIGEST_LEN], int slot)
{
    return pmd5_finish_slot_with_extra(pctx, digest, slot, NULL, 0);
}

static pmd5_status pmd5_finish_all(pmd5_context * ctx, uint8_t digests[PMD5_SLOTS_MAX][MD5_DIGEST_LEN])
{
    int i;
    for (i = 0; i < pmd5_slots(); i++) {
        pmd5_finish_slot_with_extra(ctx, digests[i], i, NULL, 0);
    }
    return PMD5_SUCCESS;
}

static pmd5_status md5_to_pmd5(const MD5_CTX * ctx, pmd5_context * pctx, int slot)
{
    if ((slot >= pmd5_slots()) || (slot < 0))
        return PMD5_INVALID_SLOT;

    // TODO This function ignores buffered but as of yet unhashed data. We're not using this function, just noting.

#ifdef USE_OPENSSL
    pctx->len[slot] = (ctx->Nl >> 3) + ((uint64_t)ctx->Nh << 29);
#else
    pctx->len[slot] = ctx->totalN + ((uint64_t)ctx->totalN2 << 32);
#endif
    return pmd5_set_slot(pctx, slot, (uint32_t)ctx->A, (uint32_t)ctx->B, (uint32_t)ctx->C, (uint32_t)ctx->D);
}

static pmd5_status pmd5_to_md5(const pmd5_context * pctx, MD5_CTX * ctx, int slot)
{
    if ((slot >= pmd5_slots()) || (slot < 0))
        return PMD5_INVALID_SLOT;

    MD5_Init(ctx);

#ifdef USE_OPENSSL
    ctx->Nl = (pctx->len[slot] << 3) & 0xFFFFFFFF;
    ctx->Nh = pctx->len[slot] >> 29;

    uint32_t a, b, c, d;
    pmd5_status ret = pmd5_get_slot(pctx, slot, &a, &b, &c, &d);
    if (ret == PMD5_SUCCESS) {
        ctx->A = a;
        ctx->B = b;
        ctx->C = c;
        ctx->D = d;
    }
    return ret;
#else
    ctx->totalN = pctx->len[slot] & 0xFFFFFFFF;
    ctx->totalN2 = pctx->len[slot] >> 32;
    return pmd5_get_slot(pctx, slot, &ctx->A, &ctx->B, &ctx->C, &ctx->D);
#endif
}

/* With GCC 10 putting these implementations inside 'extern "C"' causes an
   assembler error. That worked fine on GCC 5-9 and clang 6-10...
  */

static inline int md5_parallel_slots_cpp()
{
    int slots = pmd5_slots();
    if (slots == 0) return 1;
    return slots;
}

static inline int md5_parallel_cpp(int streams, char** buf, int* len, char** sum, char* pre4, char* post4)
{
    int slots = md5_parallel_slots_cpp();
    if ((streams < 1) || (streams > slots)) return 0;
    if (pre4 && post4) return 0;

    if (slots == 1) {
        MD5_CTX ctx;
        MD5_Init(&ctx);
        if (pre4) {
            MD5_Update(&ctx, (const unsigned char*)pre4, 4);
        }
        MD5_Update(&ctx, (const unsigned char*)buf[0], len[0]);
        if (post4) {
            MD5_Update(&ctx, (const unsigned char*)post4, 4);
        }
        if (sum[0]) {
            MD5_Final((uint8_t*)sum[0], &ctx);
        }
        return 0;
    }

    int i;
    int active[PMD5_SLOTS_MAX];
    char* buffers[PMD5_SLOTS_MAX];
    uint64_t left[PMD5_SLOTS_MAX];
    for (i = 0; i < PMD5_SLOTS_MAX; i++) {
        active[i] = streams > i;
        if (i < streams) {
            buffers[i] = buf[i];
            left[i] = (uint64_t)len[i];
        } else {
            buffers[i] = NULL;
            left[i] = 0;
        }
    }
    MD5_CTX results[PMD5_SLOTS_MAX];

    pmd5_context ctx_simd;
    if (pmd5_init_all(&ctx_simd) != PMD5_SUCCESS) return 0;

    if (pre4) {
        char temp_buffers[PMD5_SLOTS_MAX][64];
        int have_any = 0;
        for (i = 0; i < slots; i++) {
            if (active[i]) {
                if (left[i] < 60) {
                    MD5_Init(&results[i]);
                    MD5_Update(&results[i], (const unsigned char*)pre4, 4);
                    MD5_Update(&results[i], (const unsigned char*)buf[i], left[i]);
                    active[i] = 0;
                    left[i] = 0;
                } else {
                    memcpy(temp_buffers[i], pre4, 4);
                    memcpy(temp_buffers[i] + 4, buffers[i], 60);
                    buffers[i] += 60;
                    left[i] -= 60;
                    have_any = 1;
                }
            }
        }

        if (have_any) {
            char* ptrs[PMD5_SLOTS_MAX];
            for (i = 0; i < PMD5_SLOTS_MAX; i++) {
                ptrs[i] = &temp_buffers[i][0];
            }
            if (pmd5_update_all_simple(&ctx_simd, (const uint8_t**)ptrs, 64, 0) != PMD5_SUCCESS) {
                return 0;
            }
        }
    }

    int failed = 0;
    while (true) {
        for (i = 0; i < slots; i++) {
            if (active[i] && (left[i] < 64)) {
                if (pmd5_to_md5(&ctx_simd, &results[i], i) != PMD5_SUCCESS) {
                    failed = 1;
                }
                active[i] = 0;
            }
        }

        uint64_t shortest = 0;
        for (i = 0; i < slots; i++) {
            if (!active[i]) {
                buffers[i] = NULL;
            } else if ((shortest == 0) || (left[i] < shortest)) {
                shortest = left[i];
            }
        }

        if (shortest > 0) {
            shortest = shortest & ~63;
            if (pmd5_update_all_simple(&ctx_simd, (const uint8_t**)buffers, shortest, 0) != PMD5_SUCCESS) {
                failed = 1;
            }
            for (i = 0; i < slots; i++) {
                if (active[i]) {
                    left[i] -= shortest;
                }
            }
        }

        if (failed) {
            return 0;
        } else {
            int have_any = 0;
            for (i = 0; i < slots; i++) {
                have_any |= active[i];
            }
            if (!have_any) {
                break;
            }
        }
    }

    for (i = 0; i < slots; i++) {
        if (i < streams) {
            if (left[i] > 0) {
                // buffer[i] == NULL here
                MD5_Update(&results[i], (const unsigned char*)buf[i] + len[i] - left[i], left[i]);
            }
            if (post4) {
                MD5_Update(&results[i], (const unsigned char*)post4, 4);
            }
            if (sum[i]) {
                MD5_Final((uint8_t*)sum[i], &results[i]);
            }
        }
    }

    return 1;
}

// each pmd5_context needs to be 32-byte aligned
#define MD5P8_Contexts_simd(ctx, index) ((pmd5_context*)((((uintptr_t)((ctx)->context_storage) + 31) & ~31) + (index)*((sizeof(pmd5_context) + 31) & ~31)))

static inline void MD5P8_Init_cpp(MD5P8_CTX *ctx)
{
    int i;
    for (i = 0; i < (pmd5_slots() == PMD5_SLOTS_AVX2 ? 1 : 2); i++) {
        pmd5_init_all(MD5P8_Contexts_simd(ctx, i));
    }
    ctx->used = 0;
    ctx->next = 0;
}

static inline void MD5P8_Update_cpp(MD5P8_CTX *ctx, const uchar *input, uint32 length)
{
    int slots = pmd5_slots();
    uint32 pos = 0;

    if ((ctx->used) || (length < 512)) {
        int cpy = MIN(length, 512 - ctx->used);
        memcpy(&ctx->buffer[ctx->used], input, cpy);
        ctx->used += cpy;
        length -= cpy;
        pos += cpy;

        if (ctx->used == 512) {
            if (slots == PMD5_SLOTS_AVX2) {
                const uint8_t* ptrs[PMD5_SLOTS_MAX] = {
                    (uint8_t*)ctx->buffer,
                    (uint8_t*)(ctx->buffer + 64),
                    (uint8_t*)(ctx->buffer + 128),
                    (uint8_t*)(ctx->buffer + 192),
                    (uint8_t*)(ctx->buffer + 256),
                    (uint8_t*)(ctx->buffer + 320),
                    (uint8_t*)(ctx->buffer + 384),
                    (uint8_t*)(ctx->buffer + 448)
                };
                pmd5_update_all_simple(MD5P8_Contexts_simd(ctx, 0), ptrs, 64, 0);
            } else {
                const uint8_t* ptrs1[PMD5_SLOTS_MAX] = {
                    (uint8_t*)ctx->buffer,
                    (uint8_t*)(ctx->buffer + 64),
                    (uint8_t*)(ctx->buffer + 128),
                    (uint8_t*)(ctx->buffer + 192)
                };
                const uint8_t* ptrs2[PMD5_SLOTS_MAX] = {
                    (uint8_t*)(ctx->buffer + 256),
                    (uint8_t*)(ctx->buffer + 320),
                    (uint8_t*)(ctx->buffer + 384),
                    (uint8_t*)(ctx->buffer + 448)
                };
                pmd5_update_all_simple(MD5P8_Contexts_simd(ctx, 0), ptrs1, 64, 0);
                pmd5_update_all_simple(MD5P8_Contexts_simd(ctx, 1), ptrs2, 64, 0);
            }
            ctx->used = 0;
        }
    }

    if (length >= 512) {
        uint32 blocks = length / 512;
        if (slots == PMD5_SLOTS_AVX2) {
            const uint8_t* ptrs[8] = {
                (uint8_t*)(input + pos),
                (uint8_t*)(input + pos + 64),
                (uint8_t*)(input + pos + 128),
                (uint8_t*)(input + pos + 192),
                (uint8_t*)(input + pos + 256),
                (uint8_t*)(input + pos + 320),
                (uint8_t*)(input + pos + 384),
                (uint8_t*)(input + pos + 448)
            };
            pmd5_update_all_simple(MD5P8_Contexts_simd(ctx, 0), ptrs, blocks * 64, 512);
        } else {
            const uint8_t* ptrs1[4] = {
                (uint8_t*)(input + pos),
                (uint8_t*)(input + pos + 64),
                (uint8_t*)(input + pos + 128),
                (uint8_t*)(input + pos + 192)
            };
            const uint8_t* ptrs2[4] = {
                (uint8_t*)(input + pos + 256),
                (uint8_t*)(input + pos + 320),
                (uint8_t*)(input + pos + 384),
                (uint8_t*)(input + pos + 448)
            };
            pmd5_update_all_simple(MD5P8_Contexts_simd(ctx, 0), ptrs1, blocks * 64, 512);
            pmd5_update_all_simple(MD5P8_Contexts_simd(ctx, 1), ptrs2, blocks * 64, 512);
        }
        pos += blocks * 512;
        length -= blocks * 512;
    }

    if (length) {
        memcpy(ctx->buffer, &input[pos], length);
        ctx->used = length;
    }
}

static inline void MD5P8_Final_cpp(uchar digest[MD5_DIGEST_LEN], MD5P8_CTX *ctx)
{
    int i;
    uint32 low = 0, high = 0, sub = ctx->used ? 512 - ctx->used : 0;
    if (ctx->used) {
        uchar tmp[512];
        memset(tmp, 0, 512);
        MD5P8_Update(ctx, tmp, 512 - ctx->used);
    }

    uchar state[34*4] = {0};

    MD5_CTX tmp;
    for (i = 0; i < 8; i++) {
        if (pmd5_slots() == PMD5_SLOTS_AVX2) {
            pmd5_to_md5(MD5P8_Contexts_simd(ctx, 0), &tmp, i);
        } else if (i < 4) {
            pmd5_to_md5(MD5P8_Contexts_simd(ctx, 0), &tmp, i);
        } else {
            pmd5_to_md5(MD5P8_Contexts_simd(ctx, 1), &tmp, i - 4);
        }
#ifdef USE_OPENSSL
        if (low + tmp.Nl < low) high++;
        low += tmp.Nl;
        high += tmp.Nh;
#else
        if (low + tmp.totalN < low) high++;
        low += tmp.totalN;
        high += tmp.totalN2;
#endif
        SIVALu(state, i*16, tmp.A);
        SIVALu(state, i*16 + 4, tmp.B);
        SIVALu(state, i*16 + 8, tmp.C);
        SIVALu(state, i*16 + 12, tmp.D);
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

extern "C" {

int md5_parallel_slots()
{
    return md5_parallel_slots_cpp();
}

int md5_parallel(int streams, char** buf, int* len, char** sum, char* pre4, char* post4)
{
    return md5_parallel_cpp(streams, buf, len, sum, pre4, post4);
}

void MD5P8_Init(MD5P8_CTX *ctx)
{
    MD5P8_Init_cpp(ctx);
}

void MD5P8_Update(MD5P8_CTX *ctx, const uchar *input, uint32 length)
{
    MD5P8_Update_cpp(ctx, input, length);
}

void MD5P8_Final(uchar digest[MD5_DIGEST_LEN], MD5P8_CTX *ctx)
{
    MD5P8_Final_cpp(digest, ctx);
}

} // "C"

#endif /* HAVE_SIMD */
#endif /* __cplusplus */
#endif /* __x86_64__ */
