/*
 * File IO utilities used in rsync.
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2004-2009 Wayne Davison
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

#ifndef ENODATA
#define ENODATA EAGAIN
#endif

extern int sparse_files;

static OFF_T sparse_seek = 0;

int sparse_end(int f, OFF_T size)
{
	int ret;

	if (!sparse_seek)
		return 0;

#ifdef HAVE_FTRUNCATE
	ret = do_ftruncate(f, size);
#else
	if (do_lseek(f, sparse_seek-1, SEEK_CUR) != size-1)
		ret = -1;
	else {
		do {
			ret = write(f, "", 1);
		} while (ret < 0 && errno == EINTR);

		ret = ret <= 0 ? -1 : 0;
	}
#endif

	sparse_seek = 0;

	return ret;
}


static int write_sparse(int f, char *buf, int len)
{
	int l1 = 0, l2 = 0;
	int ret;

	for (l1 = 0; l1 < len && buf[l1] == 0; l1++) {}
	for (l2 = 0; l2 < len-l1 && buf[len-(l2+1)] == 0; l2++) {}

	sparse_seek += l1;

	if (l1 == len)
		return len;

	if (sparse_seek)
		do_lseek(f, sparse_seek, SEEK_CUR);
	sparse_seek = l2;

	while ((ret = write(f, buf + l1, len - (l1+l2))) <= 0) {
		if (ret < 0 && errno == EINTR)
			continue;
		return ret;
	}

	if (ret != (int)(len - (l1+l2)))
		return l1+ret;

	return len;
}


static char *wf_writeBuf;
static size_t wf_writeBufSize;
static size_t wf_writeBufCnt;

int flush_write_file(int f)
{
	int ret = 0;
	char *bp = wf_writeBuf;

	while (wf_writeBufCnt > 0) {
		if ((ret = write(f, bp, wf_writeBufCnt)) < 0) {
			if (errno == EINTR)
				continue;
			return ret;
		}
		wf_writeBufCnt -= ret;
		bp += ret;
	}
	return ret;
}


/*
 * write_file does not allow incomplete writes.  It loops internally
 * until len bytes are written or errno is set.
 */
int write_file(int f, char *buf, int len)
{
	int ret = 0;

	while (len > 0) {
		int r1;
		if (sparse_files > 0) {
			int len1 = MIN(len, SPARSE_WRITE_SIZE);
			r1 = write_sparse(f, buf, len1);
		} else {
			if (!wf_writeBuf) {
				wf_writeBufSize = WRITE_SIZE * 8;
				wf_writeBufCnt  = 0;
				wf_writeBuf = new_array(char, wf_writeBufSize);
				if (!wf_writeBuf)
					out_of_memory("write_file");
			}
			r1 = (int)MIN((size_t)len, wf_writeBufSize - wf_writeBufCnt);
			if (r1) {
				memcpy(wf_writeBuf + wf_writeBufCnt, buf, r1);
				wf_writeBufCnt += r1;
			}
			if (wf_writeBufCnt == wf_writeBufSize) {
				if (flush_write_file(f) < 0)
					return -1;
				if (!r1 && len)
					continue;
			}
		}
		if (r1 <= 0) {
			if (ret > 0)
				return ret;
			return r1;
		}
		len -= r1;
		buf += r1;
		ret += r1;
	}
	return ret;
}


/* This provides functionality somewhat similar to mmap() but using read().
 * It gives sliding window access to a file.  mmap() is not used because of
 * the possibility of another program (such as a mailer) truncating the
 * file thus giving us a SIGBUS. */
struct map_struct *map_file(int fd, OFF_T len, int32 read_size,
			    int32 blk_size)
{
	struct map_struct *map;

	if (!(map = new0(struct map_struct)))
		out_of_memory("map_file");

	if (blk_size && (read_size % blk_size))
		read_size += blk_size - (read_size % blk_size);

	map->fd = fd;
	map->file_size = len;
	map->def_window_size = read_size;

	return map;
}


/* slide the read window in the file */
char *map_ptr(struct map_struct *map, OFF_T offset, int32 len)
{
	int32 nread;
	OFF_T window_start, read_start;
	int32 window_size, read_size, read_offset;

	if (len == 0)
		return NULL;
	if (len < 0) {
		rprintf(FERROR, "invalid len passed to map_ptr: %ld\n",
			(long)len);
		exit_cleanup(RERR_FILEIO);
	}

	/* in most cases the region will already be available */
	if (offset >= map->p_offset && offset+len <= map->p_offset+map->p_len)
		return map->p + (offset - map->p_offset);

	/* nope, we are going to have to do a read. Work out our desired window */
	window_start = offset;
	window_size = map->def_window_size;
	if (window_start + window_size > map->file_size)
		window_size = (int32)(map->file_size - window_start);
	if (len > window_size)
		window_size = len;

	/* make sure we have allocated enough memory for the window */
	if (window_size > map->p_size) {
		map->p = realloc_array(map->p, char, window_size);
		if (!map->p)
			out_of_memory("map_ptr");
		map->p_size = window_size;
	}

	/* Now try to avoid re-reading any bytes by reusing any bytes
	 * from the previous buffer. */
	if (window_start >= map->p_offset &&
	    window_start < map->p_offset + map->p_len &&
	    window_start + window_size >= map->p_offset + map->p_len) {
		read_start = map->p_offset + map->p_len;
		read_offset = (int32)(read_start - window_start);
		read_size = window_size - read_offset;
		memmove(map->p, map->p + (map->p_len - read_offset), read_offset);
	} else {
		read_start = window_start;
		read_size = window_size;
		read_offset = 0;
	}

	if (read_size <= 0) {
		rprintf(FERROR, "invalid read_size of %ld in map_ptr\n",
			(long)read_size);
		exit_cleanup(RERR_FILEIO);
	}

	if (map->p_fd_offset != read_start) {
		OFF_T ret = do_lseek(map->fd, read_start, SEEK_SET);
		if (ret != read_start) {
			rsyserr(FERROR, errno, "lseek returned %.0f, not %.0f",
				(double)ret, (double)read_start);
			exit_cleanup(RERR_FILEIO);
		}
		map->p_fd_offset = read_start;
	}
	map->p_offset = window_start;
	map->p_len = window_size;

	while (read_size > 0) {
		nread = read(map->fd, map->p + read_offset, read_size);
		if (nread <= 0) {
			if (!map->status)
				map->status = nread ? errno : ENODATA;
			/* The best we can do is zero the buffer -- the file
			 * has changed mid transfer! */
			memset(map->p + read_offset, 0, read_size);
			break;
		}
		map->p_fd_offset += nread;
		read_offset += nread;
		read_size -= nread;
	}

	return map->p;
}


int unmap_file(struct map_struct *map)
{
	int	ret;

	if (map->p) {
		free(map->p);
		map->p = NULL;
	}
	ret = map->status;
	memset(map, 0, sizeof map[0]);
	free(map);

	return ret;
}
