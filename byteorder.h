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
#if !defined RSYNC_TEST_PORTABLE_BYTEORDER \
 && (defined __i386__ || defined __i486__ || defined __i586__ || defined __i686__ || __amd64)
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
	uint32 lower = IVALu((const uchar *)buf, pos);
#if SIZEOF_INT64 >= 8
	const int64 word_range = (int64)1 << 32;
	int64 upper = IVALu((const uchar *)buf, pos + 4);

	if (upper >= word_range / 2)
		upper -= word_range;
	return upper * word_range + (int64)lower;
#else
	return lower;
#endif
}

static inline void
SIVAL64(char *buf, int pos, int64 val)
{
#if SIZEOF_INT64 >= 8
	/* Complement first so the shifted operand is non-negative. */
	uint32 upper = val < 0 ? ~(uint32)((-1 - val) >> 32) : (uint32)(val >> 32);
#else
	uint32 upper = val < 0 ? (uint32)-1 : 0;
#endif
	SIVALu((uchar*)buf, pos, val);
	SIVALu((uchar*)buf, pos + 4, upper);
}

#else /* !CAREFUL_ALIGNMENT */

/* Fixed-size copies permit unaligned access without type punning. */
static inline uint32
IVALu(const uchar *buf, int pos)
{
	uint32 val;
	memcpy(&val, buf + pos, sizeof val);
	return val;
}

static inline void
SIVALu(uchar *buf, int pos, uint32 val)
{
	memcpy(buf + pos, &val, sizeof val);
}

static inline int64
IVAL64(const char *buf, int pos)
{
	int64 val;
	memcpy(&val, buf + pos, sizeof val);
	return val;
}

static inline void
SIVAL64(char *buf, int pos, int64 val)
{
	memcpy(buf + pos, &val, sizeof val);
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
