/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

#include "rsync.h"

int csum_length=2; /* initial value */

#define CSUM_CHUNK 64

int checksum_seed = 0;
extern int remote_version;

/*
  a simple 32 bit checksum that can be upadted from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
uint32 get_checksum1(char *buf1,int len)
{
    int i;
    uint32 s1, s2;
    schar *buf = (schar *)buf1;

    s1 = s2 = 0;
    for (i = 0; i < (len-4); i+=4) {
	s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3] + 
	  10*CHAR_OFFSET;
	s1 += (buf[i+0] + buf[i+1] + buf[i+2] + buf[i+3] + 4*CHAR_OFFSET); 
    }
    for (; i < len; i++) {
	s1 += (buf[i]+CHAR_OFFSET); s2 += s1;
    }
    return (s1 & 0xffff) + (s2 << 16);
}


void get_checksum2(char *buf,int len,char *sum)
{
	int i;
	static char *buf1;
	static int len1;
	struct mdfour m;

	if (len > len1) {
		if (buf1) free(buf1);
		buf1 = new_array(char, len+4);
		len1 = len;
		if (!buf1) out_of_memory("get_checksum2");
	}
	
	mdfour_begin(&m);
	
	memcpy(buf1,buf,len);
	if (checksum_seed) {
		SIVAL(buf1,len,checksum_seed);
		len += 4;
	}
	
	for(i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
		mdfour_update(&m, (uchar *)(buf1+i), CSUM_CHUNK);
	}
	if (len - i > 0) {
		mdfour_update(&m, (uchar *)(buf1+i), (len-i));
	}
	
	mdfour_result(&m, (uchar *)sum);
}


void file_checksum(char *fname,char *sum,OFF_T size)
{
	OFF_T i;
	struct map_struct *buf;
	int fd;
	OFF_T len = size;
	char tmpchunk[CSUM_CHUNK];
	struct mdfour m;
	
	memset(sum,0,MD4_SUM_LENGTH);
	
	fd = do_open(fname, O_RDONLY, 0);
	if (fd == -1) return;
	
	buf = map_file(fd,size);
	
	mdfour_begin(&m);

	for(i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
		memcpy(tmpchunk, map_ptr(buf,i,CSUM_CHUNK), CSUM_CHUNK);
		mdfour_update(&m, (uchar *)tmpchunk, CSUM_CHUNK);
	}

	if (len - i > 0) {
		memcpy(tmpchunk, map_ptr(buf,i,len-i), len-i);
		mdfour_update(&m, (uchar *)tmpchunk, (len-i));
	}

	mdfour_result(&m, (uchar *)sum);

	close(fd);
	unmap_file(buf);
}


void checksum_init(void)
{
  if (remote_version >= 14)
    csum_length = 2; /* adaptive */
  else
    csum_length = SUM_LENGTH;
}



static int sumresidue;
static char sumrbuf[CSUM_CHUNK];
static struct mdfour md;

void sum_init(void)
{
	char s[4];
	mdfour_begin(&md);
	sumresidue=0;
	SIVAL(s,0,checksum_seed);
	sum_update(s,4);
}

/**
 * Feed data into an MD4 accumulator, md.  The results may be
 * retrieved using sum_end().  md is used for different purposes at
 * different points during execution.
 *
 * @todo Perhaps get rid of md and just pass in the address each time.
 * Very slightly clearer and slower.
 **/
void sum_update(char *p, int len)
{
	int i;
	if (len + sumresidue < CSUM_CHUNK) {
		memcpy(sumrbuf+sumresidue, p, len);
		sumresidue += len;
		return;
	}

	if (sumresidue) {
		i = MIN(CSUM_CHUNK-sumresidue,len);
		memcpy(sumrbuf+sumresidue,p,i);
		mdfour_update(&md, (uchar *)sumrbuf, (i+sumresidue));
		len -= i;
		p += i;
	}

	for(i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
		memcpy(sumrbuf,p+i,CSUM_CHUNK);
		mdfour_update(&md, (uchar *)sumrbuf, CSUM_CHUNK);
	}

	if (len - i > 0) {
		sumresidue = len-i;
		memcpy(sumrbuf,p+i,sumresidue);
	} else {
		sumresidue = 0;    
	}
}

void sum_end(char *sum)
{
	if (sumresidue) {
		mdfour_update(&md, (uchar *)sumrbuf, sumresidue);
	}

	mdfour_result(&md, (uchar *)sum);
}
