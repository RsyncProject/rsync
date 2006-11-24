/*
 * Simple byteorder handling.
 *
 * Copyright (C) 1992-1995 Andrew Tridgell
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#undef CAREFUL_ALIGNMENT

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
#define SCVAL(buf,pos,val) (CVAL(buf,pos) = (val))

/* Our 64-bit numbers are sent in MSB-first order so that we can use
 * the highest bits to indicate the number of bytes sent. */
#define NVAL2(b,m) ((UVAL(b,0)&~(m))<<8|UVAL(b,1))
#define NVAL3(b,m) (NVAL2(b,m)<<8|UVAL(b,2))
#define NVAL4(b,m) (NVAL3(b,m)<<8|UVAL(b,3))
#define NVAL5(b,m) ((int64)NVAL4(b,m)<<8|UVAL(b,4))
#define NVAL6(b,m) (NVAL5(b,m)<<8|UVAL(b,5))
#define NVAL7(b,m) (NVAL6(b,m)<<8|UVAL(b,6))
#define NVAL8(b,m) (NVAL7(b,m)<<8|UVAL(b,7))

#if CAREFUL_ALIGNMENT
#define PVAL(buf,pos) (UVAL(buf,pos)|UVAL(buf,(pos)+1)<<8)
#define IVAL(buf,pos) (PVAL(buf,pos)|PVAL(buf,(pos)+2)<<16)
#define SSVALX(buf,pos,val) (CVAL(buf,pos)=(val)&0xFF,CVAL(buf,pos+1)=(val)>>8)
#define SIVALX(buf,pos,val) (SSVALX(buf,pos,val&0xFFFF),SSVALX(buf,pos+2,val>>16))
#define SIVAL(buf,pos,val) SIVALX((buf),(pos),((uint32)(val)))
#else
/* this handles things for architectures like the 386 that can handle
   alignment errors */
/*
   WARNING: This section is dependent on the length of int32
   being correct. set CAREFUL_ALIGNMENT if it is not.
*/
#define IVAL(buf,pos) (*(uint32 *)((char *)(buf) + (pos)))
#define SIVAL(buf,pos,val) IVAL(buf,pos)=((uint32)(val))
#endif
