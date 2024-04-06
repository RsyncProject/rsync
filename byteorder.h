/*
 * Simple byteorder handling.
 *
 * Copyright (C) 1992-1995 Andrew Tridgell
 * Copyright (C) 2007-2022 Wayne Davison
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

#undef CAREFUL_ALIGNMENT

/* We know that the x86 can handle misalignment and has the same
 * byte order (LSB-first) as the 32-bit numbers we transmit. */
#if defined __i386__ || defined __i486__ || defined __i586__ || defined __i686__ || __amd64
#define CAREFUL_ALIGNMENT 0
#endif

#ifndef CAREFUL_ALIGNMENT
#define CAREFUL_ALIGNMENT 1
#endif

#define CVAL(buf,pos) (((unsigned char *)(buf))[pos])
#define UVAL(buf,pos) ((uint32)CVAL(buf,pos))

#if CAREFUL_ALIGNMENT

static inline uint32
IVALu(const uchar *buf, int pos)
{
	return UVAL(buf, pos)
	     | UVAL(buf, pos + 1) << 8
	     | UVAL(buf, pos + 2) << 16
	     | UVAL(buf, pos + 3) << 24;
}

static inline void
SIVALu(uchar *buf, int pos, uint32 val)
{
	CVAL(buf, pos)     = val;
	CVAL(buf, pos + 1) = val >> 8;
	CVAL(buf, pos + 2) = val >> 16;
	CVAL(buf, pos + 3) = val >> 24;
}

static inline int64
IVAL64(const char *buf, int pos)
{
	return IVALu((uchar*)buf, pos) | (int64)IVALu((uchar*)buf, pos + 4) << 32;
}

static inline void
SIVAL64(char *buf, int pos, int64 val)
{
	SIVALu((uchar*)buf, pos, val);
	SIVALu((uchar*)buf, pos + 4, val >> 32);
}

#else /* !CAREFUL_ALIGNMENT */

/* We don't want false positives about alignment from UBSAN, see:
   https://github.com/WayneD/rsync/issues/427#issuecomment-1375132291
*/

/* From https://gcc.gnu.org/onlinedocs/cpp/Common-Predefined-Macros.html */
#ifndef GCC_VERSION
#define GCC_VERSION (__GNUC__ * 10000 \
                     + __GNUC_MINOR__ * 100 \
                     + __GNUC_PATCHLEVEL__)
#endif

/* This handles things for architectures like the 386 that can handle alignment errors.
 * WARNING: This section is dependent on the length of an int32 (and thus a uint32)
 * being correct (4 bytes)!  Set CAREFUL_ALIGNMENT if it is not. */

#ifdef __clang__
__attribute__((no_sanitize("undefined")))
#elif GCC_VERSION >= 409
__attribute__((no_sanitize_undefined))
#endif
static inline uint32
IVALu(const uchar *buf, int pos)
{
	union {
		const uchar *b;
		const uint32 *num;
	} u;
	u.b = buf + pos;
	return *u.num;
}

#ifdef __clang__
__attribute__((no_sanitize("undefined")))
#elif GCC_VERSION >= 409
__attribute__((no_sanitize_undefined))
#endif
static inline void
SIVALu(uchar *buf, int pos, uint32 val)
{
	union {
		uchar *b;
		uint32 *num;
	} u;
	u.b = buf + pos;
	*u.num = val;
}

#ifdef __clang__
__attribute__((no_sanitize("undefined")))
#elif GCC_VERSION >= 409
__attribute__((no_sanitize_undefined))
#endif
static inline int64
IVAL64(const char *buf, int pos)
{
	union {
		const char *b;
		const int64 *num;
	} u;
	u.b = buf + pos;
	return *u.num;
}

#ifdef __clang__
__attribute__((no_sanitize("undefined")))
#elif GCC_VERSION >= 409
__attribute__((no_sanitize_undefined))
#endif
static inline void
SIVAL64(char *buf, int pos, int64 val)
{
	union {
		char *b;
		int64 *num;
	} u;
	u.b = buf + pos;
	*u.num = val;
}

#endif /* !CAREFUL_ALIGNMENT */

static inline uint32
IVAL(const char *buf, int pos)
{
	return IVALu((uchar*)buf, pos);
}

static inline void
SIVAL(char *buf, int pos, uint32 val)
{
	SIVALu((uchar*)buf, pos, val);
}
