/*
 * Simple byteorder handling.
 *
 * Copyright (C) 1992-1995 Andrew Tridgell
 * Copyright (C) 2007-2008 Wayne Davison
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
#undef AVOID_BYTEORDER_INLINE

/* We know that the x86 can handle misalignment and has the same
 * byte order (LSB-first) as the 32-bit numbers we transmit. */
#ifdef __i386__
#define CAREFUL_ALIGNMENT 0
#endif

#ifndef CAREFUL_ALIGNMENT
#define CAREFUL_ALIGNMENT 1
#endif

#define CVAL(buf,pos) (((unsigned char *)(buf))[pos])
#define UVAL(buf,pos) ((uint32)CVAL(buf,pos))

#if CAREFUL_ALIGNMENT

#define PVAL(buf,pos) (UVAL(buf,pos)|UVAL(buf,(pos)+1)<<8)
#define IVAL(buf,pos) (PVAL(buf,pos)|PVAL(buf,(pos)+2)<<16)
#define SSVALX(buf,pos,val) (CVAL(buf,pos)=(val)&0xFF,CVAL(buf,pos+1)=(val)>>8)
#define SIVALX(buf,pos,val) (SSVALX(buf,pos,val&0xFFFF),SSVALX(buf,pos+2,val>>16))
#define SIVAL(buf,pos,val) SIVALX((buf),(pos),((uint32)(val)))

#define IVALu(buf,pos) IVAL(buf,pos)
#define SIVALu(buf,pos,val) SIVAL(buf,pos,val)

#else /* !CAREFUL_ALIGNMENT */

/* This handles things for architectures like the 386 that can handle alignment errors.
 * WARNING: This section is dependent on the length of an int32 (and thus a uint32)
 * being correct (4 bytes)!  Set CAREFUL_ALIGNMENT if it is not. */

# ifdef AVOID_BYTEORDER_INLINE

#define IVAL(buf,pos) (*(uint32 *)((char *)(buf) + (pos)))
#define SIVAL(buf,pos,val) IVAL(buf,pos)=((uint32)(val))

#define IVALu(buf,pos) IVAL(buf,pos)
#define SIVALu(buf,pos,val) SIVAL(buf,pos,val)

# else /* !AVOID_BYTEORDER_INLINE */

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

# endif /* !AVOID_BYTEORDER_INLINE */

#endif /* !CAREFUL_ALIGNMENT */
