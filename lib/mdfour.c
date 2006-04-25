/* 
 * Unix SMB/Netbios implementation.
 * Version 1.9.
 * An implementation of MD4 designed for use in the SMB authentication protocol.
 *
 * Copyright (C) 1997-1998 Andrew Tridgell
 * Copyright (C) 2005 Wayne Davison
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

#include "rsync.h"

/* NOTE: This code makes no attempt to be fast! 
 *
 * It assumes that a int is at least 32 bits long. */

static struct mdfour *m;

#define MASK32 (0xffffffff)

#define F(X,Y,Z) ((((X)&(Y)) | ((~(X))&(Z))))
#define G(X,Y,Z) ((((X)&(Y)) | ((X)&(Z)) | ((Y)&(Z))))
#define H(X,Y,Z) (((X)^(Y)^(Z)))
#define lshift(x,s) (((((x)<<(s))&MASK32) | (((x)>>(32-(s)))&MASK32)))

#define ROUND1(a,b,c,d,k,s) a = lshift((a + F(b,c,d) + M[k])&MASK32, s)
#define ROUND2(a,b,c,d,k,s) a = lshift((a + G(b,c,d) + M[k] + 0x5A827999)&MASK32,s)
#define ROUND3(a,b,c,d,k,s) a = lshift((a + H(b,c,d) + M[k] + 0x6ED9EBA1)&MASK32,s)

/* this applies md4 to 64 byte chunks */
static void mdfour64(uint32 *M)
{
	uint32 AA, BB, CC, DD;
	uint32 A,B,C,D;

	A = m->A; B = m->B; C = m->C; D = m->D; 
	AA = A; BB = B; CC = C; DD = D;

        ROUND1(A,B,C,D,  0,  3);  ROUND1(D,A,B,C,  1,  7);  
	ROUND1(C,D,A,B,  2, 11);  ROUND1(B,C,D,A,  3, 19);
        ROUND1(A,B,C,D,  4,  3);  ROUND1(D,A,B,C,  5,  7);  
	ROUND1(C,D,A,B,  6, 11);  ROUND1(B,C,D,A,  7, 19);
        ROUND1(A,B,C,D,  8,  3);  ROUND1(D,A,B,C,  9,  7);  
	ROUND1(C,D,A,B, 10, 11);  ROUND1(B,C,D,A, 11, 19);
        ROUND1(A,B,C,D, 12,  3);  ROUND1(D,A,B,C, 13,  7);  
	ROUND1(C,D,A,B, 14, 11);  ROUND1(B,C,D,A, 15, 19);	


        ROUND2(A,B,C,D,  0,  3);  ROUND2(D,A,B,C,  4,  5);  
	ROUND2(C,D,A,B,  8,  9);  ROUND2(B,C,D,A, 12, 13);
        ROUND2(A,B,C,D,  1,  3);  ROUND2(D,A,B,C,  5,  5);  
	ROUND2(C,D,A,B,  9,  9);  ROUND2(B,C,D,A, 13, 13);
        ROUND2(A,B,C,D,  2,  3);  ROUND2(D,A,B,C,  6,  5);  
	ROUND2(C,D,A,B, 10,  9);  ROUND2(B,C,D,A, 14, 13);
        ROUND2(A,B,C,D,  3,  3);  ROUND2(D,A,B,C,  7,  5);  
	ROUND2(C,D,A,B, 11,  9);  ROUND2(B,C,D,A, 15, 13);

	ROUND3(A,B,C,D,  0,  3);  ROUND3(D,A,B,C,  8,  9);  
	ROUND3(C,D,A,B,  4, 11);  ROUND3(B,C,D,A, 12, 15);
        ROUND3(A,B,C,D,  2,  3);  ROUND3(D,A,B,C, 10,  9);  
	ROUND3(C,D,A,B,  6, 11);  ROUND3(B,C,D,A, 14, 15);
        ROUND3(A,B,C,D,  1,  3);  ROUND3(D,A,B,C,  9,  9);  
	ROUND3(C,D,A,B,  5, 11);  ROUND3(B,C,D,A, 13, 15);
        ROUND3(A,B,C,D,  3,  3);  ROUND3(D,A,B,C, 11,  9);  
	ROUND3(C,D,A,B,  7, 11);  ROUND3(B,C,D,A, 15, 15);

	A += AA; B += BB; 
	C += CC; D += DD;
	
	A &= MASK32; B &= MASK32; 
	C &= MASK32; D &= MASK32;

	m->A = A; m->B = B; m->C = C; m->D = D;
}

static void copy64(uint32 *M, unsigned char *in)
{
	int i;

	for (i=0;i<16;i++)
		M[i] = (in[i*4+3]<<24) | (in[i*4+2]<<16) |
			(in[i*4+1]<<8) | (in[i*4+0]<<0);
}

static void copy4(unsigned char *out,uint32 x)
{
	out[0] = x&0xFF;
	out[1] = (x>>8)&0xFF;
	out[2] = (x>>16)&0xFF;
	out[3] = (x>>24)&0xFF;
}

void mdfour_begin(struct mdfour *md)
{
	md->A = 0x67452301;
	md->B = 0xefcdab89;
	md->C = 0x98badcfe;
	md->D = 0x10325476;
	md->totalN = 0;
	md->totalN2 = 0;
}


static void mdfour_tail(unsigned char *in, uint32 n)
{
	unsigned char buf[128];
	uint32 M[16];
	extern int protocol_version;

	/*
	 * Count total number of bits, modulo 2^64
	 */
	m->totalN += n << 3;
	if (m->totalN < (n << 3)) {
		m->totalN2++;
	}
	m->totalN2 += n >> 29;

	memset(buf, 0, 128);
	if (n) memcpy(buf, in, n);
	buf[n] = 0x80;

	if (n <= 55) {
		copy4(buf+56, m->totalN);
		/*
		 * Prior to protocol version 27 only the number of bits
		 * modulo 2^32 was included.  MD4 requires the number
		 * of bits modulo 2^64, which was fixed starting with
		 * protocol version 27.
		 */
		if (protocol_version >= 27) {
			copy4(buf+60, m->totalN2);
		}
		copy64(M, buf);
		mdfour64(M);
	} else {
		copy4(buf+120, m->totalN); 
		/*
		 * Prior to protocol version 27 only the number of bits
		 * modulo 2^32 was included.  MD4 requires the number
		 * of bits modulo 2^64, which was fixed starting with
		 * protocol version 27.
		 */
		if (protocol_version >= 27) {
			copy4(buf+124, m->totalN2); 
		}
		copy64(M, buf);
		mdfour64(M);
		copy64(M, buf+64);
		mdfour64(M);
	}
}

void mdfour_update(struct mdfour *md, unsigned char *in, uint32 n)
{
	uint32 M[16];

	m = md;

	if (n == 0) mdfour_tail(in, n);

	while (n >= 64) {
		copy64(M, in);
		mdfour64(M);
		in += 64;
		n -= 64;
		m->totalN += 64 << 3;
		if (m->totalN < 64 << 3) {
			m->totalN2++;
		}
	}

	if (n) mdfour_tail(in, n);
}


void mdfour_result(struct mdfour *md, unsigned char *out)
{
	m = md;

	copy4(out, m->A);
	copy4(out+4, m->B);
	copy4(out+8, m->C);
	copy4(out+12, m->D);
}


void mdfour(unsigned char *out, unsigned char *in, int n)
{
	struct mdfour md;
	mdfour_begin(&md);
	mdfour_update(&md, in, n);
	mdfour_result(&md, out);
}

#ifdef TEST_MDFOUR
int protocol_version = 28;

static void file_checksum1(char *fname)
{
	int fd, i, was_multiple_of_64 = 1;
	struct mdfour md;
	unsigned char buf[64*1024], sum[16];
	
	fd = open(fname,O_RDONLY);
	if (fd == -1) {
		perror("fname");
		exit(1);
	}
	
	mdfour_begin(&md);

	while (1) {
		int n = read(fd, buf, sizeof(buf));
		if (n <= 0)
			break;
		was_multiple_of_64 = !(n % 64);
		mdfour_update(&md, buf, n);
	}
	if (was_multiple_of_64 && protocol_version >= 27)
		mdfour_update(&md, buf, 0);

	close(fd);

	mdfour_result(&md, sum);

	for (i=0;i<16;i++)
		printf("%02X", sum[i]);
	printf("\n");
}

#if 0
#include "../md4.h"

static void file_checksum2(char *fname)
{
	int fd, i;
	MDstruct md;
	unsigned char buf[64], sum[16];

	fd = open(fname,O_RDONLY);
	if (fd == -1) {
		perror("fname");
		exit(1);
	}
	
	MDbegin(&md);

	while (1) {
		int n = read(fd, buf, sizeof(buf));
		if (n <= 0) break;
		MDupdate(&md, buf, n*8);
	}

	if (!md.done) {
		MDupdate(&md, buf, 0);
	}

	close(fd);

	memcpy(sum, md.buffer, 16);

	for (i=0;i<16;i++)
		printf("%02X", sum[i]);
	printf("\n");
}
#endif

 int main(int argc, char *argv[])
{
	file_checksum1(argv[1]);
#if 0
	file_checksum2(argv[1]);
#endif
	return 0;
}
#endif
