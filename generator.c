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
extern int do_xfers;
extern int log_format_has_i;
extern int log_format_has_o_or_i;
extern int daemon_log_format_has_i;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int do_progress;
extern int recurse;
extern int relative_paths;
extern int keep_dirlinks;
extern int preserve_links;
extern int preserve_devices;
extern int preserve_hard_links;
extern int preserve_perms;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_times;
extern int omit_dir_times;
extern int delete_before;
extern int delete_during;
extern int delete_after;
extern int module_id;
extern int ignore_errors;
extern int remove_sent_files;
extern int delay_updates;
extern int update_only;
extern int opt_ignore_existing;
extern int inplace;
extern int make_backups;
extern int csum_length;
extern int ignore_times;
extern int size_only;
extern OFF_T max_size;
extern int io_error;
extern int allowed_lull;
extern int sock_f_out;
extern int ignore_timeout;
extern int protocol_version;
extern int fuzzy_basis;
extern int always_checksum;
extern char *partial_dir;
extern char *basis_dir[];
extern int compare_dest;
extern int copy_dest;
extern int link_dest;
extern int whole_file;
extern int list_only;
extern int read_batch;
extern int only_existing;
extern int orig_umask;
extern int safe_symlinks;
extern long block_size; /* "long" because popt can't set an int32. */
extern int max_delete;
extern int force_delete;
extern int one_file_system;
extern struct stats stats;
extern dev_t filesystem_dev;
extern char *backup_dir;
extern char *backup_suffix;
extern int backup_suffix_len;
extern struct file_list *the_file_list;
extern struct filter_list_struct server_filter_list;

static int deletion_count = 0; /* used to implement --max-delete */


static int is_backup_file(char *fn)
{
	int k = strlen(fn) - backup_suffix_len;
	return k > 0 && strcmp(fn+k, backup_suffix) == 0;
}


/* Delete a file or directory.  If DEL_FORCE_RECURSE is set in the flags, or if
 * force_delete is set, this will delete recursively as long as DEL_NO_RECURSE
 * is not set in the flags. */
static int delete_item(char *fname, int mode, int flags)
{
	struct file_list *dirlist;
	char buf[MAXPATHLEN];
	int j, dlen, zap_dir, ok;
	void *save_filters;

	if (!S_ISDIR(mode)) {
		if (max_delete && ++deletion_count > max_delete)
			return 0;
		if (make_backups && (backup_dir || !is_backup_file(fname)))
			ok = make_backup(fname);
		else
			ok = robust_unlink(fname) == 0;
		if (ok) {
			if (!(flags & DEL_TERSE))
				log_delete(fname, mode);
			return 0;
		}
		if (errno == ENOENT) {
			deletion_count--;
			return 0;
		}
		rsyserr(FERROR, errno, "delete_file: unlink %s failed",
			full_fname(fname));
		return -1;
	}

	zap_dir = (flags & DEL_FORCE_RECURSE || (force_delete && recurse))
		&& !(flags & DEL_NO_RECURSE);
	if ((max_delete && ++deletion_count > max_delete)
	    || (dry_run && zap_dir)) {
		ok = 0;
		errno = ENOTEMPTY;
	} else if (make_backups && !backup_dir && !is_backup_file(fname)
	    && !(flags & DEL_FORCE_RECURSE))
		ok = make_backup(fname);
	else
		ok = do_rmdir(fname) == 0;
	if (ok) {
		if (!(flags & DEL_TERSE))
			log_delete(fname, mode);
		return 0;
	}
	if (errno == ENOENT) {
		deletion_count--;
		return 0;
	}
	if (!zap_dir) {
		rsyserr(FERROR, errno, "delete_file: rmdir %s failed",
			full_fname(fname));
		return -1;
	}
	flags |= DEL_FORCE_RECURSE; /* mark subdir dels as not "in the way" */
	deletion_count--;

	dlen = strlcpy(buf, fname, MAXPATHLEN);
	save_filters = push_local_filters(buf, dlen);

	dirlist = get_dirlist(buf, dlen, 0);
	for (j = dirlist->count; j--; ) {
		struct file_struct *fp = dirlist->files[j];

		if (fp->flags & FLAG_MOUNT_POINT)
			continue;

		f_name_to(fp, buf);
		delete_item(buf, fp->mode, flags & ~DEL_TERSE);
	}
	flist_free(dirlist);

	pop_local_filters(save_filters);

	if (max_delete && ++deletion_count > max_delete)
		return 0;

	if (do_rmdir(fname) == 0) {
		if (!(flags & DEL_TERSE))
			log_delete(fname, mode);
	} else if (errno != ENOTEMPTY && errno != EEXIST && errno != ENOENT) {
		rsyserr(FERROR, errno, "delete_file: rmdir %s failed",
			full_fname(fname));
		return -1;
	}

	return 0;
}


/* This function is used to implement per-directory deletion, and is used by
 * all the --delete-WHEN options.  Note that the fbuf pointer must point to a
 * MAXPATHLEN buffer with the name of the directory in it (the functions we
 * call will append names onto the end, but the old dir value will be restored
 * on exit). */
static void delete_in_dir(struct file_list *flist, char *fbuf,
			  struct file_struct *file)
{
	static int min_depth = MAXPATHLEN, cur_depth = -1;
	static void *filt_array[MAXPATHLEN/2+1];
	static int already_warned = 0;
	struct file_list *dirlist;
	char delbuf[MAXPATHLEN];
	STRUCT_STAT st;
	int dlen, i;

	if (!flist) {
		while (cur_depth >= min_depth)
			pop_local_filters(filt_array[cur_depth--]);
		min_depth = MAXPATHLEN;
		cur_depth = -1;
		return;
	}

	if (verbose > 2)
		rprintf(FINFO, "delete_in_dir(%s)\n", safe_fname(fbuf));

	if (allowed_lull)
		maybe_send_keepalive();

	if (file->dir.depth >= MAXPATHLEN/2+1)
		return; /* Impossible... */

	if (io_error && !(lp_ignore_errors(module_id) || ignore_errors)) {
		if (already_warned)
			return;
		rprintf(FINFO,
			"IO error encountered -- skipping file deletion\n");
		already_warned = 1;
		return;
	}

	while (cur_depth >= file->dir.depth && cur_depth >= min_depth)
		pop_local_filters(filt_array[cur_depth--]);
	cur_depth = file->dir.depth;
	if (min_depth > cur_depth)
		min_depth = cur_depth;
	dlen = strlen(fbuf);
	filt_array[cur_depth] = push_local_filters(fbuf, dlen);

	if (link_stat(fbuf, &st, keep_dirlinks) < 0)
		return;

	if (one_file_system) {
		if (file->flags & FLAG_TOP_DIR)
			filesystem_dev = st.st_dev;
		else if (filesystem_dev != st.st_dev)
			return;
	}

	dirlist = get_dirlist(fbuf, dlen, 0);

	/* If an item in dirlist is not found in flist, delete it
	 * from the filesystem. */
	for (i = dirlist->count; i--; ) {
		struct file_struct *fp = dirlist->files[i];
		if (!fp->basename || fp->flags & FLAG_MOUNT_POINT)
			continue;
		if (flist_find(flist, fp) < 0) {
			int mode = fp->mode;
			f_name_to(fp, delbuf);
			delete_item(delbuf, mode, DEL_FORCE_RECURSE);
		}
	}

	flist_free(dirlist);
}

/* This deletes any files on the receiving side that are not present on the
 * sending side.  This is used by --delete-before and --delete-after. */
static void do_delete_pass(struct file_list *flist)
{
	char fbuf[MAXPATHLEN];
	int j;

	if (dry_run > 1 /* destination doesn't exist yet */
	 || list_only)
		return;

	for (j = 0; j < flist->count; j++) {
		struct file_struct *file = flist->files[j];

		if (!(file->flags & FLAG_DEL_HERE))
			continue;

		f_name_to(file, fbuf);
		if (verbose > 1 && file->flags & FLAG_TOP_DIR)
			rprintf(FINFO, "deleting in %s\n", safe_fname(fbuf));

		delete_in_dir(flist, fbuf, file);
	}
	if (do_progress && !am_server)
		rprintf(FINFO, "                    \r");
}

static int unchanged_attrs(struct file_struct *file, STRUCT_STAT *st)
{
	if (preserve_perms
	 && (st->st_mode & CHMOD_BITS) != (file->mode & CHMOD_BITS))
		return 0;

	if (am_root && preserve_uid && st->st_uid != file->uid)
		return 0;

	if (preserve_gid && file->gid != GID_NONE && st->st_gid != file->gid)
		return 0;

	return 1;
}


void itemize(struct file_struct *file, int ndx, int statret, STRUCT_STAT *st,
	     int32 iflags, uchar fnamecmp_type, char *xname)
{
	if (statret == 0) {
		if (S_ISREG(file->mode) && file->length != st->st_size)
			iflags |= ITEM_REPORT_SIZE;
		if (!(iflags & ITEM_NO_DEST_AND_NO_UPDATE)) {
			int keep_time = !preserve_times ? 0
			    : S_ISDIR(file->mode) ? !omit_dir_times
			    : !S_ISLNK(file->mode);

			if ((iflags & (ITEM_TRANSFER|ITEM_LOCAL_CHANGE) && !keep_time
			     && (!(iflags & ITEM_XNAME_FOLLOWS) || *xname))
			    || (keep_time && cmp_modtime(file->modtime, st->st_mtime) != 0))
				iflags |= ITEM_REPORT_TIME;
			if (preserve_perms
			 && (file->mode & CHMOD_BITS) != (st->st_mode & CHMOD_BITS))
				iflags |= ITEM_REPORT_PERMS;
			if (preserve_uid && am_root && file->uid != st->st_uid)
				iflags |= ITEM_REPORT_OWNER;
			if (preserve_gid && file->gid != GID_NONE
			    && st->st_gid != file->gid)
				iflags |= ITEM_REPORT_GROUP;
		}
	} else
		iflags |= ITEM_IS_NEW;

	iflags &= 0xffff;
	if ((iflags & SIGNIFICANT_ITEM_FLAGS || verbose > 1
	  || (xname && *xname)) && !read_batch) {
		if (protocol_version >= 29) {
			if (ndx >= 0)
				write_int(sock_f_out, ndx);
			write_shortint(sock_f_out, iflags);
			if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
				write_byte(sock_f_out, fnamecmp_type);
			if (iflags & ITEM_XNAME_FOLLOWS)
				write_vstring(sock_f_out, xname, strlen(xname));
		} else if (ndx >= 0)
			log_item(file, &stats, iflags, xname);
	}
}


/* Perform our quick-check heuristic for determining if a file is unchanged. */
static int unchanged_file(char *fn, struct file_struct *file, STRUCT_STAT *st)
{
	if (st->st_size != file->length)
		return 0;

	/* if always checksum is set then we use the checksum instead
	   of the file time to determine whether to sync */
	if (always_checksum && S_ISREG(st->st_mode)) {
		char sum[MD4_SUM_LENGTH];
		file_checksum(fn, sum, st->st_size);
		return memcmp(sum, file->u.sum, protocol_version < 21 ? 2
							: MD4_SUM_LENGTH) == 0;
	}

	if (size_only)
		return 1;

	if (ignore_times)
		return 0;

	return cmp_modtime(st->st_mtime, file->modtime) == 0;
}


/*
 * set (initialize) the size entries in the per-file sum_struct
 * calculating dynamic block and checksum sizes.
 *
 * This is only called from generate_and_send_sums() but is a separate
 * function to encapsulate the logic.
 *
 * The block size is a rounded square root of file length.
 *
 * The checksum size is determined according to:
 *     blocksum_bits = BLOCKSUM_BIAS + 2*log2(file_len) - log2(block_len)
 * provided by Donovan Baarda which gives a probability of rsync
 * algorithm corrupting data and falling back using the whole md4
 * checksums.
 *
 * This might be made one of several selectable heuristics.
 */
static void sum_sizes_sqroot(struct sum_struct *sum, int64 len)
{
	int32 blength;
	int s2length;

	if (block_size)
		blength = block_size;
	else if (len <= BLOCK_SIZE * BLOCK_SIZE)
		blength = BLOCK_SIZE;
	else {
		int32 c;
		int64 l;
		int cnt;
		for (c = 1, l = len, cnt = 0; l >>= 2; c <<= 1, cnt++) {}
		if (cnt >= 31 || c >= MAX_BLOCK_SIZE)
			blength = MAX_BLOCK_SIZE;
		else {
		    blength = 0;
		    do {
			    blength |= c;
			    if (len < (int64)blength * blength)
				    blength &= ~c;
			    c >>= 1;
		    } while (c >= 8);	/* round to multiple of 8 */
		    blength = MAX(blength, BLOCK_SIZE);
		}
	}

	if (protocol_version < 27) {
		s2length = csum_length;
	} else if (csum_length == SUM_LENGTH) {
		s2length = SUM_LENGTH;
	} else {
		int32 c;
		int64 l;
		int b = BLOCKSUM_BIAS;
		for (l = len; l >>= 1; b += 2) {}
		for (c = blength; c >>= 1 && b; b--) {}
		/* add a bit, subtract rollsum, round up. */
		s2length = (b + 1 - 32 + 7) / 8; /* --optimize in compiler-- */
		s2length = MAX(s2length, csum_length);
		s2length = MIN(s2length, SUM_LENGTH);
	}

	sum->flength	= len;
	sum->blength	= blength;
	sum->s2length	= s2length;
	sum->count	= (len + (blength - 1)) / blength;
	sum->remainder	= (len % blength);

	if (sum->count && verbose > 2) {
		rprintf(FINFO,
			"count=%.0f rem=%ld blength=%ld s2length=%d flength=%.0f\n",
			(double)sum->count, (long)sum->remainder, (long)sum->blength,
			sum->s2length, (double)sum->flength);
	}
}


/*
 * Generate and send a stream of signatures/checksums that describe a buffer
 *
 * Generate approximately one checksum every block_len bytes.
 */
static void generate_and_send_sums(int fd, OFF_T len, int f_out, int f_copy)
{
	int32 i;
	struct map_struct *mapbuf;
	struct sum_struct sum;
	OFF_T offset = 0;

	sum_sizes_sqroot(&sum, len);

	if (len > 0)
		mapbuf = map_file(fd, len, MAX_MAP_SIZE, sum.blength);
	else
		mapbuf = NULL;

	write_sum_head(f_out, &sum);

	for (i = 0; i < sum.count; i++) {
		int32 n1 = (int32)MIN(len, (OFF_T)sum.blength);
		char *map = map_ptr(mapbuf, offset, n1);
		uint32 sum1 = get_checksum1(map, n1);
		char sum2[SUM_LENGTH];

		if (f_copy >= 0)
			full_write(f_copy, map, n1);

		get_checksum2(map, n1, sum2);

		if (verbose > 3) {
			rprintf(FINFO,
				"chunk[%.0f] offset=%.0f len=%ld sum1=%08lx\n",
				(double)i, (double)offset, (long)n1,
				(unsigned long)sum1);
		}
		write_int(f_out, sum1);
		write_buf(f_out, sum2, sum.s2length);
		len -= n1;
		offset += n1;
	}

	if (mapbuf)
		unmap_file(mapbuf);
}


/* Try to find a filename in the same dir as "fname" with a similar name. */
static int find_fuzzy(struct file_struct *file, struct file_list *dirlist)
{
	int fname_len, fname_suf_len;
	const char *fname_suf, *fname = file->basename;
	uint32 lowest_dist = 25 << 16; /* ignore a distance greater than 25 */
	int j, lowest_j = -1;

	fname_len = strlen(fname);
	fname_suf = find_filename_suffix(fname, fname_len, &fname_suf_len);

	for (j = 0; j < dirlist->count; j++) {
		struct file_struct *fp = dirlist->files[j];
		const char *suf, *name;
		int len, suf_len;
		uint32 dist;

		if (!S_ISREG(fp->mode) || !fp->length
		    || fp->flags & FLAG_NO_FUZZY)
			continue;

		name = fp->basename;

		if (fp->length == file->length
		    && cmp_modtime(fp->modtime, file->modtime) == 0) {
			if (verbose > 4) {
				rprintf(FINFO,
					"fuzzy size/modtime match for %s\n",
					name);
			}
			return j;
		}

		len = strlen(name);
		suf = find_filename_suffix(name, len, &suf_len);

		dist = fuzzy_distance(name, len, fname, fname_len);
		/* Add some extra weight to how well the suffixes match. */
		dist += fuzzy_distance(suf, suf_len, fname_suf, fname_suf_len)
		      * 10;
		if (verbose > 4) {
			rprintf(FINFO, "fuzzy distance for %s = %d.%05d\n",
				name, (int)(dist>>16), (int)(dist&0xFFFF));
		}
		if (dist <= lowest_dist) {
			lowest_dist = dist;
			lowest_j = j;
		}
	}

	return lowest_j;
}

void check_for_finished_hlinks(int itemizing, enum logcode code)
{
	struct file_struct *file;
	int ndx;

	while ((ndx = get_hlink_num()) != -1) {
		if (ndx < 0 || ndx >= the_file_list->count)
			continue;

		file = the_file_list->files[ndx];
		if (!file->link_u.links)
			continue;

		hard_link_cluster(file, ndx, itemizing, code);
	}
}

static int phase = 0;

/* Acts on the_file_list->file's ndx'th item, whose name is fname.  If a dir,
 * make sure it exists, and has the right permissions/timestamp info.  For
 * all other non-regular files (symlinks, etc.) we create them here.  For
 * regular files that have changed, we try to find a basis file and then
 * start sending checksums.
 *
 * Note that f_out is set to -1 when doing final directory-permission and
 * modification-time repair. */
static void recv_generator(char *fname, struct file_struct *file, int ndx,
			   int itemizing, int maybe_PERMS_REPORT,
			   enum logcode code, int f_out)
{
	static int missing_below = -1, excluded_below = -1;
	static char *fuzzy_dirname = "";
	static struct file_list *fuzzy_dirlist = NULL;
	struct file_struct *fuzzy_file = NULL;
	int fd = -1, f_copy = -1;
	STRUCT_STAT st, real_st, partial_st;
	struct file_struct *back_file = NULL;
	int statret, real_ret, stat_errno;
	char *fnamecmp, *partialptr, *backupptr = NULL;
	char fnamecmpbuf[MAXPATHLEN];
	uchar fnamecmp_type;

	if (list_only)
		return;

	if (!fname) {
		if (fuzzy_dirlist) {
			flist_free(fuzzy_dirlist);
			fuzzy_dirlist = NULL;
			fuzzy_dirname = "";
		}
		if (missing_below >= 0) {
			dry_run--;
			missing_below = -1;
		}
		return;
	}

	if (verbose > 2) {
		rprintf(FINFO, "recv_generator(%s,%d)\n",
			safe_fname(fname), ndx);
	}

	if (server_filter_list.head) {
		if (excluded_below >= 0) {
			if (file->dir.depth > excluded_below)
				goto skipping;
			excluded_below = -1;
		}
		if (check_filter(&server_filter_list, fname,
				 S_ISDIR(file->mode)) < 0) {
			if (S_ISDIR(file->mode))
				excluded_below = file->dir.depth;
		    skipping:
			if (verbose) {
				rprintf(FINFO,
					"skipping server-excluded file \"%s\"\n",
					safe_fname(fname));
			}
			return;
		}
	}

	if (missing_below >= 0 && file->dir.depth <= missing_below) {
		dry_run--;
		missing_below = -1;
	}
	if (dry_run > 1) {
		statret = -1;
		stat_errno = ENOENT;
	} else {
		if (fuzzy_basis && S_ISREG(file->mode)) {
			char *dn = file->dirname ? file->dirname : ".";
			if (fuzzy_dirname != dn
			    && strcmp(fuzzy_dirname, dn) != 0) {
				if (fuzzy_dirlist)
					flist_free(fuzzy_dirlist);
				fuzzy_dirlist = get_dirlist(dn, -1, 1);
			}
			fuzzy_dirname = dn;
		}

		statret = link_stat(fname, &st,
				    keep_dirlinks && S_ISDIR(file->mode));
		stat_errno = errno;
	}

	if (only_existing && statret == -1 && stat_errno == ENOENT) {
		/* we only want to update existing files */
		if (verbose > 1) {
			rprintf(FINFO, "not creating new %s \"%s\"\n",
				S_ISDIR(file->mode) ? "directory" : "file",
				safe_fname(fname));
		}
		return;
	}

	if (statret == 0 && !preserve_perms
	    && S_ISDIR(st.st_mode) == S_ISDIR(file->mode)) {
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
		 * (perhaps recursively) create it. */
		if (statret == 0 && !S_ISDIR(st.st_mode)) {
			if (delete_item(fname, st.st_mode, DEL_TERSE) < 0)
				return;
			statret = -1;
		}
		if (dry_run && statret != 0 && missing_below < 0) {
			missing_below = file->dir.depth;
			dry_run++;
		}
		if (itemizing && f_out != -1) {
			itemize(file, ndx, statret, &st,
				statret ? ITEM_LOCAL_CHANGE : 0, 0, NULL);
		}
		if (statret != 0 && do_mkdir(fname,file->mode) != 0 && errno != EEXIST) {
			if (!relative_paths || errno != ENOENT
			    || create_directory_path(fname, orig_umask) < 0
			    || (do_mkdir(fname, file->mode) < 0 && errno != EEXIST)) {
				rsyserr(FERROR, errno,
					"recv_generator: mkdir %s failed",
					full_fname(fname));
			}
		}
		if (set_perms(fname, file, statret ? NULL : &st, 0)
		    && verbose && code && f_out != -1)
			rprintf(code, "%s/\n", safe_fname(fname));
		if (delete_during && f_out != -1 && !phase && dry_run < 2
		    && (file->flags & FLAG_DEL_HERE))
			delete_in_dir(the_file_list, fname, file);
		return;
	}

	if (preserve_links && S_ISLNK(file->mode)) {
#ifdef SUPPORT_LINKS
		if (safe_symlinks && unsafe_symlink(file->u.link, fname)) {
			if (verbose) {
				if (the_file_list->count == 1)
					fname = f_name(file);
				rprintf(FINFO,
					"ignoring unsafe symlink %s -> \"%s\"\n",
					full_fname(fname),
					safe_fname(file->u.link));
			}
			return;
		}
		if (statret == 0) {
			char lnk[MAXPATHLEN];
			int len;

			if (!S_ISDIR(st.st_mode)
			    && (len = readlink(fname, lnk, MAXPATHLEN-1)) > 0) {
				lnk[len] = 0;
				/* A link already pointing to the
				 * right place -- no further action
				 * required. */
				if (strcmp(lnk, file->u.link) == 0) {
					if (itemizing) {
						itemize(file, ndx, 0, &st, 0,
							0, NULL);
					}
					set_perms(fname, file, &st,
						  maybe_PERMS_REPORT);
					return;
				}
			}
			/* Not the right symlink (or not a symlink), so
			 * delete it. */
			if (delete_item(fname, st.st_mode, DEL_TERSE) < 0)
				return;
			if (!S_ISLNK(st.st_mode))
				statret = -1;
		}
		if (do_symlink(file->u.link,fname) != 0) {
			rsyserr(FERROR, errno, "symlink %s -> \"%s\" failed",
				full_fname(fname), safe_fname(file->u.link));
		} else {
			set_perms(fname,file,NULL,0);
			if (itemizing) {
				itemize(file, ndx, statret, &st,
					ITEM_LOCAL_CHANGE, 0, NULL);
			}
			if (code && verbose) {
				rprintf(code, "%s -> %s\n", safe_fname(fname),
					safe_fname(file->u.link));
			}
			if (remove_sent_files && !dry_run) {
				char numbuf[4];
				SIVAL(numbuf, 0, ndx);
				send_msg(MSG_SUCCESS, numbuf, 4);
			}
		}
#endif
		return;
	}

	if (am_root && preserve_devices && IS_DEVICE(file->mode)) {
		if (statret != 0 ||
		    st.st_mode != file->mode ||
		    st.st_rdev != file->u.rdev) {
			if (delete_item(fname, st.st_mode, DEL_TERSE) < 0)
				return;
			if (!IS_DEVICE(st.st_mode))
				statret = -1;
			if (verbose > 2) {
				rprintf(FINFO,"mknod(%s,0%o,0x%x)\n",
					safe_fname(fname),
					(int)file->mode, (int)file->u.rdev);
			}
			if (do_mknod(fname,file->mode,file->u.rdev) != 0) {
				rsyserr(FERROR, errno, "mknod %s failed",
					full_fname(fname));
			} else {
				set_perms(fname,file,NULL,0);
				if (itemizing) {
					itemize(file, ndx, statret, &st,
						ITEM_LOCAL_CHANGE, 0, NULL);
				}
				if (code && verbose) {
					rprintf(code, "%s\n",
						safe_fname(fname));
				}
			}
		} else {
			if (itemizing)
				itemize(file, ndx, statret, &st, 0, 0, NULL);
			set_perms(fname, file, &st, maybe_PERMS_REPORT);
		}
		return;
	}

	if (preserve_hard_links
	    && hard_link_check(file, ndx, fname, statret, &st,
			       itemizing, code, HL_CHECK_MASTER))
		return;

	if (!S_ISREG(file->mode)) {
		if (the_file_list->count == 1)
			fname = f_name(file);
		rprintf(FINFO, "skipping non-regular file \"%s\"\n",
			safe_fname(fname));
		return;
	}

	if (max_size && file->length > max_size) {
		if (verbose > 1) {
			if (the_file_list->count == 1)
				fname = f_name(file);
			rprintf(FINFO, "%s is over max-size\n",
				safe_fname(fname));
		}
		return;
	}

	if (opt_ignore_existing && statret == 0) {
		if (verbose > 1)
			rprintf(FINFO, "%s exists\n", safe_fname(fname));
		return;
	}

	if (update_only && statret == 0
	    && cmp_modtime(st.st_mtime, file->modtime) > 0) {
		if (verbose > 1)
			rprintf(FINFO, "%s is newer\n", safe_fname(fname));
		return;
	}

	fnamecmp = fname;
	fnamecmp_type = FNAMECMP_FNAME;

	if (statret == 0 && !S_ISREG(st.st_mode)) {
		if (delete_item(fname, st.st_mode, DEL_TERSE) != 0)
			return;
		statret = -1;
		stat_errno = ENOENT;
	}

	if (statret != 0 && basis_dir[0] != NULL) {
		int best_match = -1;
		int match_level = 0;
		int i = 0;
		do {
			pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
				 basis_dir[i], fname);
			if (link_stat(fnamecmpbuf, &st, 0) < 0
			    || !S_ISREG(st.st_mode))
				continue;
			switch (match_level) {
			case 0:
				best_match = i;
				match_level = 1;
				/* FALL THROUGH */
			case 1:
				if (!unchanged_file(fnamecmpbuf, file, &st))
					continue;
				best_match = i;
				match_level = 2;
				if (copy_dest)
					break;
				/* FALL THROUGH */
			case 2:
				if (!unchanged_attrs(file, &st))
					continue;
				best_match = i;
				match_level = 3;
				break;
			}
			break;
		} while (basis_dir[++i] != NULL);
		if (match_level) {
			statret = 0;
			if (i != best_match) {
				i = best_match;
				pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
					 basis_dir[i], fname);
				if (link_stat(fnamecmpbuf, &st, 0) < 0) {
					match_level = 0;
					statret = -1;
					stat_errno = errno;
				}
			}
#ifdef HAVE_LINK
			if (link_dest && match_level == 3) {
				if (hard_link_one(file, ndx, fname, -1, &st,
						  fnamecmpbuf, 1,
						  itemizing && verbose > 1,
						  code) == 0) {
					if (preserve_hard_links
					    && file->link_u.links) {
						hard_link_cluster(file, ndx,
								  itemizing,
								  code);
					}
					return;
				}
				match_level = 2;
			}
#endif
			if (match_level == 2) {
				/* Copy the file locally. */
				if (copy_file(fnamecmpbuf, fname, file->mode) < 0) {
					if (verbose) {
						rsyserr(FINFO, errno,
							"copy_file %s => %s",
							full_fname(fnamecmpbuf),
							safe_fname(fname));
					}
					match_level = 0;
					statret = -1;
				} else {
					if (itemizing) {
						itemize(file, ndx, 0, &st,
							ITEM_LOCAL_CHANGE, 0,
							NULL);
					} else if (verbose && code) {
						rprintf(code, "%s\n",
							safe_fname(fname));
					}
					set_perms(fname, file, NULL,
						  maybe_PERMS_REPORT);
					if (preserve_hard_links
					    && file->link_u.links) {
						hard_link_cluster(file, ndx,
								  itemizing,
								  code);
					}
					return;
				}
			} else if (compare_dest || match_level == 1) {
				fnamecmp = fnamecmpbuf;
				fnamecmp_type = i;
			}
		}
	}

	real_ret = statret;
	real_st = st;

	if (partial_dir && (partialptr = partial_dir_fname(fname)) != NULL
	    && link_stat(partialptr, &partial_st, 0) == 0
	    && S_ISREG(partial_st.st_mode)) {
		if (statret != 0)
			goto prepare_to_open;
	} else
		partialptr = NULL;

	if (statret != 0 && fuzzy_basis && dry_run <= 1) {
		int j = find_fuzzy(file, fuzzy_dirlist);
		if (j >= 0) {
			fuzzy_file = fuzzy_dirlist->files[j];
			f_name_to(fuzzy_file, fnamecmpbuf);
			if (verbose > 2) {
				rprintf(FINFO, "fuzzy basis selected for %s: %s\n",
					safe_fname(fname), safe_fname(fnamecmpbuf));
			}
			st.st_size = fuzzy_file->length;
			statret = 0;
			fnamecmp = fnamecmpbuf;
			fnamecmp_type = FNAMECMP_FUZZY;
		}
	}

	if (statret != 0) {
		if (preserve_hard_links
		    && hard_link_check(file, ndx, fname, statret, &st,
				       itemizing, code, HL_SKIP))
			return;
		if (stat_errno == ENOENT)
			goto notify_others;
		if (verbose > 1) {
			rsyserr(FERROR, stat_errno,
				"recv_generator: failed to stat %s",
				full_fname(fname));
		}
		return;
	}

	if (!compare_dest && fnamecmp_type <= FNAMECMP_BASIS_DIR_HIGH)
		;
	else if (fnamecmp_type == FNAMECMP_FUZZY)
		;
	else if (unchanged_file(fnamecmp, file, &st)) {
		if (fnamecmp_type == FNAMECMP_FNAME) {
			if (itemizing) {
				itemize(file, ndx, real_ret, &real_st,
					0, 0, NULL);
			}
			set_perms(fname, file, &st, maybe_PERMS_REPORT);
			if (preserve_hard_links && file->link_u.links)
				hard_link_cluster(file, ndx, itemizing, code);
			return;
		}
		/* Only --compare-dest gets here. */
		itemize(file, ndx, real_ret, &real_st,
			ITEM_NO_DEST_AND_NO_UPDATE, 0, NULL);
		return;
	}

prepare_to_open:
	if (partialptr) {
		st = partial_st;
		fnamecmp = partialptr;
		fnamecmp_type = FNAMECMP_PARTIAL_DIR;
		statret = 0;
	}

	if (!do_xfers || read_batch || whole_file)
		goto notify_others;

	if (fuzzy_basis) {
		int j = flist_find(fuzzy_dirlist, file);
		if (j >= 0) /* don't use changing file as future fuzzy basis */
			fuzzy_dirlist->files[j]->flags |= FLAG_NO_FUZZY;
	}

	/* open the file */
	fd = do_open(fnamecmp, O_RDONLY, 0);

	if (fd == -1) {
		rsyserr(FERROR, errno, "failed to open %s, continuing",
			full_fname(fnamecmp));
	    pretend_missing:
		/* pretend the file didn't exist */
		if (preserve_hard_links
		    && hard_link_check(file, ndx, fname, statret, &st,
				       itemizing, code, HL_SKIP))
			return;
		statret = real_ret = -1;
		goto notify_others;
	}

	if (inplace && make_backups && fnamecmp_type == FNAMECMP_FNAME) {
		if (!(backupptr = get_backup_name(fname))) {
			close(fd);
			return;
		}
		if (!(back_file = make_file(fname, NULL, NO_FILTERS))) {
			close(fd);
			goto pretend_missing;
		}
		if (robust_unlink(backupptr) && errno != ENOENT) {
			rsyserr(FERROR, errno, "unlink %s",
				full_fname(backupptr));
			free(back_file);
			close(fd);
			return;
		}
		if ((f_copy = do_open(backupptr,
		    O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, 0600)) < 0) {
			rsyserr(FERROR, errno, "open %s",
				full_fname(backupptr));
			free(back_file);
			close(fd);
			return;
		}
		fnamecmp_type = FNAMECMP_BACKUP;
	}

	if (verbose > 3) {
		rprintf(FINFO, "gen mapped %s of size %.0f\n",
			safe_fname(fnamecmp), (double)st.st_size);
	}

	if (verbose > 2)
		rprintf(FINFO, "generating and sending sums for %d\n", ndx);

notify_others:
	write_int(f_out, ndx);
	if (itemizing) {
		int iflags = ITEM_TRANSFER;
		if (always_checksum)
			iflags |= ITEM_REPORT_CHECKSUM;
		if (fnamecmp_type != FNAMECMP_FNAME)
			iflags |= ITEM_BASIS_TYPE_FOLLOWS;
		if (fnamecmp_type == FNAMECMP_FUZZY)
			iflags |= ITEM_XNAME_FOLLOWS;
		itemize(file, -1, real_ret, &real_st, iflags, fnamecmp_type,
			fuzzy_file ? fuzzy_file->basename : NULL);
	}

	if (!do_xfers) {
		if (preserve_hard_links && file->link_u.links)
			hard_link_cluster(file, ndx, itemizing, code);
		return;
	}
	if (read_batch)
		return;

	if (statret != 0 || whole_file) {
		write_sum_head(f_out, NULL);
		return;
	}

	generate_and_send_sums(fd, st.st_size, f_out, f_copy);

	if (f_copy >= 0) {
		close(f_copy);
		set_perms(backupptr, back_file, NULL, 0);
		if (verbose > 1) {
			rprintf(FINFO, "backed up %s to %s\n",
				safe_fname(fname), safe_fname(backupptr));
		}
		free(back_file);
	}

	close(fd);
}


void generate_files(int f_out, struct file_list *flist, char *local_name)
{
	int i;
	char fbuf[MAXPATHLEN];
	int itemizing, maybe_PERMS_REPORT;
	enum logcode code;
	int lull_mod = allowed_lull * 5;
	int need_retouch_dir_times = preserve_times && !omit_dir_times;
	int need_retouch_dir_perms = 0;
	int save_only_existing = only_existing;
	int save_opt_ignore_existing = opt_ignore_existing;
	int save_do_progress = do_progress;
	int save_make_backups = make_backups;

	if (protocol_version >= 29) {
		itemizing = 1;
		maybe_PERMS_REPORT = log_format_has_i ? 0 : PERMS_REPORT;
		code = daemon_log_format_has_i ? 0 : FLOG;
	} else if (am_daemon) {
		itemizing = daemon_log_format_has_i && do_xfers;
		maybe_PERMS_REPORT = PERMS_REPORT;
		code = itemizing || !do_xfers ? FCLIENT : FINFO;
	} else if (!am_server) {
		itemizing = log_format_has_i;
		maybe_PERMS_REPORT = log_format_has_i ? 0 : PERMS_REPORT;
		code = itemizing ? 0 : FINFO;
	} else {
		itemizing = 0;
		maybe_PERMS_REPORT = PERMS_REPORT;
		code = FINFO;
	}

	if (verbose > 2) {
		rprintf(FINFO, "generator starting pid=%ld count=%d\n",
			(long)getpid(), flist->count);
	}

	if (delete_before && !local_name && flist->count > 0)
		do_delete_pass(flist);
	do_progress = 0;

	if (whole_file < 0)
		whole_file = 0;
	if (verbose >= 2) {
		rprintf(FINFO, "delta-transmission %s\n",
			whole_file
			? "disabled for local transfer or --whole-file"
			: "enabled");
	}

	/* Since we often fill up the outgoing socket and then just sit around
	 * waiting for the other 2 processes to do their thing, we don't want
	 * to exit on a timeout.  If the data stops flowing, the receiver will
	 * notice that and let us know via the redo pipe (or its closing). */
	ignore_timeout = 1;

	for (i = 0; i < flist->count; i++) {
		struct file_struct *file = flist->files[i];

		if (!file->basename)
			continue;

		recv_generator(local_name ? local_name : f_name_to(file, fbuf),
			       file, i, itemizing, maybe_PERMS_REPORT, code,
			       f_out);

		/* We need to ensure that any dirs we create have writeable
		 * permissions during the time we are putting files within
		 * them.  This is then fixed after the transfer is done. */
		if (!am_root && S_ISDIR(file->mode) && !(file->mode & S_IWUSR)
		    && !list_only) {
			int mode = file->mode | S_IWUSR; /* user write */
			char *fname = local_name ? local_name : fbuf;
			if (do_chmod(fname, mode & CHMOD_BITS) < 0) {
				rsyserr(FERROR, errno,
					"failed to modify permissions on %s",
					full_fname(fname));
			}
			need_retouch_dir_perms = 1;
		}

		if (preserve_hard_links)
			check_for_finished_hlinks(itemizing, code);

		if (allowed_lull && !(i % lull_mod))
			maybe_send_keepalive();
		else if (!(i % 200))
			maybe_flush_socket();
	}
	recv_generator(NULL, NULL, 0, 0, 0, code, -1);
	if (delete_during)
		delete_in_dir(NULL, NULL, NULL);

	phase++;
	csum_length = SUM_LENGTH;
	only_existing = max_size = opt_ignore_existing = 0;
	update_only = always_checksum = size_only = 0;
	ignore_times = 1;
	make_backups = 0; /* avoid a duplicate backup for inplace processing */

	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f_out, -1);

	/* files can cycle through the system more than once
	 * to catch initial checksum errors */
	while ((i = get_redo_num(itemizing, code)) != -1) {
		struct file_struct *file = flist->files[i];
		recv_generator(local_name ? local_name : f_name_to(file, fbuf),
			       file, i, itemizing, maybe_PERMS_REPORT, code,
			       f_out);
	}

	phase++;
	only_existing = save_only_existing;
	opt_ignore_existing = save_opt_ignore_existing;
	make_backups = save_make_backups;

	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f_out, -1);
	/* Reduce round-trip lag-time for a useless delay-updates phase. */
	if (protocol_version >= 29 && !delay_updates)
		write_int(f_out, -1);

	/* Read MSG_DONE for the redo phase (and any prior messages). */
	get_redo_num(itemizing, code);

	if (protocol_version >= 29) {
		phase++;
		if (verbose > 2)
			rprintf(FINFO, "generate_files phase=%d\n", phase);
		if (delay_updates)
			write_int(f_out, -1);
		/* Read MSG_DONE for delay-updates phase & prior messages. */
		get_redo_num(itemizing, code);
	}

	do_progress = save_do_progress;
	if (delete_after && !local_name && flist->count > 0)
		do_delete_pass(flist);

	if ((need_retouch_dir_perms || need_retouch_dir_times)
	    && !list_only && !local_name && !dry_run) {
		int j = 0;
		/* Now we need to fix any directory permissions that were
		 * modified during the transfer and/or re-set any tweaked
		 * modified-time values. */
		for (i = 0; i < flist->count; i++) {
			struct file_struct *file = flist->files[i];
			if (!file->basename || !S_ISDIR(file->mode))
				continue;
			if (!need_retouch_dir_times && file->mode & S_IWUSR)
				continue;
			recv_generator(f_name(file), file, i, itemizing,
				       maybe_PERMS_REPORT, code, -1);
			if (allowed_lull && !(++j % lull_mod))
				maybe_send_keepalive();
			else if (!(j % 200))
				maybe_flush_socket();
		}
	}
	recv_generator(NULL, NULL, 0, 0, 0, code, -1);

	if (max_delete > 0 && deletion_count > max_delete) {
		rprintf(FINFO,
			"Deletions stopped due to --max-delete limit (%d skipped)\n",
			deletion_count - max_delete);
		io_error |= IOERR_DEL_LIMIT;
	}

	if (verbose > 2)
		rprintf(FINFO,"generate_files finished\n");
}
