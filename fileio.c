/* 
   Copyright (C) Andrew Tridgell 1998
   
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


static int write_sparse(int f,char *buf,int len)
{
	int l1=0,l2=0;
	int ret;

	for (l1=0;l1<len && buf[l1]==0;l1++) ;
	for (l2=0;l2<(len-l1) && buf[len-(l2+1)]==0;l2++) ;

	last_byte = buf[len-1];

	if (l1 == len || l2 > 0)
		last_sparse=1;

	if (l1 > 0)
		do_lseek(f,l1,SEEK_CUR);  

	if (l1 == len) 
		return len;

	if ((ret=write(f,buf+l1,len-(l1+l2))) != len-(l1+l2)) {
		if (ret == -1 || ret == 0) return ret;
		return (l1+ret);
	}

	if (l2 > 0)
		do_lseek(f,l2,SEEK_CUR);
	
	return len;
}



int write_file(int f,char *buf,int len)
{
	int ret = 0;

	if (!sparse_files) 
		return write(f,buf,len);

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



struct map_struct *map_file(int fd,OFF_T len)
{
	struct map_struct *ret;
	ret = (struct map_struct *)malloc(sizeof(*ret));
	if (!ret) out_of_memory("map_file");

	ret->map = NULL;
	ret->fd = fd;
	ret->size = len;
	ret->p = NULL;
	ret->p_size = 0;
	ret->p_offset = 0;
	ret->p_len = 0;

#ifdef USE_MMAP
	len = MIN(len, MAX_MAP_SIZE);
	ret->map = (char *)do_mmap(NULL,len,PROT_READ,MAP_SHARED,fd,0);
	if (ret->map == (char *)-1) {
		ret->map = NULL;
	} else {
		ret->p_len = len;
	}
#endif
	return ret;
}


char *map_ptr(struct map_struct *map,OFF_T offset,int len)
{
	int nread;

	if (len == 0) 
		return NULL;

	if (len > (map->size-offset))
		len = map->size-offset;

#ifdef USE_MMAP
	if (map->map) {
		if (offset >= map->p_offset && 
		    offset+len <= map->p_offset+map->p_len) {
			return (map->map + (offset - map->p_offset));
		}
		if (munmap(map->map, map->p_len) != 0) {
			rprintf(FERROR,"munmap failed : %s\n", strerror(errno));
			exit_cleanup(1);
		}

		/* align the mmap region on a nice boundary back a bit from
		   where it is asked for to allow for some seeking */
		if (offset > 2*CHUNK_SIZE) {
			map->p_offset = offset - 2*CHUNK_SIZE;
			map->p_offset &= ~((OFF_T)(CHUNK_SIZE-1));
		} else {
			map->p_offset = 0;
		}
		
		/* map up to MAX_MAP_SIZE */
		map->p_len = MAX(len, MAX_MAP_SIZE);
		map->p_len = MIN(map->p_len, map->size - map->p_offset);

		map->map = (char *)do_mmap(NULL,map->p_len,PROT_READ,
					   MAP_SHARED,map->fd,map->p_offset);

		if (map->map == (char *)-1) {
			map->map = NULL;
			map->p_len = 0;
			map->p_offset = 0;
		} else {
			return (map->map + (offset - map->p_offset));
		}
	}
#endif

	if (offset >= map->p_offset && 
	    offset+len <= map->p_offset+map->p_len) {
		return (map->p + (offset - map->p_offset));
	}

	len = MAX(len,CHUNK_SIZE);
	if (len > (map->size-offset))
		len = map->size-offset;

	if (len > map->p_size) {
		if (map->p) free(map->p);
		map->p = (char *)malloc(len);
		if (!map->p) out_of_memory("map_ptr");
		map->p_size = len;
	}

	map->p_offset = offset;
	map->p_len = len;

	if (do_lseek(map->fd,offset,SEEK_SET) != offset) {
		rprintf(FERROR,"lseek failed in map_ptr\n");
		exit_cleanup(1);
	}

	if ((nread=read(map->fd,map->p,len)) != len) {		
		if (nread < 0) nread = 0;
		/* the best we can do is zero the buffer - the file
                   has changed mid transfer! */
		memset(map->p+nread, 0, len - nread);
	}
  
	return map->p; 
}


void unmap_file(struct map_struct *map)
{
#ifdef USE_MMAP
	if (map->map) {
		munmap(map->map,map->p_len);
		map->map = NULL;
	}
#endif
	if (map->p) {
		free(map->p);
		map->p = NULL;
	}
	memset(map, 0, sizeof(*map));
	free(map);
}

