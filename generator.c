/* -*- c-file-style: "linux" -*-

   rsync -- fast file replication program
   
   Copyright (C) 1996-2000 by Andrew Tridgell 
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2002 by Martin Pool <mbp@samba.org>
   
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
extern int dry_run;
extern int relative_paths;
extern int preserve_links;
extern int am_root;
extern int preserve_devices;
extern int preserve_hard_links;
extern int update_only;
extern int opt_ignore_existing;
extern int block_size;
extern int csum_length;
extern int ignore_times;
extern int size_only;
extern int io_timeout;
extern int remote_version;
extern int always_checksum;
extern int modify_window;
extern char *compare_dest;
extern int link_dest;


/* choose whether to skip a particular file */
static int skip_file(char *fname,
		     struct file_struct *file, STRUCT_STAT *st)
{
	if (st->st_size != file->length) {
		return 0;
	}
	if (link_dest) {
		if((st->st_mode & ~_S_IFMT) !=  (file->mode & ~_S_IFMT)) {
			return 0;
		}
		if (st->st_uid != file->uid || st->st_gid != file->gid) {
			return 0;
		}
	}

	
	/* if always checksum is set then we use the checksum instead 
	   of the file time to determine whether to sync */
	if (always_checksum && S_ISREG(st->st_mode)) {
		char sum[MD4_SUM_LENGTH];
		char fnamecmpdest[MAXPATHLEN];

		if (compare_dest != NULL) {
			if (access(fname, 0) != 0) {
				snprintf(fnamecmpdest,MAXPATHLEN,"%s/%s",
						    compare_dest,fname);
				fname = fnamecmpdest;
			}
		}
		file_checksum(fname,sum,st->st_size);
		if (remote_version < 21) {
			return (memcmp(sum,file->sum,2) == 0);
		} else {
			return (memcmp(sum,file->sum,MD4_SUM_LENGTH) == 0);
		}
	}

	if (size_only) {
		return 1;
	}

	if (ignore_times) {
		return 0;
	}

	return (cmp_modtime(st->st_mtime,file->modtime) == 0);
}


/* use a larger block size for really big files */
static int adapt_block_size(struct file_struct *file, int bsize)
{
	int ret;

	if (bsize != BLOCK_SIZE) return bsize;

	ret = file->length / (10000); /* rough heuristic */
	ret = ret & ~15; /* multiple of 16 */
	if (ret < bsize) ret = bsize;
	if (ret > CHUNK_SIZE/2) ret = CHUNK_SIZE/2;
	return ret;
}


/*
  send a header that says "we have no checksums" down the f_out fd
  */
static void send_null_sums(int f_out)
{
	write_int(f_out, 0);
	write_int(f_out, block_size);
	write_int(f_out, 0);
}



/**
 * Perhaps we want to just send an empty checksum set for this file,
 * which will force the whole thing to be literally transferred.
 *
 * When do we do this?  If the user's explicitly said they
 * want the whole thing, or if { they haven't explicitly
 * requested a delta, and it's local but not batch mode.}
 *
 * Whew. */
static BOOL disable_deltas_p(void)
{
	extern int whole_file, no_whole_file;
	extern int local_server;
	extern int write_batch;

	assert(whole_file == 0 || whole_file == 1);

	/* whole_file and no_whole_file are never both on at the same time */

	if (whole_file)
		return True;
	else if (no_whole_file)
		return False;
	else if (write_batch)
		return False;
	else
		return local_server;
}


/*
 * Generate and send a stream of signatures/checksums that describe a buffer
 *
 * Generate approximately one checksum every block_len bytes.
 */
static void generate_and_send_sums(struct map_struct *buf, OFF_T len,
				   int block_len, int f_out)
{
	size_t i;
	struct sum_struct sum;
	OFF_T offset = 0;

	sum.count = (len + (block_len - 1)) / block_len;
	sum.remainder = (len % block_len);
	sum.n = block_len;
	sum.flength = len;
	/* not needed here  sum.sums = NULL; */

	if (sum.count && verbose > 3) {
		rprintf(FINFO, "count=%ld rem=%ld n=%ld flength=%.0f\n",
			(long) sum.count, (long) sum.remainder,
			(long) sum.n, (double) sum.flength);
	}

	write_int(f_out, sum.count);
	write_int(f_out, sum.n);
	write_int(f_out, sum.remainder);

	for (i = 0; i < sum.count; i++) {
		int n1 = MIN(len, block_len);
		char *map = map_ptr(buf, offset, n1);
		uint32 sum1 = get_checksum1(map, n1);
		char sum2[SUM_LENGTH];

		get_checksum2(map, n1, sum2);

		if (verbose > 3) {
			rprintf(FINFO,
				"chunk[%d] offset=%.0f len=%d sum1=%08lx\n",
				i, (double) offset, n1, (unsigned long) sum1);
		}
		write_int(f_out, sum1);
		write_buf(f_out, sum2, csum_length);
		len -= n1;
		offset += n1;
	}
}



/**
 * Acts on file number @p i from @p flist, whose name is @p fname.
 *
 * First fixes up permissions, then generates checksums for the file.
 *
 * @note This comment was added later by mbp who was trying to work it
 * out.  It might be wrong.
 **/ 
void recv_generator(char *fname, struct file_list *flist, int i, int f_out)
{  
	int fd;
	STRUCT_STAT st;
	struct map_struct *buf;
	int statret;
	struct file_struct *file = flist->files[i];
	char *fnamecmp;
	char fnamecmpbuf[MAXPATHLEN];
	extern char *compare_dest;
	extern int list_only;
	extern int preserve_perms;
	extern int only_existing;
	extern int orig_umask;

	if (list_only) return;

	if (verbose > 2)
		rprintf(FINFO,"recv_generator(%s,%d)\n",fname,i);

	statret = link_stat(fname,&st);

	if (only_existing && statret == -1 && errno == ENOENT) {
		/* we only want to update existing files */
		if (verbose > 1) rprintf(FINFO, "not creating new file \"%s\"\n",fname);
		return;
	}

	if (statret == 0 && 
	    !preserve_perms && 
	    (S_ISDIR(st.st_mode) == S_ISDIR(file->mode))) {
		/* if the file exists already and we aren't perserving
                   presmissions then act as though the remote end sent
                   us the file permissions we already have */
		file->mode = (file->mode & _S_IFMT) | (st.st_mode & ~_S_IFMT);
	}

	if (S_ISDIR(file->mode)) {
                /* The file to be received is a directory, so we need
                 * to prepare appropriately.  If there is already a
                 * file of that name and it is *not* a directory, then
                 * we need to delete it.  If it doesn't exist, then
                 * recursively create it. */
          
		if (dry_run) return; /* XXXX -- might cause inaccuracies?? -- mbp */
		if (statret == 0 && !S_ISDIR(st.st_mode)) {
			if (robust_unlink(fname) != 0) {
				rprintf(FERROR, RSYNC_NAME
					": recv_generator: unlink \"%s\" to make room for directory: %s\n",
                                        fname,strerror(errno));
				return;
			}
			statret = -1;
		}
		if (statret != 0 && do_mkdir(fname,file->mode) != 0 && errno != EEXIST) {
			if (!(relative_paths && errno==ENOENT && 
			      create_directory_path(fname, orig_umask)==0 && 
			      do_mkdir(fname,file->mode)==0)) {
				rprintf(FERROR, RSYNC_NAME ": recv_generator: mkdir \"%s\": %s (2)\n",
					fname,strerror(errno));
			}
		}
		/* f_out is set to -1 when doing final directory 
		   permission and modification time repair */
		if (set_perms(fname,file,NULL,0) && verbose && (f_out != -1)) 
			rprintf(FINFO,"%s/\n",fname);
		return;
	}

	if (preserve_links && S_ISLNK(file->mode)) {
#if SUPPORT_LINKS
		char lnk[MAXPATHLEN];
		int l;
		extern int safe_symlinks;

		if (safe_symlinks && unsafe_symlink(file->link, fname)) {
			if (verbose) {
				rprintf(FINFO,"ignoring unsafe symlink \"%s\" -> \"%s\"\n",
					fname,file->link);
			}
			return;
		}
		if (statret == 0) {
			l = readlink(fname,lnk,MAXPATHLEN-1);
			if (l > 0) {
				lnk[l] = 0;
				/* A link already pointing to the
				 * right place -- no further action
				 * required. */
				if (strcmp(lnk,file->link) == 0) {
					set_perms(fname,file,&st,1);
					return;
				}
			}  
			/* Not a symlink, so delete whatever's
			 * already there and put a new symlink
			 * in place. */			   
			delete_file(fname);
		}
		if (do_symlink(file->link,fname) != 0) {
			rprintf(FERROR,RSYNC_NAME": symlink \"%s\" -> \"%s\": %s\n",
				fname,file->link,strerror(errno));
		} else {
			set_perms(fname,file,NULL,0);
			if (verbose) {
				rprintf(FINFO,"%s -> %s\n", fname,file->link);
			}
		}
#endif
		return;
	}

#ifdef HAVE_MKNOD
	if (am_root && preserve_devices && IS_DEVICE(file->mode)) {
		if (statret != 0 || 
		    st.st_mode != file->mode ||
		    st.st_rdev != file->rdev) {	
			delete_file(fname);
			if (verbose > 2)
				rprintf(FINFO,"mknod(%s,0%o,0x%x)\n",
					fname,(int)file->mode,(int)file->rdev);
			if (do_mknod(fname,file->mode,file->rdev) != 0) {
				rprintf(FERROR,"mknod %s : %s\n",fname,strerror(errno));
			} else {
				set_perms(fname,file,NULL,0);
				if (verbose)
					rprintf(FINFO,"%s\n",fname);
			}
		} else {
			set_perms(fname,file,&st,1);
		}
		return;
	}
#endif

	if (preserve_hard_links && check_hard_link(file)) {
		if (verbose > 1)
			rprintf(FINFO, "recv_generator: \"%s\" is a hard link\n",f_name(file));
		return;
	}

	if (!S_ISREG(file->mode)) {
		rprintf(FINFO, "skipping non-regular file \"%s\"\n",fname);
		return;
	}

	fnamecmp = fname;

	if ((statret == -1) && (compare_dest != NULL)) {
		/* try the file at compare_dest instead */
		int saveerrno = errno;
		snprintf(fnamecmpbuf,MAXPATHLEN,"%s/%s",compare_dest,fname);
		statret = link_stat(fnamecmpbuf,&st);
		if (!S_ISREG(st.st_mode))
			statret = -1;
		if (statret == -1)
			errno = saveerrno;
#if HAVE_LINK
		else if (link_dest && !dry_run) {
			if (do_link(fnamecmpbuf, fname) != 0) {
				if (verbose > 0)
					rprintf(FINFO,"link %s => %s : %s\n",
						fnamecmpbuf,
						fname,
						strerror(errno));
			}
			fnamecmp = fnamecmpbuf;
		}
#endif
		else
			fnamecmp = fnamecmpbuf;
	}

	if (statret == -1) {
		if (errno == ENOENT) {
			write_int(f_out,i);
			if (!dry_run) send_null_sums(f_out);
		} else {
			if (verbose > 1)
				rprintf(FERROR, RSYNC_NAME
					": recv_generator failed to open \"%s\": %s\n",
					fname, strerror(errno));
		}
		return;
	}

	if (!S_ISREG(st.st_mode)) {
		if (delete_file(fname) != 0) {
			return;
		}

		/* now pretend the file didn't exist */
		write_int(f_out,i);
		if (!dry_run) send_null_sums(f_out);
		return;
	}

	if (opt_ignore_existing && fnamecmp == fname) { 
		if (verbose > 1)
			rprintf(FINFO,"%s exists\n",fname);
		return;
	} 

	if (update_only && cmp_modtime(st.st_mtime,file->modtime)>0 && fnamecmp == fname) {
		if (verbose > 1)
			rprintf(FINFO,"%s is newer\n",fname);
		return;
	}

	if (skip_file(fname, file, &st)) {
		if (fnamecmp == fname)
			set_perms(fname,file,&st,1);
		return;
	}

	if (dry_run) {
		write_int(f_out,i);
		return;
	}

	if (disable_deltas_p()) {
		write_int(f_out,i);
		send_null_sums(f_out);
		return;
	}

	/* open the file */  
	fd = do_open(fnamecmp, O_RDONLY, 0);

	if (fd == -1) {
		rprintf(FERROR,RSYNC_NAME": failed to open \"%s\", continuing : %s\n",fnamecmp,strerror(errno));
		/* pretend the file didn't exist */
		write_int(f_out,i);
		send_null_sums(f_out);
		return;
	}

	if (st.st_size > 0) {
		buf = map_file(fd,st.st_size);
	} else {
		buf = NULL;
	}

	if (verbose > 3)
		rprintf(FINFO,"gen mapped %s of size %.0f\n",fnamecmp,(double)st.st_size);

	if (verbose > 2)
		rprintf(FINFO, "generating and sending sums for %d\n", i);

	write_int(f_out,i);
	generate_and_send_sums(buf, st.st_size,
			       adapt_block_size(file, block_size), f_out);

	close(fd);
	if (buf) unmap_file(buf);
}



void generate_files(int f,struct file_list *flist,char *local_name,int f_recv)
{
	int i;
	int phase=0;

	if (verbose > 2)
		rprintf(FINFO,"generator starting pid=%d count=%d\n",
			(int)getpid(),flist->count);

	if (verbose >= 2) {
		rprintf(FINFO,
			disable_deltas_p() 
			? "delta-transmission disabled for local transfer or --whole-file\n"
			: "delta transmission enabled\n");
	}
	
	/* we expect to just sit around now, so don't exit on a
	   timeout. If we really get a timeout then the other process should
	   exit */
	io_timeout = 0;

	for (i = 0; i < flist->count; i++) {
		struct file_struct *file = flist->files[i];
		mode_t saved_mode = file->mode;
		if (!file->basename) continue;

		/* we need to ensure that any directories we create have writeable
		   permissions initially so that we can create the files within
		   them. This is then fixed after the files are transferred */
		if (!am_root && S_ISDIR(file->mode)) {
			file->mode |= S_IWUSR; /* user write */
                        /* XXX: Could this be causing a problem on SCO?  Perhaps their
                         * handling of permissions is strange? */
		}

		recv_generator(local_name?local_name:f_name(file),
			       flist,i,f);

		file->mode = saved_mode;
	}

	phase++;
	csum_length = SUM_LENGTH;
	ignore_times=1;

	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f,-1);

	if (remote_version >= 13) {
		/* in newer versions of the protocol the files can cycle through
		   the system more than once to catch initial checksum errors */
		for (i=read_int(f_recv); i != -1; i=read_int(f_recv)) {
			struct file_struct *file = flist->files[i];
			recv_generator(local_name?local_name:f_name(file),
				       flist,i,f);    
		}

		phase++;
		if (verbose > 2)
			rprintf(FINFO,"generate_files phase=%d\n",phase);

		write_int(f,-1);
	}
}
