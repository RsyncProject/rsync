/*
 * SSE2/SSSE3-optimized routines to support checksumming of bytes.
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
 * While on more modern CPUs transfers are less likely to be CPU limited,
 * lower CPU usage is always better. Improvements may still be seen when
 * matching chunks from NVMe storage even on newer CPUs.
 *
 * Benchmarks                   C           SSE2        SSSE3
 * - Intel Atom D2700           550 MB/s    750 MB/s    1000 MB/s
 * - Intel i7-7700hq            1850 MB/s   2550 MB/s   4050 MB/s
 * - AMD ThreadRipper 2950x     2900 MB/s   5600 MB/s   8950 MB/s
 *
 * This optimization for get_checksum1() is intentionally limited to x86-64
 * as no 32-bit CPU was available for testing. As 32-bit CPUs only have half
 * the available xmm registers, this optimized version may not be faster than
 * the pure C version anyway. Note that all x86-64 CPUs support SSE2.
 *
 * This file is compiled using GCC 4.8+'s C++ front end to allow the use of
 * the target attribute, selecting the fastest code path based on runtime
 * detection of CPU capabilities.
 */

#ifdef __x86_64__
#ifdef __cplusplus

#include "rsync.h"

#ifdef ENABLE_SSE2

#include <immintrin.h>

/* Compatibility functions to let our SSSE3 algorithm run on SSE2 */

__attribute__ ((target ("sse2"))) static inline __m128i sse_load_si128(__m128i_u* buf) {
    return _mm_loadu_si128(buf);
}

__attribute__ ((target ("ssse3"))) static inline __m128i sse_load_si128(__m128i_u* buf) {
    return _mm_lddqu_si128(buf);  // same as loadu on all but the oldest SSSE3 CPUs
}

__attribute__ ((target ("sse2"))) static inline __m128i sse_interleave_odd_epi16(__m128i a, __m128i b) {
    return _mm_packs_epi32(
        _mm_srai_epi32(a, 16),
        _mm_srai_epi32(b, 16)
    );
}

__attribute__ ((target ("sse2"))) static inline __m128i sse_interleave_even_epi16(__m128i a, __m128i b) {
    return sse_interleave_odd_epi16(
        _mm_slli_si128(a, 2),
        _mm_slli_si128(b, 2)
    );
}

__attribute__ ((target ("sse2"))) static inline __m128i sse_mulu_odd_epi8(__m128i a, __m128i b) {
    return _mm_mullo_epi16(
        _mm_srli_epi16(a, 8),
        _mm_srai_epi16(b, 8)
    );
}

__attribute__ ((target ("sse2"))) static inline __m128i sse_mulu_even_epi8(__m128i a, __m128i b) {
    return _mm_mullo_epi16(
        _mm_and_si128(a, _mm_set1_epi16(0xFF)),
        _mm_srai_epi16(_mm_slli_si128(b, 1), 8)
    );
}

__attribute__ ((target ("sse2"))) static inline __m128i sse_hadds_epi16(__m128i a, __m128i b) {
    return _mm_adds_epi16(
        sse_interleave_even_epi16(a, b),
        sse_interleave_odd_epi16(a, b)
    );
}

__attribute__ ((target ("ssse3"))) static inline __m128i sse_hadds_epi16(__m128i a, __m128i b) {
    return _mm_hadds_epi16(a, b);
}

__attribute__ ((target ("sse2"))) static inline __m128i sse_maddubs_epi16(__m128i a, __m128i b) {
    return _mm_adds_epi16(
        sse_mulu_even_epi8(a, b),
        sse_mulu_odd_epi8(a, b)
    );
}

__attribute__ ((target ("ssse3"))) static inline __m128i sse_maddubs_epi16(__m128i a, __m128i b) {
    return _mm_maddubs_epi16(a, b);
}

__attribute__ ((target ("default"))) static inline __m128i sse_load_si128(__m128i_u* buf) { }
__attribute__ ((target ("default"))) static inline __m128i sse_interleave_odd_epi16(__m128i a, __m128i b) { }
__attribute__ ((target ("default"))) static inline __m128i sse_interleave_even_epi16(__m128i a, __m128i b) { }
__attribute__ ((target ("default"))) static inline __m128i sse_mulu_odd_epi8(__m128i a, __m128i b) { }
__attribute__ ((target ("default"))) static inline __m128i sse_mulu_even_epi8(__m128i a, __m128i b) { }
__attribute__ ((target ("default"))) static inline __m128i sse_hadds_epi16(__m128i a, __m128i b) { }
__attribute__ ((target ("default"))) static inline __m128i sse_maddubs_epi16(__m128i a, __m128i b) { }

/*
  a simple 32 bit checksum that can be updated from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
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
    s2 += 32*s1 +
          28*t1[0] + 24*t1[1] + 20*t1[2] + 16*t1[3] + 12*t1[4] + 8*t1[5] + 4*t1[6] +
          t2[0] + t2[1] + t2[2] + t2[3] + t2[4] + t2[5] + t2[6] + t2[7] +
          ((16+32+48+64+80+96) + 8)*CHAR_OFFSET;
    s1 += t1[0] + t1[1] + t1[2] + t1[3] + t1[4] + t1[5] + t1[6] + t1[7] +
          32*CHAR_OFFSET;
 */
/*
  Both sse2 and ssse3 targets must be specified here for the optimizer to
  fully unroll into two separate functions for each, or it will decide which
  version of other functions (such as sse_maddubs_epi16) to call every loop
  iteration instead of properly inlining them, negating any performance gain.
 */
__attribute__ ((target ("sse2", "ssse3"))) static inline uint32 get_checksum1_accel(char *buf1, int32 len) {
    int32 i;
    uint32 s1, s2;
    schar *buf = (schar *)buf1;

    i = s1 = s2 = 0;
    if (len > 32) {
        const char mul_t1_buf[16] = {28, 0, 24, 0, 20, 0, 16, 0, 12, 0, 8, 0, 4, 0, 0, 0};
        __m128i mul_t1 = sse_load_si128((__m128i_u*)mul_t1_buf);
        __m128i ss1 = _mm_setzero_si128();
        __m128i ss2 = _mm_setzero_si128();

        for (i = 0; i < (len-32); i+=32) {
            // Load ... 2*[int8*16]
            __m128i in8_1 = sse_load_si128((__m128i_u*)&buf[i]);
            __m128i in8_2 = sse_load_si128((__m128i_u*)&buf[i + 16]);

            // (1*buf[i] + 1*buf[i+1]), (1*buf[i+2], 1*buf[i+3]), ... 2*[int16*8]
            // Fastest, even though multiply by 1
            __m128i mul_one = _mm_set1_epi8(1);
            __m128i add16_1 = sse_maddubs_epi16(mul_one, in8_1);
            __m128i add16_2 = sse_maddubs_epi16(mul_one, in8_2);

            // (4*buf[i] + 3*buf[i+1]), (2*buf[i+2], buf[i+3]), ... 2*[int16*8]
            __m128i mul_const = _mm_set1_epi32(4 + (3 << 8) + (2 << 16) + (1 << 24));
            __m128i mul_add16_1 = sse_maddubs_epi16(mul_const, in8_1);
            __m128i mul_add16_2 = sse_maddubs_epi16(mul_const, in8_2);

            // s2 += 32*s1
            ss2 = _mm_add_epi32(ss2, _mm_slli_epi32(ss1, 5));

            // [sum(t1[0]..t1[6]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
            // Shifting left, then shifting right again and shuffling (rather than just
            // shifting right as with mul32 below) to cheaply end up with the correct sign
            // extension as we go from int16 to int32.
            __m128i sum_add32 = _mm_add_epi16(add16_1, add16_2);
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 2));
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 4));
            sum_add32 = _mm_add_epi16(sum_add32, _mm_slli_si128(sum_add32, 8));
            sum_add32 = _mm_srai_epi32(sum_add32, 16);
            sum_add32 = _mm_shuffle_epi32(sum_add32, 3);

            // [sum(t2[0]..t2[6]), X, X, X] [int32*4]; faster than multiple _mm_hadds_epi16
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

            // [t1[0], t1[1], ...] [int16*8]
            // We could've combined this with generating sum_add32 above and save one _mm_add_epi16,
            // but benchmarking shows that as being slower
            __m128i add16 = sse_hadds_epi16(add16_1, add16_2);

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

        int32 x[4] = {0};
        _mm_store_si128((__m128i_u*)x, ss1);
        s1 = x[0];
        _mm_store_si128((__m128i_u*)x, ss2);
        s2 = x[0];
    }
    for (; i < (len-4); i+=4) {
        s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3] + 10*CHAR_OFFSET;
        s1 += (buf[i] + buf[i+1] + buf[i+2] + buf[i+3] + 4*CHAR_OFFSET);
    }
    for (; i < len; i++) {
        s1 += (buf[i]+CHAR_OFFSET); s2 += s1;
    }
    return (s1 & 0xffff) + (s2 << 16);
}

/*
  a simple 32 bit checksum that can be updated from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
/*
  Pure copy/paste from get_checksum1 @ checksum.c. We cannot use the target
  attribute there as that requires cpp.
  */
__attribute__ ((target ("default"))) static inline uint32 get_checksum1_accel(char *buf1, int32 len)
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

extern "C" {

/*
  C doesn't support the target attribute, so here's another wrapper
*/
uint32 get_checksum1(char *buf1, int32 len) {
    return get_checksum1_accel(buf1, len);
}

}
#endif /* ENABLE_SSE2 */
#endif /* __cplusplus */
#endif /* __x86_64__ */
