/* 
   Copyright (C) Andrew Tridgell 1998
   Copyright (C) 2002 by Martin Pool
   
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

/*
  File IO utilities used in rsync 
  */
#include "rsync.h"

static char last_byte;
static int last_sparse;
extern int sparse_files;

int sparse_end(int f)
{
	if (last_sparse) {
		do_lseek(f,-1,SEEK_CUR);
		return (write(f,&last_byte,1) == 1 ? 0 : -1);
	}
	last_sparse = 0;
	return 0;
}


static int write_sparse(int f,char *buf,size_t len)
{
	size_t l1=0, l2=0;
	int ret;

	for (l1=0;l1<len && buf[l1]==0;l1++) ;
	for (l2=0;l2<(len-l1) && buf[len-(l2+1)]==0;l2++) ;

	last_byte = buf[len-1];

	if (l1 == len || l2 > 0)
		last_sparse=1;

	if (l1 > 0) {
		do_lseek(f,l1,SEEK_CUR);  
	}

	if (l1 == len) 
		return len;

	ret = write(f, buf + l1, len - (l1+l2));
	if (ret == -1 || ret == 0)
		return ret;
	else if (ret != (int) (len - (l1+l2))) 
		return (l1+ret);

	if (l2 > 0)
		do_lseek(f,l2,SEEK_CUR);
	
	return len;
}



int write_file(int f,char *buf,size_t len)
{
	int ret = 0;

	if (!sparse_files) {
		return write(f,buf,len);
	}

	while (len>0) {
		int len1 = MIN(len, SPARSE_WRITE_SIZE);
		int r1 = write_sparse(f, buf, len1);
		if (r1 <= 0) {
			if (ret > 0) return ret;
			return r1;
		}
		len -= r1;
		buf += r1;
		ret += r1;
	}
	return ret;
}



/* this provides functionality somewhat similar to mmap() but using
   read(). It gives sliding window access to a file. mmap() is not
   used because of the possibility of another program (such as a
   mailer) truncating the file thus giving us a SIGBUS */
struct map_struct *map_file(int fd,OFF_T len)
{
	struct map_struct *map;
	map = new(struct map_struct);
	if (!map) out_of_memory("map_file");

	map->fd = fd;
	map->file_size = len;
	map->p = NULL;
	map->p_size = 0;
	map->p_offset = 0;
	map->p_fd_offset = 0;
	map->p_len = 0;

	return map;
}

/* slide the read window in the file */
char *map_ptr(struct map_struct *map,OFF_T offset,int len)
{
	int nread;
	OFF_T window_start, read_start;
	int window_size, read_size, read_offset;

	if (len == 0) {
		return NULL;
	}

	/* can't go beyond the end of file */
	if (len > (map->file_size - offset)) {
		len = map->file_size - offset;
	}

	/* in most cases the region will already be available */
	if (offset >= map->p_offset && 
	    offset+len <= map->p_offset+map->p_len) {
		return (map->p + (offset - map->p_offset));
	}


	/* nope, we are going to have to do a read. Work out our desired window */
	if (offset > 2*CHUNK_SIZE) {
		window_start = offset - 2*CHUNK_SIZE;
		window_start &= ~((OFF_T)(CHUNK_SIZE-1)); /* assumes power of 2 */
	} else {
		window_start = 0;
	}
	window_size = MAX_MAP_SIZE;
	if (window_start + window_size > map->file_size) {
		window_size = map->file_size - window_start;
	}
	if (offset + len > window_start + window_size) {
		window_size = (offset+len) - window_start;
	}

	/* make sure we have allocated enough memory for the window */
	if (window_size > map->p_size) {
		map->p = realloc_array(map->p, char, window_size);
		if (!map->p) out_of_memory("map_ptr");
		map->p_size = window_size;
	}

	/* now try to avoid re-reading any bytes by reusing any bytes from the previous
	   buffer. */
	if (window_start >= map->p_offset &&
	    window_start < map->p_offset + map->p_len &&
	    window_start + window_size >= map->p_offset + map->p_len) {
		read_start = map->p_offset + map->p_len;
		read_offset = read_start - window_start;
		read_size = window_size - read_offset;
		memmove(map->p, map->p + (map->p_len - read_offset), read_offset);
	} else {
		read_start = window_start;
		read_size = window_size;
		read_offset = 0;
	}

	if (read_size <= 0) {
		rprintf(FINFO,"Warning: unexpected read size of %d in map_ptr\n", read_size);
	} else {
		if (map->p_fd_offset != read_start) {
			if (do_lseek(map->fd,read_start,SEEK_SET) != read_start) {
				rprintf(FERROR,"lseek failed in map_ptr\n");
				exit_cleanup(RERR_FILEIO);
			}
			map->p_fd_offset = read_start;
		}

		if ((nread=read(map->fd,map->p + read_offset,read_size)) != read_size) {
			if (nread < 0) nread = 0;
			/* the best we can do is zero the buffer - the file
			   has changed mid transfer! */
			memset(map->p+read_offset+nread, 0, read_size - nread);
		}
		map->p_fd_offset += nread;
	}

	map->p_offset = window_start;
	map->p_len = window_size;
  
	return map->p + (offset - map->p_offset); 
}


void unmap_file(struct map_struct *map)
{
	if (map->p) {
		free(map->p);
		map->p = NULL;
	}
	memset(map, 0, sizeof(*map));
	free(map);
}

