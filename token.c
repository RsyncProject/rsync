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
#include "lib/zlib.h"

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


/* Memory allocation/freeing routines, called by zlib stuff. */
static void *
z_alloc(void *opaque, uInt items, uInt size)
{
    return malloc(items * size);
}

static void
z_free(void *opaque, void *adrs, uInt nbytes)
{
    free(adrs);
}

/* Flag bytes in compressed stream are encoded as follows: */
#define END_FLAG	0	/* that's all folks */
#define TOKEN_LONG	0x20	/* followed by 32-bit token number */
#define TOKENRUN_LONG	0x21	/* ditto with 16-bit run count */
#define DEFLATED_DATA	0x40	/* + 6-bit high len, then low len byte */
#define TOKEN_REL	0x80	/* + 6-bit relative token number */
#define TOKENRUN_REL	0xc0	/* ditto with 16-bit run count */

#define MAX_DATA_COUNT	16383	/* fit 14 bit count into 2 bytes with flags */

/* For coding runs of tokens */
static int last_token = -1;
static int run_start;
static int last_run_end;

/* Deflation state */
static z_stream tx_strm;

/* Output buffer */
static char *obuf = NULL;

/* Send a deflated token */
static void
send_deflated_token(int f, int token,
		    struct map_struct *buf, int offset, int nb, int toklen)
{
    int n, r;
    static int init_done;

    if (last_token == -1) {
	/* initialization */
	if (!init_done) {
	    tx_strm.next_in = NULL;
	    tx_strm.zalloc = z_alloc;
	    tx_strm.zfree = z_free;
	    if (deflateInit2(&tx_strm, Z_DEFAULT_COMPRESSION, 8,
			     -15, 8, Z_DEFAULT_STRATEGY) != Z_OK) {
		fprintf(FERROR, "compression init failed\n");
		exit_cleanup(1);
	    }
	    if ((obuf = malloc(MAX_DATA_COUNT+2)) == NULL)
		out_of_memory("send_deflated_token");
	    init_done = 1;
	} else
	    deflateReset(&tx_strm);
	run_start = token;
	last_run_end = 0;

    } else if (nb != 0 || token != last_token + 1
	       || token >= run_start + 65536) {
	/* output previous run */
	r = run_start - last_run_end;
	n = last_token - run_start;
	if (r >= 0 && r <= 63) {
	    write_byte(f, (n==0? TOKEN_REL: TOKENRUN_REL) + r);
	} else {
	    write_byte(f, (n==0? TOKEN_LONG: TOKENRUN_LONG));
	    write_int(f, run_start);
	}
	if (n != 0) {
	    write_byte(f, n);
	    write_byte(f, n >> 8);
	}
	last_run_end = last_token;
	run_start = token;
    }

    last_token = token;

    if (nb != 0) {
	/* deflate the data starting at offset */
	tx_strm.avail_in = 0;
	tx_strm.avail_out = 0;
	do {
	    if (tx_strm.avail_in == 0 && nb != 0) {
		/* give it some more input */
		n = MIN(nb, CHUNK_SIZE);
		tx_strm.next_in = map_ptr(buf, offset, n);
		tx_strm.avail_in = n;
		nb -= n;
		offset += n;
	    }
	    if (tx_strm.avail_out == 0) {
		tx_strm.next_out = obuf + 2;
		tx_strm.avail_out = MAX_DATA_COUNT;
	    }
	    r = deflate(&tx_strm, nb? Z_NO_FLUSH: Z_PACKET_FLUSH);
	    if (r != Z_OK) {
		fprintf(FERROR, "deflate returned %d\n", r);
		exit_cleanup(1);
	    }
	    if (nb == 0 || tx_strm.avail_out == 0) {
		n = MAX_DATA_COUNT - tx_strm.avail_out;
		if (n > 0) {
		    obuf[0] = DEFLATED_DATA + (n >> 8);
		    obuf[1] = n;
		    write_buf(f, obuf, n+2);
		}
	    }
	} while (nb != 0 || tx_strm.avail_out == 0);
    }

    if (token != -1) {
	/* add the data in the current block to the compressor's
	   history and hash table */
	tx_strm.next_in = map_ptr(buf, offset, toklen);
	tx_strm.avail_in = toklen;
	tx_strm.next_out = NULL;
	tx_strm.avail_out = 2 * toklen;
	r = deflate(&tx_strm, Z_INSERT_ONLY);
	if (r != Z_OK || tx_strm.avail_in != 0) {
	    fprintf(FERROR, "deflate on token returned %d (%d bytes left)\n",
		    r, tx_strm.avail_in);
	    exit_cleanup(1);
	}

    } else {
	/* end of file - clean up */
	write_byte(f, END_FLAG);
    }
}


/* tells us what the receiver is in the middle of doing */
static enum { r_init, r_idle, r_running, r_inflating, r_inflated } recv_state;

/* for inflating stuff */
static z_stream rx_strm;
static char *cbuf;
static char *dbuf;

/* for decoding runs of tokens */
static int rx_token;
static int rx_run;

/* Receive a deflated token and inflate it */
static int
recv_deflated_token(int f, char **data)
{
    int n, r, flag;
    static int init_done = 0;

    for (;;) {
	switch (recv_state) {
	case r_init:
	    if (!init_done) {
		rx_strm.next_out = NULL;
		rx_strm.zalloc = z_alloc;
		rx_strm.zfree = z_free;
		if (inflateInit2(&rx_strm, -15) != Z_OK) {
		    fprintf(FERROR, "inflate init failed\n");
		    exit_cleanup(1);
		}
		if ((cbuf = malloc(MAX_DATA_COUNT)) == NULL
		    || (dbuf = malloc(CHUNK_SIZE)) == NULL)
		    out_of_memory("recv_deflated_token");
		init_done = 1;
	    } else {
		inflateReset(&rx_strm);
	    }
	    recv_state = r_idle;
	    rx_token = 0;
	    break;
	    
	case r_idle:
	case r_inflated:
	    flag = read_byte(f);
	    if ((flag & 0xC0) == DEFLATED_DATA) {
		n = ((flag & 0x3f) << 8) + read_byte(f);
		read_buf(f, cbuf, n);
		rx_strm.next_in = cbuf;
		rx_strm.avail_in = n;
		recv_state = r_inflating;
		break;
	    }
	    if (recv_state == r_inflated) {
		/* check previous inflated stuff ended correctly */
		rx_strm.avail_in = 0;
		rx_strm.next_out = dbuf;
		rx_strm.avail_out = CHUNK_SIZE;
		r = inflate(&rx_strm, Z_PACKET_FLUSH);
		n = CHUNK_SIZE - rx_strm.avail_out;
		if (r != Z_OK || n != 0) {
		    fprintf(FERROR, "inflate flush returned %d (%d bytes)\n",
			    r, n);
		    exit_cleanup(1);
		}
		recv_state = r_idle;
	    }
	    if (flag == END_FLAG) {
		/* that's all folks */
		recv_state = r_init;
		return 0;
	    }

	    /* here we have a token of some kind */
	    if (flag & TOKEN_REL) {
		rx_token += flag & 0x3f;
		flag >>= 6;
	    } else
		rx_token = read_int(f);
	    if (flag & 1) {
		rx_run = read_byte(f);
		rx_run += read_byte(f) << 8;
		recv_state = r_running;
	    }
	    return -1 - rx_token;

	case r_inflating:
	    rx_strm.next_out = dbuf;
	    rx_strm.avail_out = CHUNK_SIZE;
	    r = inflate(&rx_strm, Z_NO_FLUSH);
	    n = CHUNK_SIZE - rx_strm.avail_out;
	    if (r != Z_OK) {
		fprintf(FERROR, "inflate returned %d (%d bytes)\n", r, n);
		exit_cleanup(1);
	    }
	    if (rx_strm.avail_out != 0)
		recv_state = r_inflated;
	    if (n != 0) {
		*data = dbuf;
		return n;
	    }
	    break;

	case r_running:
	    ++rx_token;
	    if (--rx_run == 0)
		recv_state = r_idle;
	    return -1 - rx_token;
	}
    }
}

/*
 * put the data corresponding to a token that we've just returned
 * from recv_deflated_token into the decompressor's history buffer.
 */
void
see_deflate_token(char *buf, int len)
{
    int r;

    rx_strm.next_in = buf;
    rx_strm.avail_in = len;
    r = inflateIncomp(&rx_strm);
    if (r != Z_OK) {
	fprintf(FERROR, "inflateIncomp returned %d\n", r);
	exit_cleanup(1);
    }
}

/*
 * transmit a verbatim buffer of length n followed by a token 
 * If token == -1 then we have reached EOF 
 * If n == 0 then don't send a buffer
 */
void send_token(int f,int token,struct map_struct *buf,int offset,
		int n,int toklen)
{
  if (!do_compression) {
    simple_send_token(f,token,buf,offset,n);
  } else {
    send_deflated_token(f, token, buf, offset, n, toklen);
  }
}


/*
 * receive a token or buffer from the other end. If the reurn value is >0 then
 * it is a data buffer of that length, and *data will point at the data.
 * if the return value is -i then it represents token i-1
 * if the return value is 0 then the end has been reached
 */
int recv_token(int f,char **data)
{
  int tok;

  if (!do_compression) {
    tok = simple_recv_token(f,data);
  } else {
    tok = recv_deflated_token(f, data);
  }
  return tok;
}

/*
 * look at the data corresponding to a token, if necessary
 */
void see_token(char *data, int toklen)
{
    if (do_compression)
	see_deflate_token(data, toklen);
}
