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

extern int verbose;
extern int remote_version;
extern int csum_length;
extern struct stats stats;
extern int io_error;
extern int dry_run;
extern int am_server;


/*
  receive the checksums for a buffer
  */
static struct sum_struct *receive_sums(int f)
{
	struct sum_struct *s;
	int i;
	OFF_T offset = 0;

	s = (struct sum_struct *)malloc(sizeof(*s));
	if (!s) out_of_memory("receive_sums");

	s->count = read_int(f);
	s->n = read_int(f);
	s->remainder = read_int(f);  
	s->sums = NULL;

	if (verbose > 3)
		rprintf(FINFO,"count=%d n=%d rem=%d\n",
			s->count,s->n,s->remainder);

	if (s->count == 0) 
		return(s);

	s->sums = (struct sum_buf *)malloc(sizeof(s->sums[0])*s->count);
	if (!s->sums) out_of_memory("receive_sums");

	for (i=0;i<s->count;i++) {
		s->sums[i].sum1 = read_int(f);
		read_buf(f,s->sums[i].sum2,csum_length);

		s->sums[i].offset = offset;
		s->sums[i].i = i;

		if (i == s->count-1 && s->remainder != 0) {
			s->sums[i].len = s->remainder;
		} else {
			s->sums[i].len = s->n;
		}
		offset += s->sums[i].len;

		if (verbose > 3)
			rprintf(FINFO,"chunk[%d] len=%d offset=%d sum1=%08x\n",
				i,s->sums[i].len,(int)s->sums[i].offset,s->sums[i].sum1);
	}

	s->flength = offset;

	return s;
}



void send_files(struct file_list *flist,int f_out,int f_in)
{ 
	int fd;
	struct sum_struct *s;
	struct map_struct *buf;
	STRUCT_STAT st;
	char fname[MAXPATHLEN];  
	int i;
	struct file_struct *file;
	int phase = 0;
	extern struct stats stats;		
	struct stats initial_stats;

	if (verbose > 2)
		rprintf(FINFO,"send_files starting\n");

	setup_readbuffer(f_in);

	while (1) {
		int offset=0;

		i = read_int(f_in);
		if (i == -1) {
			if (phase==0 && remote_version >= 13) {
				phase++;
				csum_length = SUM_LENGTH;
				write_int(f_out,-1);
				if (verbose > 2)
					rprintf(FINFO,"send_files phase=%d\n",phase);
				continue;
			}
			break;
		}

		if (i < 0 || i >= flist->count) {
			rprintf(FERROR,"Invalid file index %d (count=%d)\n", 
				i, flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}

		file = flist->files[i];

		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;

		fname[0] = 0;
		if (file->basedir) {
			strlcpy(fname,file->basedir,MAXPATHLEN);
			if (strlen(fname) == MAXPATHLEN-1) {
				io_error = 1;
				rprintf(FERROR, "send_files failed on long-named directory %s\n",
					fname);
				return;
			}
			strlcat(fname,"/",MAXPATHLEN);
			offset = strlen(file->basedir)+1;
		}
		strlcat(fname,f_name(file),MAXPATHLEN);
	  
		if (verbose > 2) 
			rprintf(FINFO,"send_files(%d,%s)\n",i,fname);
	  
		if (dry_run) {	
			if (!am_server) {
				log_transfer(file, fname+offset);
			}
			write_int(f_out,i);
			continue;
		}

		initial_stats = stats;

		s = receive_sums(f_in);
		if (!s) {
			io_error = 1;
			rprintf(FERROR,"receive_sums failed\n");
			return;
		}
	  
		fd = open(fname,O_RDONLY);
		if (fd == -1) {
			io_error = 1;
			rprintf(FERROR,"send_files failed to open %s: %s\n",
				fname,strerror(errno));
			free_sums(s);
			continue;
		}
	  
		/* map the local file */
		if (do_fstat(fd,&st) != 0) {
			io_error = 1;
			rprintf(FERROR,"fstat failed : %s\n",strerror(errno));
			free_sums(s);
			close(fd);
			return;
		}
	  
		if (st.st_size > 0) {
			buf = map_file(fd,st.st_size);
		} else {
			buf = NULL;
		}
	  
		if (verbose > 2)
			rprintf(FINFO,"send_files mapped %s of size %d\n",
				fname,(int)st.st_size);

		write_int(f_out,i);
	  
		write_int(f_out,s->count);
		write_int(f_out,s->n);
		write_int(f_out,s->remainder);
	  
		if (verbose > 2)
			rprintf(FINFO,"calling match_sums %s\n",fname);
	  
		if (!am_server) {
			log_transfer(file, fname+offset);
		}

		set_compression(fname);
	  
		match_sums(f_out,s,buf,st.st_size);

		log_send(file, &initial_stats);
	  	  
		if (buf) unmap_file(buf);
		close(fd);
	  
		free_sums(s);
	  
		if (verbose > 2)
			rprintf(FINFO,"sender finished %s\n",fname);
	}

	if (verbose > 2)
		rprintf(FINFO,"send files finished\n");

	match_report();

	write_int(f_out,-1);
}





