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
extern int log_format_has_i;
extern int log_format_has_o_or_i;
extern int daemon_log_format_has_i;
extern int am_root;
extern int am_server;
extern int am_daemon;
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
extern int delete_during;
extern int update_only;
extern int opt_ignore_existing;
extern int inplace;
extern int make_backups;
extern int csum_length;
extern int ignore_times;
extern int size_only;
extern OFF_T max_size;
extern int io_timeout;
extern int protocol_version;
extern int fuzzy_basis;
extern int always_checksum;
extern char *partial_dir;
extern char *basis_dir[];
extern int compare_dest;
extern int link_dest;
extern int whole_file;
extern int local_server;
extern int list_only;
extern int read_batch;
extern int only_existing;
extern int orig_umask;
extern int safe_symlinks;
extern long block_size; /* "long" because popt can't set an int32. */
extern struct stats stats;

extern struct filter_list_struct server_filter_list;

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


#define SID_UPDATING		  ITEM_UPDATING
#define SID_REPORT_CHECKSUM	  ITEM_REPORT_CHECKSUM
#define SID_NO_DEST_AND_NO_UPDATE (1<<16)

static void itemize(struct file_struct *file, int statret, STRUCT_STAT *st,
		    int32 sflags, int f_out, int ndx)
{
	int iflags = sflags & (SID_UPDATING | SID_REPORT_CHECKSUM);

	if (statret >= 0) {
		if (S_ISREG(file->mode) && file->length != st->st_size)
			iflags |= ITEM_REPORT_SIZE;
		if (!(sflags & SID_NO_DEST_AND_NO_UPDATE)) {
			int keep_time = !preserve_times ? 0
			    : S_ISDIR(file->mode) ? !omit_dir_times
			    : !S_ISLNK(file->mode);

			if ((iflags & ITEM_UPDATING && !keep_time)
			    || (keep_time && file->modtime != st->st_mtime))
				iflags |= ITEM_REPORT_TIME;
			if (preserve_perms && file->mode != st->st_mode)
				iflags |= ITEM_REPORT_PERMS;
			if (preserve_uid && am_root && file->uid != st->st_uid)
				iflags |= ITEM_REPORT_OWNER;
			if (preserve_gid && file->gid != GID_NONE
			    && st->st_gid != file->gid)
				iflags |= ITEM_REPORT_GROUP;
		}
	} else
		iflags |= ITEM_IS_NEW | ITEM_UPDATING;

	if ((iflags || verbose > 1) && !read_batch) {
		if (protocol_version >= 29) {
			if (ndx >= 0)
				write_int(f_out, ndx);
			write_shortint(f_out, iflags);
		} else if (ndx >= 0)
			log_recv(file, &stats, iflags);
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
 *     blocksum_bits = BLOCKSUM_EXP + 2*log2(file_len) - log2(block_len)
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
	uint32 lowest_dist = 0x7FFFFFFF;
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
		    && fp->modtime == file->modtime) {
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


/* Acts on flist->file's ndx'th item, whose name is fname.  If a directory,
 * make sure it exists, and has the right permissions/timestamp info.  For
 * all other non-regular files (symlinks, etc.) we create them here.  For
 * regular files that have changed, we try to find a basis file and then
 * start sending checksums.
 *
 * Note that f_out is set to -1 when doing final directory-permission and
 * modification-time repair. */
static void recv_generator(char *fname, struct file_list *flist,
			   struct file_struct *file, int ndx,
			   int f_out, int f_out_name)
{
	static int missing_below = -1, excluded_below = -1;
	static char *fuzzy_dirname = NULL;
	static struct file_list *fuzzy_dirlist = NULL;
	struct file_struct *fuzzy_file = NULL;
	int fd = -1, f_copy = -1;
	STRUCT_STAT st, partial_st;
	struct file_struct *back_file = NULL;
	int statret, stat_errno;
	char *fnamecmp, *partialptr, *backupptr = NULL;
	char fnamecmpbuf[MAXPATHLEN];
	int itemizing, maybe_DEL_TERSE, maybe_PERMS_REPORT;
	uchar fnamecmp_type;
	enum logcode code;

	if (list_only)
		return;

	if (protocol_version >= 29) {
		itemizing = 1;
		code = daemon_log_format_has_i ? 0 : FLOG;
		maybe_DEL_TERSE = log_format_has_o_or_i ? 0 : DEL_TERSE;
		maybe_PERMS_REPORT = log_format_has_i ? 0 : PERMS_REPORT;
	} else if (am_daemon) {
		itemizing = daemon_log_format_has_i && !dry_run;
		code = itemizing || dry_run ? FCLIENT : FINFO;
		maybe_DEL_TERSE = DEL_TERSE;
		maybe_PERMS_REPORT = PERMS_REPORT;
	} else if (!am_server) {
		itemizing = log_format_has_i;
		code = itemizing ? 0 : FINFO;
		maybe_DEL_TERSE = log_format_has_o_or_i ? 0 : DEL_TERSE;
		maybe_PERMS_REPORT = log_format_has_i ? 0 : PERMS_REPORT;
	} else {
		itemizing = 0;
		code = FINFO;
		maybe_DEL_TERSE = DEL_TERSE;
		maybe_PERMS_REPORT = PERMS_REPORT;
	}

	if (!fname) {
		if (fuzzy_dirlist) {
			flist_free(fuzzy_dirlist);
			fuzzy_dirlist = NULL;
			fuzzy_dirname = NULL;
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
			/* Yes, identical dirnames are guaranteed to have
			 * identical pointers at this point. */
			if (fuzzy_dirname != dn) {
				if (fuzzy_dirlist)
					flist_free(fuzzy_dirlist);
				fuzzy_dirname = dn;
				fuzzy_dirlist = get_dirlist(fuzzy_dirname, 1);
			}
		}

		statret = link_stat(fname, &st,
				    keep_dirlinks && S_ISDIR(file->mode));
		stat_errno = errno;
	}

	if (only_existing && statret == -1 && stat_errno == ENOENT) {
		/* we only want to update existing files */
		if (verbose > 1) {
			rprintf(FINFO, "not creating new file \"%s\"\n",
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
			delete_file(fname, st.st_mode, maybe_DEL_TERSE);
			statret = -1;
		}
		if (dry_run && statret != 0 && missing_below < 0) {
			missing_below = file->dir.depth;
			dry_run++;
		}
		if (itemizing && f_out != -1)
			itemize(file, statret, &st, 0, f_out, ndx);
		if (statret != 0 && do_mkdir(fname,file->mode) != 0 && errno != EEXIST) {
			if (!relative_paths || errno != ENOENT
			    || create_directory_path(fname, orig_umask) < 0
			    || do_mkdir(fname, file->mode) < 0) {
				rsyserr(FERROR, errno,
					"recv_generator: mkdir %s failed",
					full_fname(fname));
			}
		}
		if (set_perms(fname, file, statret ? NULL : &st, 0)
		    && verbose && code && f_out != -1)
			rprintf(code, "%s/\n", safe_fname(fname));
		if (delete_during && f_out != -1 && csum_length != SUM_LENGTH
		    && (file->flags & FLAG_DEL_HERE))
			delete_in_dir(flist, fname, file);
		return;
	}

	if (max_size && file->length > max_size) {
		if (verbose > 1) {
			rprintf(FINFO, "%s is over max-size\n",
				safe_fname(fname));
		}
		return;
	}

	if (preserve_links && S_ISLNK(file->mode)) {
#ifdef SUPPORT_LINKS
		if (safe_symlinks && unsafe_symlink(file->u.link, fname)) {
			if (verbose) {
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
						itemize(file, 0, &st, 0,
							f_out, ndx);
					}
					set_perms(fname, file, &st,
						  maybe_PERMS_REPORT);
					return;
				}
			}
			/* Not the right symlink (or not a symlink), so
			 * delete it. */
			if (S_ISLNK(st.st_mode))
				delete_file(fname, st.st_mode, DEL_TERSE);
			else {
				delete_file(fname, st.st_mode, maybe_DEL_TERSE);
				statret = -1;
			}
		}
		if (do_symlink(file->u.link,fname) != 0) {
			rsyserr(FERROR, errno, "symlink %s -> \"%s\" failed",
				full_fname(fname), safe_fname(file->u.link));
		} else {
			set_perms(fname,file,NULL,0);
			if (itemizing) {
				itemize(file, statret, &st, SID_UPDATING,
					f_out, ndx);
			}
			if (code && verbose) {
				rprintf(code, "%s -> %s\n", safe_fname(fname),
					safe_fname(file->u.link));
			}
		}
#endif
		return;
	}

	if (am_root && preserve_devices && IS_DEVICE(file->mode)) {
		if (statret != 0 ||
		    st.st_mode != file->mode ||
		    st.st_rdev != file->u.rdev) {
			if (IS_DEVICE(st.st_mode))
				delete_file(fname, st.st_mode, DEL_TERSE);
			else {
				delete_file(fname, st.st_mode, maybe_DEL_TERSE);
				statret = -1;
			}
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
					itemize(file, statret, &st, SID_UPDATING,
						f_out, ndx);
				}
				if (code && verbose) {
					rprintf(code, "%s\n",
						safe_fname(fname));
				}
			}
		} else {
			if (itemizing) {
				itemize(file, statret, &st, 0,
					f_out, ndx);
			}
			set_perms(fname, file, &st, maybe_PERMS_REPORT);
		}
		return;
	}

	if (preserve_hard_links && hard_link_check(file, HL_CHECK_MASTER))
		return;

	if (!S_ISREG(file->mode)) {
		rprintf(FINFO, "skipping non-regular file \"%s\"\n",
			safe_fname(fname));
		return;
	}

	fnamecmp = fname;
	fnamecmp_type = FNAMECMP_FNAME;

	if (statret != 0 && basis_dir[0] != NULL) {
		int fallback_match = -1;
		int match_level = 0;
		int i = 0;
		do {
			pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
				 basis_dir[i], fname);
			if (link_stat(fnamecmpbuf, &st, 0) == 0
			    && S_ISREG(st.st_mode)) {
				statret = 0;
				if (link_dest) {
					if (!match_level) {
						fallback_match = i;
						match_level = 1;
					} else if (match_level == 2
					    && !unchanged_attrs(file, &st))
						continue;
					if (!unchanged_file(fnamecmpbuf, file, &st))
						continue;
					fallback_match = i;
					match_level = 2;
					if (!unchanged_attrs(file, &st))
						continue;
				}
				match_level = 3;
				break;
			}
		} while (basis_dir[++i] != NULL);
		if (statret == 0) {
			if (match_level < 3) {
				i = fallback_match;
				pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
					 basis_dir[i], fname);
			}
#ifdef HAVE_LINK
			if (link_dest && match_level == 3 && !dry_run) {
				if (do_link(fnamecmpbuf, fname) < 0) {
					if (verbose) {
						rsyserr(FINFO, errno,
							"link %s => %s",
							full_fname(fnamecmpbuf),
							safe_fname(fname));
					}
					fnamecmp = fnamecmpbuf;
					fnamecmp_type = i;
				}
			} else
#endif
			{
				fnamecmp = fnamecmpbuf;
				fnamecmp_type = i;
			}
		}
	}

	if (statret == 0 && !S_ISREG(st.st_mode)) {
		if (delete_file(fname, st.st_mode, maybe_DEL_TERSE) != 0)
			return;
		statret = -1;
		stat_errno = ENOENT;
	}

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
			st.st_mode = fuzzy_file->mode;
			st.st_size = fuzzy_file->length;
			st.st_mtime = fuzzy_file->modtime;
			statret = 0;
			fnamecmp = fnamecmpbuf;
			fnamecmp_type = FNAMECMP_FUZZY;
		}
	}

	if (statret != 0) {
		if (preserve_hard_links && hard_link_check(file, HL_SKIP))
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

	if (opt_ignore_existing && fnamecmp_type == FNAMECMP_FNAME) {
		if (verbose > 1)
			rprintf(FINFO, "%s exists\n", safe_fname(fname));
		return;
	}

	if (update_only && fnamecmp_type == FNAMECMP_FNAME
	    && cmp_modtime(st.st_mtime, file->modtime) > 0) {
		if (verbose > 1)
			rprintf(FINFO, "%s is newer\n", safe_fname(fname));
		return;
	}

	if (!compare_dest && fnamecmp_type <= FNAMECMP_BASIS_DIR_HIGH)
		;
	else if (fnamecmp_type == FNAMECMP_FUZZY)
		;
	else if (unchanged_file(fnamecmp, file, &st)) {
		if (itemizing) {
			itemize(file, statret, &st,
				fnamecmp_type == FNAMECMP_FNAME
					       ? 0 : SID_NO_DEST_AND_NO_UPDATE,
				f_out, ndx);
		}
		if (fnamecmp_type == FNAMECMP_FNAME)
			set_perms(fname, file, &st, maybe_PERMS_REPORT);
		return;
	}

prepare_to_open:
	if (partialptr) {
		st = partial_st;
		fnamecmp = partialptr;
		fnamecmp_type = FNAMECMP_PARTIAL_DIR;
		statret = 0;
	}

	if (dry_run || read_batch)
		goto notify_others;
	if (whole_file > 0) {
		if (statret == 0)
			statret = 1;
		goto notify_others;
	}

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
		if (preserve_hard_links && hard_link_check(file, HL_SKIP))
			return;
		statret = -1;
		goto notify_others;
	}

	if (inplace && make_backups) {
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
		itemize(file, statret, &st, SID_UPDATING
			| (always_checksum ? SID_REPORT_CHECKSUM : 0),
			f_out, -1);
		if (inplace && !read_batch)
			write_byte(f_out, fnamecmp_type);
	}
	if (f_out_name >= 0) {
		write_byte(f_out_name, fnamecmp_type);
		if (fnamecmp_type == FNAMECMP_FUZZY) {
			uchar lenbuf[3], *lb = lenbuf;
			int len = strlen(fuzzy_file->basename);
			if (len > 0x7F) {
#if MAXPATHLEN > 0x7FFF
				*lb++ = len / 0x10000 + 0x80;
				*lb++ = len / 0x100;
#else
				*lb++ = len / 0x100 + 0x80;
#endif
			}
			*lb = len;
			write_buf(f_out_name, lenbuf, lb - lenbuf + 1);
			write_buf(f_out_name, fuzzy_file->basename, len);
		}
	}

	if (dry_run || read_batch)
		return;

	if (statret == 0) {
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
	} else
		write_sum_head(f_out, NULL);
}


void generate_files(int f_out, struct file_list *flist, char *local_name,
		    int f_out_name)
{
	int i;
	int phase = 0;
	char fbuf[MAXPATHLEN];
	int need_retouch_dir_times = preserve_times && !omit_dir_times;
	int need_retouch_dir_perms = 0;
	int save_only_existing = only_existing;
	int save_opt_ignore_existing = opt_ignore_existing;

	if (verbose > 2) {
		rprintf(FINFO, "generator starting pid=%ld count=%d\n",
			(long)getpid(), flist->count);
	}

	if (verbose >= 2) {
		rprintf(FINFO,
			whole_file > 0
			? "delta-transmission disabled for local transfer or --whole-file\n"
			: "delta transmission enabled\n");
	}

	/* We expect to just sit around now, so don't exit on a timeout.
	 * If we really get a timeout then the other process should exit. */
	io_timeout = 0;

	for (i = 0; i < flist->count; i++) {
		struct file_struct *file = flist->files[i];
		struct file_struct copy;

		if (!file->basename)
			continue;

		/* We need to ensure that any dirs we create have writeable
		 * permissions during the time we are putting files within
		 * them.  This is then fixed after the transfer is done. */
		if (!am_root && S_ISDIR(file->mode) && !(file->mode & S_IWUSR)) {
			copy = *file;
			copy.mode |= S_IWUSR; /* user write */
			file = &copy;
			need_retouch_dir_perms = 1;
		}

		recv_generator(local_name ? local_name : f_name_to(file, fbuf),
			       flist, file, i, f_out, f_out_name);
	}
	recv_generator(NULL, NULL, NULL, 0, -1, -1);
	if (delete_during)
		delete_in_dir(NULL, NULL, NULL);

	phase++;
	csum_length = SUM_LENGTH;
	only_existing = max_size = opt_ignore_existing = 0;
	update_only = always_checksum = size_only = 0;
	ignore_times = 1;

	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f_out, -1);

	/* files can cycle through the system more than once
	 * to catch initial checksum errors */
	while ((i = get_redo_num()) != -1) {
		struct file_struct *file = flist->files[i];
		recv_generator(local_name ? local_name : f_name_to(file, fbuf),
			       flist, file, i, f_out, f_out_name);
	}

	phase++;
	only_existing = save_only_existing;
	opt_ignore_existing = save_opt_ignore_existing;

	if (verbose > 2)
		rprintf(FINFO,"generate_files phase=%d\n",phase);

	write_int(f_out, -1);

	/* Read post-redo-phase MSG_DONE and any prior messages. */
	get_redo_num();

	if (preserve_hard_links)
		do_hard_links();

	if ((need_retouch_dir_perms || need_retouch_dir_times)
	    && !list_only && !local_name && !dry_run) {
		/* Now we need to fix any directory permissions that were
		 * modified during the transfer and/or re-set any tweaked
		 * modified-time values. */
		for (i = 0; i < flist->count; i++) {
			struct file_struct *file = flist->files[i];
			if (!file->basename || !S_ISDIR(file->mode))
				continue;
			if (!need_retouch_dir_times && file->mode & S_IWUSR)
				continue;
			recv_generator(local_name ? local_name : f_name(file),
				       flist, file, i, -1, -1);
		}
	}
	recv_generator(NULL, NULL, NULL, 0, -1, -1);

	if (verbose > 2)
		rprintf(FINFO,"generate_files finished\n");
}
