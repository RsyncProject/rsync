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

extern int do_compression;


/* non-compressing recv token */
static int simple_recv_token(int f,char **data)
{
  static int residue = 0;
  static char *buf = NULL;
  int n;

  if (!buf) {
    buf = (char *)malloc(CHUNK_SIZE);
    if (!buf) out_of_memory("simple_recv_token");
  }


  if (residue == 0) {
    int i = read_int(f);
    if (i <= 0) return i;
    residue = i;
  }

  *data = buf;
  n = MIN(CHUNK_SIZE,residue);
  residue -= n;
  read_buf(f,buf,n);
  return n;
}


/* non-compressing send token */
static void simple_send_token(int f,int token,
			      struct map_struct *buf,int offset,int n)
{
  if (n > 0) {
    int l = 0;
    while (l < n) {
      int n1 = MIN(CHUNK_SIZE,n-l);
      write_int(f,n1);
      write_buf(f,map_ptr(buf,offset+l,n1),n1);
      l += n1;
    }
  }
  write_int(f,-(token+1));
}




/*
 * transmit a verbatim buffer of length n followed by a token 
 * If token == -1 then we have reached EOF 
 * If n == 0 then don't send a buffer
 */
void send_token(int f,int token,struct map_struct *buf,int offset,int n)
{
  if (!do_compression) {
    simple_send_token(f,token,buf,offset,n);
    return;
  }

  /* compressed transmit here */
}


/*
 * receive a token or buffer from the other end. If the reurn value is >0 then
 * it is a data buffer of that length, and *data will point at the data.
 * if the return value is -i then it represents token i-1
 * if the return value is 0 then the end has been reached
 */
int recv_token(int f,char **data)
{
  if (!do_compression) {
    return simple_recv_token(f,data);
  }

  /* compressed receive here */
  return 0;
}
