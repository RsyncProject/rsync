/*
 * Routines used by the file-transfer code.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003-2009 Wayne Davison
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

#include "rsync.h"
#include "ifuncs.h"
#include "zlib/zlib.h"

extern int do_compression;
extern int module_id;
extern int def_compress_level;
extern char *skip_compress;

static int compression_level, per_file_default_level;

struct suffix_tree {
	struct suffix_tree *sibling;
	struct suffix_tree *child;
	char letter, word_end;
};

static char *match_list;
static struct suffix_tree *suftree;

static void add_suffix(struct suffix_tree **prior, char ltr, const char *str)
{
	struct suffix_tree *node, *newnode;

	if (ltr == '[') {
		const char *after = strchr(str, ']');
		/* Treat "[foo" and "[]" as having a literal '['. */
		if (after && after++ != str+1) {
			while ((ltr = *str++) != ']')
				add_suffix(prior, ltr, after);
			return;
		}
	}

	for (node = *prior; node; prior = &node->sibling, node = node->sibling) {
		if (node->letter == ltr) {
			if (*str)
				add_suffix(&node->child, *str, str+1);
			else
				node->word_end = 1;
			return;
		}
		if (node->letter > ltr)
			break;
	}
	if (!(newnode = new(struct suffix_tree)))
		out_of_memory("add_suffix");
	newnode->sibling = node;
	newnode->child = NULL;
	newnode->letter = ltr;
	*prior = newnode;
	if (*str) {
		add_suffix(&newnode->child, *str, str+1);
		newnode->word_end = 0;
	} else
		newnode->word_end = 1;
}

static void add_nocompress_suffixes(const char *str)
{
	char *buf, *t;
	const char *f = str;

	if (!(buf = new_array(char, strlen(f) + 1)))
		out_of_memory("add_nocompress_suffixes");

	while (*f) {
		if (*f == '/') {
			f++;
			continue;
		}

		t = buf;
		do {
			if (isUpper(f))
				*t++ = toLower(f);
			else
				*t++ = *f;
		} while (*++f != '/' && *f);
		*t++ = '\0';

		add_suffix(&suftree, *buf, buf+1);
	}

	free(buf);
}

static void init_set_compression(void)
{
	const char *f;
	char *t, *start;

	if (skip_compress)
		add_nocompress_suffixes(skip_compress);

	/* A non-daemon transfer skips the default suffix list if the
	 * user specified --skip-compress. */
	if (skip_compress && module_id < 0)
		f = "";
	else
		f = lp_dont_compress(module_id);

	if (!(match_list = t = new_array(char, strlen(f) + 2)))
		out_of_memory("set_compression");

	per_file_default_level = def_compress_level;

	while (*f) {
		if (*f == ' ') {
			f++;
			continue;
		}

		start = t;
		do {
			if (isUpper(f))
				*t++ = toLower(f);
			else
				*t++ = *f;
		} while (*++f != ' ' && *f);
		*t++ = '\0';

		if (t - start == 1+1 && *start == '*') {
			/* Optimize a match-string of "*". */
			*match_list = '\0';
			suftree = NULL;
			per_file_default_level = 0;
			break;
		}

		/* Move *.foo items into the stuffix tree. */
		if (*start == '*' && start[1] == '.' && start[2]
		 && !strpbrk(start+2, ".?*")) {
			add_suffix(&suftree, start[2], start+3);
			t = start;
		}
	}
	*t++ = '\0';
}

/* determine the compression level based on a wildcard filename list */
void set_compression(const char *fname)
{
	const struct suffix_tree *node;
	const char *s;
	char ltr;

	if (!do_compression)
		return;

	if (!match_list)
		init_set_compression();

	compression_level = per_file_default_level;

	if (!*match_list && !suftree)
		return;

	if ((s = strrchr(fname, '/')) != NULL)
		fname = s + 1;

	for (s = match_list; *s; s += strlen(s) + 1) {
		if (iwildmatch(s, fname)) {
			compression_level = 0;
			return;
		}
	}

	if (!(node = suftree) || !(s = strrchr(fname, '.'))
	 || s == fname || !(ltr = *++s))
		return;

	while (1) {
		if (isUpper(&ltr))
			ltr = toLower(&ltr);
		while (node->letter != ltr) {
			if (node->letter > ltr)
				return;
			if (!(node = node->sibling))
				return;
		}
		if ((ltr = *++s) == '\0') {
			if (node->word_end)
				compression_level = 0;
			return;
		}
		if (!(node = node->child))
			return;
	}
}

/* non-compressing recv token */
static int32 simple_recv_token(int f, char **data)
{
	static int32 residue;
	static char *buf;
	int32 n;

	if (!buf) {
		buf = new_array(char, CHUNK_SIZE);
		if (!buf)
			out_of_memory("simple_recv_token");
	}

	if (residue == 0) {
		int32 i = read_int(f);
		if (i <= 0)
			return i;
		residue = i;
	}

	*data = buf;
	n = MIN(CHUNK_SIZE,residue);
	residue -= n;
	read_buf(f,buf,n);
	return n;
}

/* non-compressing send token */
static void simple_send_token(int f, int32 token, struct map_struct *buf,
			      OFF_T offset, int32 n)
{
	if (n > 0) {
		int32 len = 0;
		while (len < n) {
			int32 n1 = MIN(CHUNK_SIZE, n-len);
			write_int(f, n1);
			write_buf(f, map_ptr(buf, offset+len, n1), n1);
			len += n1;
		}
	}
	/* a -2 token means to send data only and no token */
	if (token != -2)
		write_int(f, -(token+1));
}

/* Flag bytes in compressed stream are encoded as follows: */
#define END_FLAG	0	/* that's all folks */
#define TOKEN_LONG	0x20	/* followed by 32-bit token number */
#define TOKENRUN_LONG	0x21	/* ditto with 16-bit run count */
#define DEFLATED_DATA	0x40	/* + 6-bit high len, then low len byte */
#define TOKEN_REL	0x80	/* + 6-bit relative token number */
#define TOKENRUN_REL	0xc0	/* ditto with 16-bit run count */

#define MAX_DATA_COUNT	16383	/* fit 14 bit count into 2 bytes with flags */

/* zlib.h says that if we want to be able to compress something in a single
 * call, avail_out must be at least 0.1% larger than avail_in plus 12 bytes.
 * We'll add in 0.1%+16, just to be safe (and we'll avoid floating point,
 * to ensure that this is a compile-time value). */
#define AVAIL_OUT_SIZE(avail_in_size) ((avail_in_size)*1001/1000+16)

/* For coding runs of tokens */
static int32 last_token = -1;
static int32 run_start;
static int32 last_run_end;

/* Deflation state */
static z_stream tx_strm;

/* Output buffer */
static char *obuf;

/* We want obuf to be able to hold both MAX_DATA_COUNT+2 bytes as well as
 * AVAIL_OUT_SIZE(CHUNK_SIZE) bytes, so make sure that it's large enough. */
#if MAX_DATA_COUNT+2 > AVAIL_OUT_SIZE(CHUNK_SIZE)
#define OBUF_SIZE	(MAX_DATA_COUNT+2)
#else
#define OBUF_SIZE	AVAIL_OUT_SIZE(CHUNK_SIZE)
#endif

/* Send a deflated token */
static void
send_deflated_token(int f, int32 token, struct map_struct *buf, OFF_T offset,
		    int32 nb, int32 toklen)
{
	int32 n, r;
	static int init_done, flush_pending;

	if (last_token == -1) {
		/* initialization */
		if (!init_done) {
			tx_strm.next_in = NULL;
			tx_strm.zalloc = NULL;
			tx_strm.zfree = NULL;
			if (deflateInit2(&tx_strm, compression_level,
					 Z_DEFLATED, -15, 8,
					 Z_DEFAULT_STRATEGY) != Z_OK) {
				rprintf(FERROR, "compression init failed\n");
				exit_cleanup(RERR_STREAMIO);
			}
			if ((obuf = new_array(char, OBUF_SIZE)) == NULL)
				out_of_memory("send_deflated_token");
			init_done = 1;
		} else
			deflateReset(&tx_strm);
		last_run_end = 0;
		run_start = token;
		flush_pending = 0;
	} else if (last_token == -2) {
		run_start = token;
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

	if (nb != 0 || flush_pending) {
		/* deflate the data starting at offset */
		int flush = Z_NO_FLUSH;
		tx_strm.avail_in = 0;
		tx_strm.avail_out = 0;
		do {
			if (tx_strm.avail_in == 0 && nb != 0) {
				/* give it some more input */
				n = MIN(nb, CHUNK_SIZE);
				tx_strm.next_in = (Bytef *)
					map_ptr(buf, offset, n);
				tx_strm.avail_in = n;
				nb -= n;
				offset += n;
			}
			if (tx_strm.avail_out == 0) {
				tx_strm.next_out = (Bytef *)(obuf + 2);
				tx_strm.avail_out = MAX_DATA_COUNT;
				if (flush != Z_NO_FLUSH) {
					/*
					 * We left the last 4 bytes in the
					 * buffer, in case they are the
					 * last 4.  Move them to the front.
					 */
					memcpy(tx_strm.next_out,
					       obuf+MAX_DATA_COUNT-2, 4);
					tx_strm.next_out += 4;
					tx_strm.avail_out -= 4;
				}
			}
			if (nb == 0 && token != -2)
				flush = Z_SYNC_FLUSH;
			r = deflate(&tx_strm, flush);
			if (r != Z_OK) {
				rprintf(FERROR, "deflate returned %d\n", r);
				exit_cleanup(RERR_STREAMIO);
			}
			if (nb == 0 || tx_strm.avail_out == 0) {
				n = MAX_DATA_COUNT - tx_strm.avail_out;
				if (flush != Z_NO_FLUSH) {
					/*
					 * We have to trim off the last 4
					 * bytes of output when flushing
					 * (they are just 0, 0, ff, ff).
					 */
					n -= 4;
				}
				if (n > 0) {
					obuf[0] = DEFLATED_DATA + (n >> 8);
					obuf[1] = n;
					write_buf(f, obuf, n+2);
				}
			}
		} while (nb != 0 || tx_strm.avail_out == 0);
		flush_pending = token == -2;
	}

	if (token == -1) {
		/* end of file - clean up */
		write_byte(f, END_FLAG);
	} else if (token != -2) {
		/* Add the data in the current block to the compressor's
		 * history and hash table. */
		do {
			/* Break up long sections in the same way that
			 * see_deflate_token() does. */
			int32 n1 = toklen > 0xffff ? 0xffff : toklen;
			toklen -= n1;
			tx_strm.next_in = (Bytef *)map_ptr(buf, offset, n1);
			tx_strm.avail_in = n1;
			tx_strm.next_out = (Bytef *) obuf;
			tx_strm.avail_out = AVAIL_OUT_SIZE(CHUNK_SIZE);
			r = deflate(&tx_strm, Z_INSERT_ONLY);
			if (r != Z_OK || tx_strm.avail_in != 0) {
				rprintf(FERROR, "deflate on token returned %d (%d bytes left)\n",
					r, tx_strm.avail_in);
				exit_cleanup(RERR_STREAMIO);
			}
		} while (toklen > 0);
	}
}

/* tells us what the receiver is in the middle of doing */
static enum { r_init, r_idle, r_running, r_inflating, r_inflated } recv_state;

/* for inflating stuff */
static z_stream rx_strm;
static char *cbuf;
static char *dbuf;

/* for decoding runs of tokens */
static int32 rx_token;
static int32 rx_run;

/* Receive a deflated token and inflate it */
static int32 recv_deflated_token(int f, char **data)
{
	static int init_done;
	static int32 saved_flag;
	int32 n, flag;
	int r;

	for (;;) {
		switch (recv_state) {
		case r_init:
			if (!init_done) {
				rx_strm.next_out = NULL;
				rx_strm.zalloc = NULL;
				rx_strm.zfree = NULL;
				if (inflateInit2(&rx_strm, -15) != Z_OK) {
					rprintf(FERROR, "inflate init failed\n");
					exit_cleanup(RERR_STREAMIO);
				}
				if (!(cbuf = new_array(char, MAX_DATA_COUNT))
				    || !(dbuf = new_array(char, AVAIL_OUT_SIZE(CHUNK_SIZE))))
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
			if (saved_flag) {
				flag = saved_flag & 0xff;
				saved_flag = 0;
			} else
				flag = read_byte(f);
			if ((flag & 0xC0) == DEFLATED_DATA) {
				n = ((flag & 0x3f) << 8) + read_byte(f);
				read_buf(f, cbuf, n);
				rx_strm.next_in = (Bytef *)cbuf;
				rx_strm.avail_in = n;
				recv_state = r_inflating;
				break;
			}
			if (recv_state == r_inflated) {
				/* check previous inflated stuff ended correctly */
				rx_strm.avail_in = 0;
				rx_strm.next_out = (Bytef *)dbuf;
				rx_strm.avail_out = AVAIL_OUT_SIZE(CHUNK_SIZE);
				r = inflate(&rx_strm, Z_SYNC_FLUSH);
				n = AVAIL_OUT_SIZE(CHUNK_SIZE) - rx_strm.avail_out;
				/*
				 * Z_BUF_ERROR just means no progress was
				 * made, i.e. the decompressor didn't have
				 * any pending output for us.
				 */
				if (r != Z_OK && r != Z_BUF_ERROR) {
					rprintf(FERROR, "inflate flush returned %d (%d bytes)\n",
						r, n);
					exit_cleanup(RERR_STREAMIO);
				}
				if (n != 0 && r != Z_BUF_ERROR) {
					/* have to return some more data and
					   save the flag for later. */
					saved_flag = flag + 0x10000;
					*data = dbuf;
					return n;
				}
				/*
				 * At this point the decompressor should
				 * be expecting to see the 0, 0, ff, ff bytes.
				 */
				if (!inflateSyncPoint(&rx_strm)) {
					rprintf(FERROR, "decompressor lost sync!\n");
					exit_cleanup(RERR_STREAMIO);
				}
				rx_strm.avail_in = 4;
				rx_strm.next_in = (Bytef *)cbuf;
				cbuf[0] = cbuf[1] = 0;
				cbuf[2] = cbuf[3] = 0xff;
				inflate(&rx_strm, Z_SYNC_FLUSH);
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
			rx_strm.next_out = (Bytef *)dbuf;
			rx_strm.avail_out = AVAIL_OUT_SIZE(CHUNK_SIZE);
			r = inflate(&rx_strm, Z_NO_FLUSH);
			n = AVAIL_OUT_SIZE(CHUNK_SIZE) - rx_strm.avail_out;
			if (r != Z_OK) {
				rprintf(FERROR, "inflate returned %d (%d bytes)\n", r, n);
				exit_cleanup(RERR_STREAMIO);
			}
			if (rx_strm.avail_in == 0)
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
static void see_deflate_token(char *buf, int32 len)
{
	int r;
	int32 blklen;
	unsigned char hdr[5];

	rx_strm.avail_in = 0;
	blklen = 0;
	hdr[0] = 0;
	do {
		if (rx_strm.avail_in == 0 && len != 0) {
			if (blklen == 0) {
				/* Give it a fake stored-block header. */
				rx_strm.next_in = (Bytef *)hdr;
				rx_strm.avail_in = 5;
				blklen = len;
				if (blklen > 0xffff)
					blklen = 0xffff;
				hdr[1] = blklen;
				hdr[2] = blklen >> 8;
				hdr[3] = ~hdr[1];
				hdr[4] = ~hdr[2];
			} else {
				rx_strm.next_in = (Bytef *)buf;
				rx_strm.avail_in = blklen;
				len -= blklen;
				blklen = 0;
			}
		}
		rx_strm.next_out = (Bytef *)dbuf;
		rx_strm.avail_out = AVAIL_OUT_SIZE(CHUNK_SIZE);
		r = inflate(&rx_strm, Z_SYNC_FLUSH);
		if (r != Z_OK && r != Z_BUF_ERROR) {
			rprintf(FERROR, "inflate (token) returned %d\n", r);
			exit_cleanup(RERR_STREAMIO);
		}
	} while (len || rx_strm.avail_out == 0);
}

/**
 * Transmit a verbatim buffer of length @p n followed by a token.
 * If token == -1 then we have reached EOF
 * If n == 0 then don't send a buffer
 */
void send_token(int f, int32 token, struct map_struct *buf, OFF_T offset,
		int32 n, int32 toklen)
{
	if (!do_compression)
		simple_send_token(f, token, buf, offset, n);
	else
		send_deflated_token(f, token, buf, offset, n, toklen);
}

/*
 * receive a token or buffer from the other end. If the reurn value is >0 then
 * it is a data buffer of that length, and *data will point at the data.
 * if the return value is -i then it represents token i-1
 * if the return value is 0 then the end has been reached
 */
int32 recv_token(int f, char **data)
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
void see_token(char *data, int32 toklen)
{
	if (do_compression)
		see_deflate_token(data, toklen);
}
