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


/*
  a simple 32 bit checksum that can be upadted from either end
  (inspired by Mark Adler's Adler-32 checksum)
  */
uint32 get_checksum1(char *buf,int len)
{
    int i;
    uint32 s1, s2;

    s1 = s2 = 0;
    for (i = 0; i < len; i++) {
	s1 += buf[i];
	s2 += s1;
    }
    return (s1 & 0xffff) + (s2 << 16);
}


void get_checksum2(char *buf,int len,char *sum)
{
  char buf2[64];
  int i;
  MDstruct MD;

  MDbegin(&MD);
  for(i = 0; i + 64 <= len; i += 64) {
    bcopy(buf+i,buf2,64);
    MDupdate(&MD, buf2, 512);
  }
  bcopy(buf+i,buf2,len-i);
  MDupdate(&MD, buf2, (len-i)*8);
  SIVAL(sum,0,MD.buffer[0]);
  SIVAL(sum,4,MD.buffer[1]);
  SIVAL(sum,8,MD.buffer[2]);
  SIVAL(sum,12,MD.buffer[3]);
}

void file_checksum(char *fname,char *sum,off_t size)
{
  char *buf;
  int fd;
  bzero(sum,SUM_LENGTH);

  fd = open(fname,O_RDONLY);
  if (fd == -1) return;

  buf = map_file(fd,size);
  if (!buf) {
    close(fd);
    return;
  }

  get_checksum2(buf,size,sum);
  close(fd);
  unmap_file(buf,size);
}
