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

int csum_length=SUM_LENGTH;

#define CSUM_CHUNK 64

int checksum_seed = 0;

/*
  a simple 32 bit checksum that can be upadted from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
uint32 get_checksum1(char *buf,int len)
{
    int i;
    uint32 s1, s2;

    s1 = s2 = 0;
    for (i = 0; i < (len-4); i+=4) {
	s2 += 4*(s1 + buf[i]) + 3*buf[i+1] + 2*buf[i+2] + buf[i+3];
	s1 += (buf[i+0] + buf[i+1] + buf[i+2] + buf[i+3]); 
    }
    for (; i < len; i++) {
	s1 += buf[i]; s2 += s1;
    }
    return (s1 & 0xffff) + (s2 << 16);
}


static void sum_put(MDstruct *md,char *sum)
{
  SIVAL(sum,0,md->buffer[0]);
  if (csum_length <= 4) return;
  SIVAL(sum,4,md->buffer[1]);
  if (csum_length <= 8) return;
  SIVAL(sum,8,md->buffer[2]);
  if (csum_length <= 12) return;
  SIVAL(sum,12,md->buffer[3]);
}


void get_checksum2(char *buf,int len,char *sum)
{
  int i;
  MDstruct MD;
  static char *buf1 = NULL;
  static int len1 = 0;

  if (len > len1) {
    if (buf1) free(buf1);
    buf1 = (char *)malloc(len+sizeof(checksum_seed));
    len1 = len;
    if (!buf1) out_of_memory("get_checksum2");
  }

  MDbegin(&MD);

  bcopy(buf,buf1,len);
  if (checksum_seed) {
    bcopy((char *)&checksum_seed,buf1+len,sizeof(checksum_seed));
    len += sizeof(checksum_seed);
  }

  for(i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
    MDupdate(&MD, buf1+i, CSUM_CHUNK*8);
  }
  MDupdate(&MD, buf1+i, (len-i)*8);

  sum_put(&MD,sum);
}


void file_checksum(char *fname,char *sum,off_t size)
{
  int i;
  MDstruct MD;
  char *buf;
  int fd;
  int len = size;
  char tmpchunk[CSUM_CHUNK];

  bzero(sum,csum_length);

  fd = open(fname,O_RDONLY);
  if (fd == -1) return;

  buf = map_file(fd,size);

  MDbegin(&MD);

  for(i = 0; i + CSUM_CHUNK <= len; i += CSUM_CHUNK) {
    bcopy(map_ptr(buf,i,CSUM_CHUNK),tmpchunk,CSUM_CHUNK);
    MDupdate(&MD, tmpchunk, CSUM_CHUNK*8);
  }

  bcopy(map_ptr(buf,i,len-i),tmpchunk,len-i);
  MDupdate(&MD, tmpchunk, (len-i)*8);

  sum_put(&MD,sum);

  close(fd);
  unmap_file(buf,size);
}


void checksum_init(void)
{
}


#ifdef CHECKSUM_MAIN
 int main(int argc,char *argv[])
{
  char sum[SUM_LENGTH];
  int i,j;

  checksum_init();

  for (i=1;i<argc;i++) {
    struct stat st;
    if (stat(argv[i],&st) == 0) {
      file_checksum(argv[i],sum,st.st_size);
      for (j=0;j<SUM_LENGTH;j++)
	printf("%02X",(unsigned char)sum[j]);
      printf("  %s\n",argv[i]);
    }
  }
}
#endif
