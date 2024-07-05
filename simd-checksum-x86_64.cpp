/*
 * SSE2/SSSE3/AVX2-optimized routines to support checksumming of bytes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2020 Wayne Davison
 * Copyright (C) 2020 Jorrit Jongma
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
/*
 * Optimization target for get_checksum1() was the Intel Atom D2700, the
 * slowest CPU in the test set and the most likely to be CPU limited during
 * transfers. The combination of intrinsics was chosen specifically for the
 * most gain on that CPU, other combinations were occasionally slightly
 * faster on the others.
 *
 * While on more modern CPUs transfers are less likely to be CPU limited
 * (at least by this specific function), lower CPU usage is always better.
 * Improvements may still be seen when matching chunks from NVMe storage
 * even on newer CPUs.
 *
 * Benchmarks (in MB/s)            C    SSE2   SSSE3    AVX2
 * - Intel Atom D2700            550     750    1000     N/A
 * - Intel i7-7700hq            1850    2550    4050    6200
 * - AMD ThreadRipper 2950x     2900    5600    8950    8100
 *
 * Curiously the AMD is slower with AVX2 than SSSE3, while the Intel is
 * significantly faster. AVX2 is kept because it's more likely to relieve
 * the bottleneck on the slower CPU.
 *
 * This optimization for get_checksum1() is intentionally limited to x86-64
 * as no 32-bit CPU was available for testing. As 32-bit CPUs only have half
 * the available xmm registers, this optimized version may not be faster than
 * the pure C version anyway. Note that all x86-64 CPUs support at least SSE2.
 *
 * This file is compiled using GCC 4.8+/clang 6+'s C++ front end to allow the
 * use of the target attribute, selecting the fastest code path based on
 * dispatch priority (GCC 5) or runtime detection of CPU capabilities (GCC 6+).
 * GCC 4.x are not supported to ease configure.ac logic.
 */

#ifdef __x86_64__ /* { */
#ifdef __cplusplus /* { */

#include "rsync.h"

#ifdef USE_ROLL_SIMD /* { */

#include <immintrin.h>

/* Some clang versions don't like it when you use static with multi-versioned functions: linker errors */
#ifdef __clang__
#define MVSTATIC
#else
#define MVSTATIC static
#endif

// Missing from the headers on gcc 6 and older, clang 8 and older
typedef long long __m128i_u __attribute__((__vector_size__(16), __may_alias__, __aligned__(16)));
typedef long long __m256i_u __attribute__((__vector_size__(32), __may_alias__, __aligned__(16)));

/* Compatibility macros to let our SSSE3 algorithm run with only SSE2.
   These used to be neat individual functions with target attributes switching between SSE2 and SSSE3 implementations
   as needed, but though this works perfectly with GCC, clang fails to inline those properly leading to a near 50%
   performance drop - combined with static and inline modifiers gets you linker errors and even compiler crashes...
*/

#define SSE2_INTERLEAVE_ODD_EPI16(a, b) _mm_packs_epi32(_mm_srai_epi32(a, 16), _mm_srai_epi32(b, 16))
#define SSE2_INTERLEAVE_EVEN_EPI16(a, b) SSE2_INTERLEAVE_ODD_EPI16(_mm_slli_si128(a, 2), _mm_slli_si128(b, 2))
#define SSE2_MULU_ODD_EPI8(a, b) _mm_mullo_epi16(_mm_srli_epi16(a, 8), _mm_srai_epi16(b, 8))
#define SSE2_MULU_EVEN_EPI8(a, b) _mm_mullo_epi16(_mm_and_si128(a, _mm_set1_epi16(0xFF)), _mm_srai_epi16(_mm_slli_si128(b, 1), 8))

#define SSE2_HADDS_EPI16(a, b) _mm_adds_epi16(SSE2_INTERLEAVE_EVEN_EPI16(a, b), SSE2_INTERLEAVE_ODD_EPI16(a, b))
#define SSE2_MADDUBS_EPI16(a, b) _mm_adds_epi16(SSE2_MULU_EVEN_EPI8(a, b), SSE2_MULU_ODD_EPI8(a, b))

#ifndef USE_ROLL_ASM
__attribute__ ((target("default"))) MVSTATIC int32 get_checksum1_avx2_64(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2) { return i; }
#endif
__attribute__ ((target("default"))) MVSTATIC int32 get_checksum1_ssse3_32(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2) { return i; }
__attribute__ ((target("default"))) MVSTATIC int32 get_checksum1_sse2_32(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2) { return i; }

/*
  Original loop per 4 bytes:
    s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3] + 10*CHAR_OFFSET;
    s1 += buf[i] + buf[i+1] + buf[i+2] + buf[i+3] + 4*CHAR_OFFSET;

  SSE2/SSSE3 loop per 32 bytes:
    int16 t1[8];
    int16 t2[8];
    for (int j = 0; j < 8; j++) {
      t1[j] = buf[j*4 + i] + buf[j*4 + i+1] + buf[j*4 + i+2] + buf[j*4 + i+3];
      t2[j] = 4*buf[j*4 + i] + 3*buf[j*4 + i+1] + 2*buf[j*4 + i+2] + buf[j*4 + i+3];
    }
    s2 += 32*s1 + (uint32)(
              28*t1[0] + 24*t1[1] + 20*t1[2] + 16*t1[3] + 12*t1[4] + 8*t1[5] + 4*t1[6] +
              t2[0] + t2[1] + t2[2] + t2[3] + t2[4] + t2[5] + t2[6] + t2[7]
          ) + 528*CHAR_OFFSET;
    s1 += (uint32)(t1[0] + t1[1] + t1[2] + t1[3] + t1[4] + t1[5] + t1[6] + t1[7]) +
          32*CHAR_OFFSET;
 */
__attribute__ ((target("ssse3"))) MVSTATIC int32 get_checksum1_ssse3_32(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2)
{
    if (len > 32) {
        int aligned = ((uintptr_t)buf & 15) == 0;

        uint32 x[4] = {0};
        x[0] = *ps1;
        __m128i ss1 = _mm_loadu_si128((__m128i_u*)x);
        x[0] = *ps2;
        __m128i ss2 = _mm_loadu_si128((__m128i_u*)x);

        const int16 mul_t1_buf[8] = {28, 24, 20, 16, 12, 8, 4, 0};
        __m128i mul_t1 = _mm_loadu_si128((__m128i_u*)mul_t1_buf);

        for (; i < (len-32); i+=32) {
            // Load ... 2*[int8*16]
            __m128i in8_1, in8_2;
            if (!aligned) {
                // Synonymous with _mm_loadu_si128 on all but a handful of old CPUs
                in8_1 = _mm_lddqu_si128((__m128i_u*)&buf[i]);
                in8_2 = _mm_lddqu_si128((__m128i_u*)&buf[i + 16]);
            } else {
                in8_1 = _mm_load_si128((__m128i_u*)&buf[i]);
                in8_2 = _mm_load_si128((__m128i_u*)&buf[i + 16]);
            }

            // (1*buf[i] + 1*buf[i+1]), (1*buf[i+2], 1*buf[i+3]), ... 2*[int16*8]
            // Fastest, even though multiply by 1
            __m128i mul_one = _mm_set1_epi8(1);
            __m128i add16_1 = _mm_maddubs_epi16(mul_one, in8_1);
            __m128i add16_2 = _mm_maddubs_epi16(mul_one, in8_2);

            // (4*buf[i] + 3*buf[i+1]), (2*buf[i+2], buf[i+3]), ... 2*[int16*8]
            __m128i mul_const = _mm_set1_epi32(4 + (3 << 8) + (2 << 16) + (1 << 24));
            __m128i mul_add16_1 = _mm_maddubs_epi16(mul_const, in8_1);
            __m128i mul_add16_2 = _mm_maddubs_epi16(mul_const, in8_2);

            // s2 += 32*s1
            ss2 = _mm_add_epi32(ss2, _mm_slli_epi32(ss1, 5));

            // [sum(t1[0]..t1[7]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            // Shifting left, then shifting right again and shuffling (rather than just
            // shifting right as with mul32 below) to cheaply end up with the correct sign
            // extension as we go from int16 to int32.
            __m128i sum_add32 = _mm_add_epi16(add16_1, add16_2);
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 2));
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 4));
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 8));
            sum_add32 = _mm_srai_epi32(sum_add32, 16);
            sum_add32 = _mm_shuffle_epi32(sum_add32, 3);

            // [sum(t2[0]..t2[7]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            __m128i sum_mul_add32 = _mm_add_epi16(mul_add16_1, mul_add16_2);
            sum_mul_add32 = _mm_add_epi16(sum_mul_add32, _mm_slli_si128(sum_mul_add32, 2));
            sum_mul_add32 = _mm_add_epi16(sum_mul_add32, _mm_slli_si128(sum_mul_add32, 4));
            sum_mul_add32 = _mm_add_epi16(sum_mul_add32, _mm_slli_si128(sum_mul_add32, 8));
            sum_mul_add32 = _mm_srai_epi32(sum_mul_add32, 16);
            sum_mul_add32 = _mm_shuffle_epi32(sum_mul_add32, 3);

            // s1 += t1[0] + t1[1] + t1[2] + t1[3] + t1[4] + t1[5] + t1[6] + t1[7]
            ss1 = _mm_add_epi32(ss1, sum_add32);

            // s2 += t2[0] + t2[1] + t2[2] + t2[3] + t2[4] + t2[5] + t2[6] + t2[7]
            ss2 = _mm_add_epi32(ss2, sum_mul_add32);

            // [t1[0] + t1[1], t1[2] + t1[3] ...] [int16*8]
            // We could've combined this with generating sum_add32 above and
            // save an instruction but benchmarking shows that as being slower
            __m128i add16 = _mm_hadds_epi16(add16_1, add16_2);

            // [t1[0], t1[1], ...] -> [t1[0]*28 + t1[1]*24, ...] [int32*4]
            __m128i mul32 = _mm_madd_epi16(add16, mul_t1);

            // [sum(mul32), X, X, X] [int32*4]; faster than multiple _mm_hadd_epi32
            mul32 = _mm_add_epi32(mul32, _mm_srli_si128(mul32, 4));
            mul32 = _mm_add_epi32(mul32, _mm_srli_si128(mul32, 8));

            // s2 += 28*t1[0] + 24*t1[1] + 20*t1[2] + 16*t1[3] + 12*t1[4] + 8*t1[5] + 4*t1[6]
            ss2 = _mm_add_epi32(ss2, mul32);

#if CHAR_OFFSET != 0
            // s1 += 32*CHAR_OFFSET
            __m128i char_offset_multiplier = _mm_set1_epi32(32 * CHAR_OFFSET);
            ss1 = _mm_add_epi32(ss1, char_offset_multiplier);

            // s2 += 528*CHAR_OFFSET
            char_offset_multiplier = _mm_set1_epi32(528 * CHAR_OFFSET);
            ss2 = _mm_add_epi32(ss2, char_offset_multiplier);
#endif
        }

        _mm_store_si128((__m128i_u*)x, ss1);
        *ps1 = x[0];
        _mm_store_si128((__m128i_u*)x, ss2);
        *ps2 = x[0];
    }
    return i;
}

/*
  Same as SSSE3 version, but using macros defined above to emulate SSSE3 calls that are not available with SSE2.
  For GCC-only the SSE2 and SSSE3 versions could be a single function calling other functions with the right
  target attributes to emulate SSSE3 calls on SSE2 if needed, but clang doesn't inline those properly leading
  to a near 50% performance drop.
 */
__attribute__ ((target("sse2"))) MVSTATIC int32 get_checksum1_sse2_32(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2)
{
    if (len > 32) {
        int aligned = ((uintptr_t)buf & 15) == 0;

        uint32 x[4] = {0};
        x[0] = *ps1;
        __m128i ss1 = _mm_loadu_si128((__m128i_u*)x);
        x[0] = *ps2;
        __m128i ss2 = _mm_loadu_si128((__m128i_u*)x);

        const int16 mul_t1_buf[8] = {28, 24, 20, 16, 12, 8, 4, 0};
        __m128i mul_t1 = _mm_loadu_si128((__m128i_u*)mul_t1_buf);

        for (; i < (len-32); i+=32) {
            // Load ... 2*[int8*16]
            __m128i in8_1, in8_2;
            if (!aligned) {
                in8_1 = _mm_loadu_si128((__m128i_u*)&buf[i]);
                in8_2 = _mm_loadu_si128((__m128i_u*)&buf[i + 16]);
            } else {
                in8_1 = _mm_load_si128((__m128i_u*)&buf[i]);
                in8_2 = _mm_load_si128((__m128i_u*)&buf[i + 16]);
            }

            // (1*buf[i] + 1*buf[i+1]), (1*buf[i+2], 1*buf[i+3]), ... 2*[int16*8]
            // Fastest, even though multiply by 1
            __m128i mul_one = _mm_set1_epi8(1);
            __m128i add16_1 = SSE2_MADDUBS_EPI16(mul_one, in8_1);
            __m128i add16_2 = SSE2_MADDUBS_EPI16(mul_one, in8_2);

            // (4*buf[i] + 3*buf[i+1]), (2*buf[i+2], buf[i+3]), ... 2*[int16*8]
            __m128i mul_const = _mm_set1_epi32(4 + (3 << 8) + (2 << 16) + (1 << 24));
            __m128i mul_add16_1 = SSE2_MADDUBS_EPI16(mul_const, in8_1);
            __m128i mul_add16_2 = SSE2_MADDUBS_EPI16(mul_const, in8_2);

            // s2 += 32*s1
            ss2 = _mm_add_epi32(ss2, _mm_slli_epi32(ss1, 5));

            // [sum(t1[0]..t1[7]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            // Shifting left, then shifting right again and shuffling (rather than just
            // shifting right as with mul32 below) to cheaply end up with the correct sign
            // extension as we go from int16 to int32.
            __m128i sum_add32 = _mm_add_epi16(add16_1, add16_2);
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 2));
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 4));
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 8));
            sum_add32 = _mm_srai_epi32(sum_add32, 16);
            sum_add32 = _mm_shuffle_epi32(sum_add32, 3);

            // [sum(t2[0]..t2[7]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            __m128i sum_mul_add32 = _mm_add_epi16(mul_add16_1, mul_add16_2);
            sum_mul_add32 = _mm_add_epi16(sum_mul_add32, _mm_slli_si128(sum_mul_add32, 2));
            sum_mul_add32 = _mm_add_epi16(sum_mul_add32, _mm_slli_si128(sum_mul_add32, 4));
            sum_mul_add32 = _mm_add_epi16(sum_mul_add32, _mm_slli_si128(sum_mul_add32, 8));
            sum_mul_add32 = _mm_srai_epi32(sum_mul_add32, 16);
            sum_mul_add32 = _mm_shuffle_epi32(sum_mul_add32, 3);

            // s1 += t1[0] + t1[1] + t1[2] + t1[3] + t1[4] + t1[5] + t1[6] + t1[7]
            ss1 = _mm_add_epi32(ss1, sum_add32);

            // s2 += t2[0] + t2[1] + t2[2] + t2[3] + t2[4] + t2[5] + t2[6] + t2[7]
            ss2 = _mm_add_epi32(ss2, sum_mul_add32);

            // [t1[0] + t1[1], t1[2] + t1[3] ...] [int16*8]
            // We could've combined this with generating sum_add32 above and
            // save an instruction but benchmarking shows that as being slower
            __m128i add16 = SSE2_HADDS_EPI16(add16_1, add16_2);

            // [t1[0], t1[1], ...] -> [t1[0]*28 + t1[1]*24, ...] [int32*4]
            __m128i mul32 = _mm_madd_epi16(add16, mul_t1);

            // [sum(mul32), X, X, X] [int32*4]; faster than multiple _mm_hadd_epi32
            mul32 = _mm_add_epi32(mul32, _mm_srli_si128(mul32, 4));
            mul32 = _mm_add_epi32(mul32, _mm_srli_si128(mul32, 8));

            // s2 += 28*t1[0] + 24*t1[1] + 20*t1[2] + 16*t1[3] + 12*t1[4] + 8*t1[5] + 4*t1[6]
            ss2 = _mm_add_epi32(ss2, mul32);

#if CHAR_OFFSET != 0
            // s1 += 32*CHAR_OFFSET
            __m128i char_offset_multiplier = _mm_set1_epi32(32 * CHAR_OFFSET);
            ss1 = _mm_add_epi32(ss1, char_offset_multiplier);

            // s2 += 528*CHAR_OFFSET
            char_offset_multiplier = _mm_set1_epi32(528 * CHAR_OFFSET);
            ss2 = _mm_add_epi32(ss2, char_offset_multiplier);
#endif
        }

        _mm_store_si128((__m128i_u*)x, ss1);
        *ps1 = x[0];
        _mm_store_si128((__m128i_u*)x, ss2);
        *ps2 = x[0];
    }
    return i;
}

#ifdef USE_ROLL_ASM /* { */

extern "C" __attribute__ ((target("avx2"))) int32 get_checksum1_avx2_asm(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2);

#else /* } { */

/*
  AVX2 loop per 64 bytes:
    int16 t1[16];
    int16 t2[16];
    for (int j = 0; j < 16; j++) {
      t1[j] = buf[j*4 + i] + buf[j*4 + i+1] + buf[j*4 + i+2] + buf[j*4 + i+3];
      t2[j] = 4*buf[j*4 + i] + 3*buf[j*4 + i+1] + 2*buf[j*4 + i+2] + buf[j*4 + i+3];
    }
    s2 += 64*s1 + (uint32)(
              60*t1[0] + 56*t1[1] + 52*t1[2] + 48*t1[3] + 44*t1[4] + 40*t1[5] + 36*t1[6] + 32*t1[7] + 28*t1[8] + 24*t1[9] + 20*t1[10] + 16*t1[11] + 12*t1[12] + 8*t1[13] + 4*t1[14] +
              t2[0] + t2[1] + t2[2] + t2[3] + t2[4] + t2[5] + t2[6] + t2[7] + t2[8] + t2[9] + t2[10] + t2[11] + t2[12] + t2[13] + t2[14] + t2[15]
          ) + 2080*CHAR_OFFSET;
    s1 += (uint32)(t1[0] + t1[1] + t1[2] + t1[3] + t1[4] + t1[5] + t1[6] + t1[7] + t1[8] + t1[9] + t1[10] + t1[11] + t1[12] + t1[13] + t1[14] + t1[15]) +
          64*CHAR_OFFSET;
 */

__attribute__ ((target("avx2"))) MVSTATIC int32 get_checksum1_avx2_64(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2)
{
    if (len > 64) {

        uint32 x[4] = {0};
        __m128i ss1 = _mm_cvtsi32_si128(*ps1);
        __m128i ss2 = _mm_cvtsi32_si128(*ps2);

        const char mul_t1_buf[16] = {60, 56, 52, 48, 44, 40, 36, 32, 28, 24, 20, 16, 12, 8, 4, 0};
	__m128i tmp = _mm_load_si128((__m128i*) mul_t1_buf);
        __m256i mul_t1 = _mm256_cvtepu8_epi16(tmp);
	__m256i mul_const = _mm256_broadcastd_epi32(_mm_cvtsi32_si128(4 | (3 << 8) | (2 << 16) | (1 << 24)));
        __m256i mul_one;
       	    mul_one = _mm256_abs_epi8(_mm256_cmpeq_epi16(mul_one,mul_one)); // set all vector elements to 1

        for (; i < (len-64); i+=64) {
            // Load ... 4*[int8*16]
            __m256i in8_1, in8_2;
	    __m128i in8_1_low, in8_2_low, in8_1_high, in8_2_high;
	    in8_1_low = _mm_loadu_si128((__m128i_u*)&buf[i]);
	    in8_2_low = _mm_loadu_si128((__m128i_u*)&buf[i+16]);
	    in8_1_high = _mm_loadu_si128((__m128i_u*)&buf[i+32]);
	    in8_2_high = _mm_loadu_si128((__m128i_u*)&buf[i+48]);
	    in8_1 = _mm256_inserti128_si256(_mm256_castsi128_si256(in8_1_low), in8_1_high,1);
	    in8_2 = _mm256_inserti128_si256(_mm256_castsi128_si256(in8_2_low), in8_2_high,1);

            // (1*buf[i] + 1*buf[i+1]), (1*buf[i+2], 1*buf[i+3]), ... 2*[int16*8]
            // Fastest, even though multiply by 1
            __m256i add16_1 = _mm256_maddubs_epi16(mul_one, in8_1);
            __m256i add16_2 = _mm256_maddubs_epi16(mul_one, in8_2);

            // (4*buf[i] + 3*buf[i+1]), (2*buf[i+2], buf[i+3]), ... 2*[int16*8]
            __m256i mul_add16_1 = _mm256_maddubs_epi16(mul_const, in8_1);
            __m256i mul_add16_2 = _mm256_maddubs_epi16(mul_const, in8_2);

            // s2 += 64*s1
            ss2 = _mm_add_epi32(ss2, _mm_slli_epi32(ss1, 6));

            // [sum(t1[0]..t1[7]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            __m256i sum_add32 = _mm256_add_epi16(add16_1, add16_2);
            sum_add32 = _mm256_add_epi16(sum_add32, _mm256_srli_epi32(sum_add32, 16));
            sum_add32 = _mm256_add_epi16(sum_add32, _mm256_srli_si256(sum_add32, 4));
            sum_add32 = _mm256_add_epi16(sum_add32, _mm256_srli_si256(sum_add32, 8));

            // [sum(t2[0]..t2[7]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            __m256i sum_mul_add32 = _mm256_add_epi16(mul_add16_1, mul_add16_2);
            sum_mul_add32 = _mm256_add_epi16(sum_mul_add32, _mm256_srli_epi32(sum_mul_add32, 16));
            sum_mul_add32 = _mm256_add_epi16(sum_mul_add32, _mm256_srli_si256(sum_mul_add32, 4));
            sum_mul_add32 = _mm256_add_epi16(sum_mul_add32, _mm256_srli_si256(sum_mul_add32, 8));

            // s1 += t1[0] + t1[1] + t1[2] + t1[3] + t1[4] + t1[5] + t1[6] + t1[7]
	    __m128i sum_add32_hi = _mm256_extracti128_si256(sum_add32, 0x1);
            ss1 = _mm_add_epi32(ss1, _mm256_castsi256_si128(sum_add32));
            ss1 = _mm_add_epi32(ss1, sum_add32_hi);

            // s2 += t2[0] + t2[1] + t2[2] + t2[3] + t2[4] + t2[5] + t2[6] + t2[7]
	    __m128i sum_mul_add32_hi = _mm256_extracti128_si256(sum_mul_add32, 0x1);
            ss2 = _mm_add_epi32(ss2, _mm256_castsi256_si128(sum_mul_add32));
            ss2 = _mm_add_epi32(ss2, sum_mul_add32_hi);

            // [t1[0] + t1[1], t1[2] + t1[3] ...] [int16*8]
            // We could've combined this with generating sum_add32 above and
            // save an instruction but benchmarking shows that as being slower
            __m256i add16 = _mm256_hadds_epi16(add16_1, add16_2);

            // [t1[0], t1[1], ...] -> [t1[0]*28 + t1[1]*24, ...] [int32*4]
            __m256i mul32 = _mm256_madd_epi16(add16, mul_t1);

            // [sum(mul32), X, X, X] [int32*4]; faster than multiple _mm_hadd_epi32
            mul32 = _mm256_add_epi32(mul32, _mm256_srli_si256(mul32, 4));
            mul32 = _mm256_add_epi32(mul32, _mm256_srli_si256(mul32, 8));
	    // prefetch 2 cacheline ahead
            _mm_prefetch(&buf[i + 160], _MM_HINT_T0);

            // s2 += 28*t1[0] + 24*t1[1] + 20*t1[2] + 16*t1[3] + 12*t1[4] + 8*t1[5] + 4*t1[6]
	    __m128i mul32_hi = _mm256_extracti128_si256(mul32, 0x1);
            ss2 = _mm_add_epi32(ss2, _mm256_castsi256_si128(mul32));
            ss2 = _mm_add_epi32(ss2, mul32_hi);

#if CHAR_OFFSET != 0
            // s1 += 32*CHAR_OFFSET
            __m128i char_offset_multiplier = _mm_set1_epi32(32 * CHAR_OFFSET);
            ss1 = _mm_add_epi32(ss1, char_offset_multiplier);

            // s2 += 528*CHAR_OFFSET
            char_offset_multiplier = _mm_set1_epi32(528 * CHAR_OFFSET);
            ss2 = _mm_add_epi32(ss2, char_offset_multiplier);
#endif
        }

        _mm_store_si128((__m128i_u*)x, ss1);
        *ps1 = x[0];
        _mm_store_si128((__m128i_u*)x, ss2);
        *ps2 = x[0];
    }
    return i;
}

#endif /* } !USE_ROLL_ASM */

static int32 get_checksum1_default_1(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2)
{
    uint32 s1 = *ps1;
    uint32 s2 = *ps2;
    for (; i < (len-4); i+=4) {
        s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3] + 10*CHAR_OFFSET;
        s1 += (buf[i+0] + buf[i+1] + buf[i+2] + buf[i+3] + 4*CHAR_OFFSET);
    }
    for (; i < len; i++) {
        s1 += (buf[i]+CHAR_OFFSET); s2 += s1;
    }
    *ps1 = s1;
    *ps2 = s2;
    return i;
}

/* With GCC 10 putting this implementation inside 'extern "C"' causes an
   assembler error. That worked fine on GCC 5-9 and clang 6-10...
  */
static inline uint32 get_checksum1_cpp(char *buf1, int32 len)
{
    int32 i = 0;
    uint32 s1 = 0;
    uint32 s2 = 0;

    // multiples of 64 bytes using AVX2 (if available)
#ifdef USE_ROLL_ASM
    i = get_checksum1_avx2_asm((schar*)buf1, len, i, &s1, &s2);
#else
    i = get_checksum1_avx2_64((schar*)buf1, len, i, &s1, &s2);
#endif

    // multiples of 32 bytes using SSSE3 (if available)
    i = get_checksum1_ssse3_32((schar*)buf1, len, i, &s1, &s2);

    // multiples of 32 bytes using SSE2 (if available)
    i = get_checksum1_sse2_32((schar*)buf1, len, i, &s1, &s2);

    // whatever is left
    i = get_checksum1_default_1((schar*)buf1, len, i, &s1, &s2);

    return (s1 & 0xffff) + (s2 << 16);
}

extern "C" {

uint32 get_checksum1(char *buf1, int32 len)
{
    return get_checksum1_cpp(buf1, len);
}

} // extern "C"

#ifdef BENCHMARK_SIMD_CHECKSUM1
#pragma clang optimize off
#pragma GCC push_options
#pragma GCC optimize ("O0")

#define ROUNDS 1024
#define BLOCK_LEN 1024*1024

#ifndef CLOCK_MONOTONIC_RAW
#define CLOCK_MONOTONIC_RAW CLOCK_MONOTONIC
#endif

static void benchmark(const char* desc, int32 (*func)(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2), schar* buf, int32 len) {
    struct timespec start, end;
    uint64_t us;
    uint32_t cs, s1, s2;
    int i, next;

    clock_gettime(CLOCK_MONOTONIC_RAW, &start);
    for (i = 0; i < ROUNDS; i++) {
        s1 = s2 = 0;
        next = func((schar*)buf, len, 0, &s1, &s2);
        get_checksum1_default_1((schar*)buf, len, next, &s1, &s2);
    }
    clock_gettime(CLOCK_MONOTONIC_RAW, &end);
    us = next == 0 ? 0 : (end.tv_sec - start.tv_sec) * 1000000 + (end.tv_nsec - start.tv_nsec) / 1000;
    cs = next == 0 ? 0 : (s1 & 0xffff) + (s2 << 16);
    printf("%-5s :: %5.0f MB/s :: %08x\n", desc, us ? (float)(len / (1024 * 1024) * ROUNDS) / ((float)us / 1000000.0f) : 0, cs);
}

static int32 get_checksum1_auto(schar* buf, int32 len, int32 i, uint32* ps1, uint32* ps2) {
    uint32 cs = get_checksum1((char*)buf, len);
    *ps1 = cs & 0xffff;
    *ps2 = cs >> 16;
    return len;
}

int main() {
    int i;
    unsigned char* buf = (unsigned char*)aligned_alloc(64,BLOCK_LEN);
    for (i = 0; i < BLOCK_LEN; i++) buf[i] = (i + (i % 3) + (i % 11)) % 256;

    benchmark("Auto", get_checksum1_auto, (schar*)buf, BLOCK_LEN);
    benchmark("Raw-C", get_checksum1_default_1, (schar*)buf, BLOCK_LEN);
    benchmark("SSE2", get_checksum1_sse2_32, (schar*)buf, BLOCK_LEN);
    benchmark("SSSE3", get_checksum1_ssse3_32, (schar*)buf, BLOCK_LEN);
#ifdef USE_ROLL_ASM
    benchmark("AVX2-ASM", get_checksum1_avx2_asm, (schar*)buf, BLOCK_LEN);
#else
    benchmark("AVX2", get_checksum1_avx2_64, (schar*)buf, BLOCK_LEN);
#endif

    free(buf);
    return 0;
}

#pragma GCC pop_options
#pragma clang optimize on
#endif /* BENCHMARK_SIMD_CHECKSUM1 */

#endif /* } USE_ROLL_SIMD */
#endif /* } __cplusplus */
#endif /* } __x86_64__ */
