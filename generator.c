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
extern int preserve_perms;
extern int preserve_uid;
extern int preserve_gid;
extern int update_only;
extern int opt_ignore_existing;
extern int csum_length;
extern int ignore_times;
extern int size_only;
extern int io_timeout;
extern int protocol_version;
extern int always_checksum;
extern char *compare_dest;
extern int link_dest;
extern int whole_file;
extern int local_server;
extern int write_batch;
extern int list_only;
extern int only_existing;
extern int orig_umask;
extern int safe_symlinks;


/* choose whether to skip a particular file */
static int skip_file(char *fname, struct file_struct *file, STRUCT_STAT *st)
{
	if (st->st_size != file->length) {
		return 0;
	}
	if (link_dest) {
		if (preserve_perms
		    && (st->st_mode & CHMOD_BITS) != (file->mode & CHMOD_BITS))
			return 0;

		if (am_root && preserve_uid && st->st_uid != file->uid)
			return 0;

		if (preserve_gid && file->gid != GID_NONE
		    && st->st_gid != file->gid)
			return 0;
	}

	/* if always checksum is set then we use the checksum instead
	   of the file time to determine whether to sync */
	if (always_checksum && S_ISREG(st->st_mode)) {
		char sum[MD4_SUM_LENGTH];
		char fnamecmpdest[MAXPATHLEN];

		if (compare_dest != NULL) {
			if (access(fname, 0) != 0) {
				pathjoin(fnamecmpdest, sizeof fnamecmpdest,
					 compare_dest, fname);
				fname = fnamecmpdest;
			}
		}
		file_checksum(fname,sum,st->st_size);
		return memcmp(sum, file->u.sum, protocol_version < 21 ? 2
							: MD4_SUM_LENGTH) == 0;
	}

	if (size_only) {
		return 1;
	}

	if (ignore_times) {
		return 0;
	}

	return (cmp_modtime(st->st_mtime,file->modtime) == 0);
}


/*
 * NULL sum_struct means we have no checksums
 */
void write_sum_head(int f, struct sum_struct *sum)
{
	static struct sum_struct null_sum;

	if (sum == NULL)
		sum = &null_sum;

	write_int(f, sum->count);
	write_int(f, sum->blength);
	if (protocol_version >= 27)
		write_int(f, sum->s2length);
	write_int(f, sum->remainder);
}

/* 
 * set (initialize) the size entries in the per-file sum_struct
 * calulating dynamic block ans checksum sizes.
 *
 * This is only called from generate_and_send_sums() but is a seperate
 * function to encapsulate the logic.
 *
 * The block size is a rounded square root of file length.
 *
 * The checksum size is determined according to:
 *     blocksum_bits = BLOCKSUM_EXP + 2*log2(file_len) - log2(block_len)
 * provided by Donovan Baarda which gives a probability of rsync
 * algorithm corrupting data and falling back using the whole md4
 * checksums.
 *
 * This might be made one of several selectable heuristics.
 */

static void sum_sizes_sqroot(struct sum_struct *sum, uint64 len)
{
	extern unsigned int block_size;
	unsigned int blength;
	int s2length;
	uint32 c;
	uint64 l;

	if (block_size) {
		blength = block_size;
	} else if (len <= BLOCK_SIZE * BLOCK_SIZE) {
		blength = BLOCK_SIZE;
	} else {
		l = len;
		c = 1;
		while (l >>= 2) {
			c <<= 1;
		}
		blength = 0;
		do {
			blength |= c;
			if (len < (uint64)blength * blength)
				blength &= ~c;
			c >>= 1;
		} while (c >= 8);	/* round to multiple of 8 */
		blength = MAX(blength, BLOCK_SIZE);
	}

	if (protocol_version < 27) {
		s2length = csum_length;
	} else if (csum_length == SUM_LENGTH) {
		s2length = SUM_LENGTH;
	} else {
		int b = BLOCKSUM_BIAS;
		l = len;
		while (l >>= 1) {
			b += 2;
		}
		c = blength;
		while (c >>= 1 && b) {
			b--;
		}
		s2length = (b + 1 - 32 + 7) / 8; /* add a bit,
						  * subtract rollsum,
						  * round up
						  *    --optimize in compiler--
						  */
		s2length = MAX(s2length, csum_length);
		s2length = MIN(s2length, SUM_LENGTH);
	}

	sum->flength	= len;
	sum->blength	= blength;
	sum->s2length	= s2length;
	sum->count	= (len + (blength - 1)) / blength;
	sum->remainder	= (len % blength);

	if (sum->count && verbose > 2) {
		rprintf(FINFO, "count=%.0f rem=%u blength=%u s2length=%d flength=%.0f\n",
			(double)sum->count, sum->remainder, sum->blength,
			sum->s2length, (double)sum->flength);
	}
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
	if (whole_file > 0)
		return True;
	if (whole_file == 0 || write_batch)
		return False;
	return local_server;
}


/*
 * Generate and send a stream of signatures/checksums that describe a buffer
 *
 * Generate approximately one checksum every block_len bytes.
 */
static void generate_and_send_sums(struct map_struct *buf, size_t len, int f_out)
{
	size_t i;
	struct sum_struct sum;
	OFF_T offset = 0;

	sum_sizes_sqroot(&sum, len);

	write_sum_head(f_out, &sum);

	for (i = 0; i < sum.count; i++) {
		unsigned int n1 = MIN(len, sum.blength);
		char *map = map_ptr(buf, offset, n1);
		uint32 sum1 = get_checksum1(map, n1);
		char sum2[SUM_LENGTH];

		get_checksum2(map, n1, sum2);

		if (verbose > 3) {
			rprintf(FINFO,
				"chunk[%.0f] offset=%.0f len=%u sum1=%08lx\n",
				(double)i, (double)offset, n1,
				(unsigned long)sum1);
		}
		write_int(f_out, sum1);
		write_buf(f_out, sum2, sum.s2length);
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
void recv_generator(char *fname, struct file_struct *file, int i, int f_out)
{
	int fd;
	STRUCT_STAT st;
	struct map_struct *mapbuf;
	int statret;
	char *fnamecmp;
	char fnamecmpbuf[MAXPATHLEN];

	if (list_only)
		return;

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
		 * permissions then act as though the remote end sent
		 * us the file permissions we already have */
		file->mode = (file->mode & ~CHMOD_BITS)
			   | (st.st_mode & CHMOD_BITS);
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
				rprintf(FERROR,
					"recv_generator: unlink %s to make room for directory: %s\n",
					full_fname(fname), strerror(errno));
				return;
			}
			statret = -1;
		}
		if (statret != 0 && do_mkdir(fname,file->mode) != 0 && errno != EEXIST) {
			if (!(relative_paths && errno==ENOENT &&
			      create_directory_path(fname, orig_umask)==0 &&
			      do_mkdir(fname,file->mode)==0)) {
				rprintf(FERROR, "recv_generator: mkdir %s failed: %s\n",
					full_fname(fname), strerror(errno));
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

		if (safe_symlinks && unsafe_symlink(file->u.link, fname)) {
			if (verbose) {
				rprintf(FINFO, "ignoring unsafe symlink %s -> \"%s\"\n",
					full_fname(fname), file->u.link);
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
				if (strcmp(lnk,file->u.link) == 0) {
					set_perms(fname,file,&st,1);
					return;
				}
			}
			/* Not a symlink, so delete whatever's
			 * already there and put a new symlink
			 * in place. */
			delete_file(fname);
		}
		if (do_symlink(file->u.link,fname) != 0) {
			rprintf(FERROR, "symlink %s -> \"%s\" failed: %s\n",
				full_fname(fname), file->u.link, strerror(errno));
		} else {
			set_perms(fname,file,NULL,0);
			if (verbose) {
				rprintf(FINFO,"%s -> %s\n", fname,file->u.link);
			}
		}
#endif
		return;
	}

#ifdef HAVE_MKNOD
	if (am_root && preserve_devices && IS_DEVICE(file->mode)) {
		if (statret != 0 ||
		    st.st_mode != file->mode ||
		    st.st_rdev != file->u.rdev) {
			delete_file(fname);
			if (verbose > 2)
				rprintf(FINFO,"mknod(%s,0%o,0x%x)\n",
					fname,(int)file->mode,(int)file->u.rdev);
			if (do_mknod(fname,file->mode,file->u.rdev) != 0) {
				rprintf(FERROR, "mknod %s failed: %s\n",
					full_fname(fname), strerror(errno));
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

	if (preserve_hard_links && hard_link_check(file, HL_CHECK_MASTER))
		return;

	if (!S_ISREG(file->mode)) {
		rprintf(FINFO, "skipping non-regular file \"%s\"\n",fname);
		return;
	}

	fnamecmp = fname;

	if (statret == -1 && compare_dest != NULL) {
		/* try the file at compare_dest instead */
		int saveerrno = errno;
		pathjoin(fnamecmpbuf, sizeof fnamecmpbuf, compare_dest, fname);
		statret = link_stat(fnamecmpbuf,&st);
		if (!S_ISREG(st.st_mode))
			statret = -1;
		if (statret == -1)
			errno = saveerrno;
#if HAVE_LINK
		else if (link_dest && !dry_run) {
			if (do_link(fnamecmpbuf, fname) != 0) {
				if (verbose > 0) {
					rprintf(FINFO,"link %s => %s : %s\n",
						fnamecmpbuf, fname,
						strerror(errno));
				}
			}
			fnamecmp = fnamecmpbuf;
		}
#endif
		else
			fnamecmp = fnamecmpbuf;
	}

	if (statret == -1) {
		if (preserve_hard_links && hard_link_check(file, HL_SKIP))
			return;
		if (errno == ENOENT) {
			write_int(f_out,i);
			if (!dry_run) write_sum_head(f_out, NULL);
		} else if (verbose > 1) {
			rprintf(FERROR,
				"recv_generator: failed to open %s: %s\n",
				full_fname(fname), strerror(errno));
		}
		return;
	}

	if (!S_ISREG(st.st_mode)) {
		if (delete_file(fname) != 0) {
			return;
		}

		/* now pretend the file didn't exist */
		if (preserve_hard_links && hard_link_check(file, HL_SKIP))
			return;
		write_int(f_out,i);
		if (!dry_run) write_sum_head(f_out, NULL);
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
		write_sum_head(f_out, NULL);
		return;
	}

	/* open the file */
	fd = do_open(fnamecmp, O_RDONLY, 0);

	if (fd == -1) {
		rprintf(FERROR, "failed to open %s, continuing: %s\n",
			full_fname(fnamecmp), strerror(errno));
		/* pretend the file didn't exist */
		if (preserve_hard_links && hard_link_check(file, HL_SKIP))
			return;
		write_int(f_out,i);
		write_sum_head(f_out, NULL);
		return;
	}

	if (st.st_size > 0)
		mapbuf = map_file(fd,st.st_size);
	else
		mapbuf = NULL;

	if (verbose > 3) {
		rprintf(FINFO,"gen mapped %s of size %.0f\n", fnamecmp,
			(double)st.st_size);
	}

	if (verbose > 2)
		rprintf(FINFO, "generating and sending sums for %d\n", i);

	write_int(f_out,i);
	generate_and_send_sums(mapbuf, st.st_size, f_out);

	close(fd);
	if (mapbuf) unmap_file(mapbuf);
}


void generate_files(int f, struct file_list *flist, char *local_name)
{
	int i;
	int phase=0;
	char fbuf[MAXPATHLEN];

	if (verbose > 2) {
		rprintf(FINFO, "generator starting pid=%ld count=%d\n",
			(long)getpid(), flist->count);
	}

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
		struct file_struct copy;

		if (!file->basename)
			continue;
		/* we need to ensure that any directories we create have writeable
		   permissions initially so that we can create the files within
		   them. This is then fixed after the files are transferred */
		if (!am_root && S_ISDIR(file->mode) && !(file->mode & S_IWUSR)) {
			copy = *file;
			/* XXX: Could this be causing a problem on SCO?  Perhaps their
			 * handling of permissions is strange? */
			copy.mode |= S_IWUSR; /* user write */
			file = &copy;
		}

		recv_generator(local_name ? local_name : f_name_to(file, fbuf),
			       file, i, f);
	}

	phase++;
	csum_length = SUM_LENGTH;
	ignore_times=1;

	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f,-1);

	/* files can cycle through the system more than once
	 * to catch initial checksum errors */
	while ((i = get_redo_num()) != -1) {
		struct file_struct *file = flist->files[i];
		recv_generator(local_name ? local_name : f_name_to(file, fbuf),
			       file, i, f);
	}

	phase++;
	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f,-1);

	if (preserve_hard_links)
		do_hard_links();

	/* now we need to fix any directory permissions that were
	 * modified during the transfer */
	for (i = 0; i < flist->count; i++) {
		struct file_struct *file = flist->files[i];
		if (!file->basename || !S_ISDIR(file->mode)) continue;
		recv_generator(local_name ? local_name : f_name(file),
			       file, i, -1);
	}

	if (verbose > 2)
		rprintf(FINFO,"generate_files finished\n");
}
