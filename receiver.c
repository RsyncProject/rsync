/*
 * Routines only used by the receiving process.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003-2015 Wayne Davison
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
#include "inums.h"

extern int dry_run;
extern int do_xfers;
extern int am_root;
extern int am_server;
extern int inc_recurse;
extern int log_before_transfer;
extern int stdout_format_has_i;
extern int logfile_format_has_i;
extern int want_xattr_optim;
extern int csum_length;
extern int read_batch;
extern int write_batch;
extern int batch_gen_fd;
extern int protocol_version;
extern int relative_paths;
extern int preserve_hard_links;
extern int preserve_perms;
extern int preserve_xattrs;
extern int basis_dir_cnt;
extern int make_backups;
extern int cleanup_got_literal;
extern int remove_source_files;
extern int append_mode;
extern int sparse_files;
extern int preallocate_files;
extern int keep_partial;
extern int checksum_len;
extern int checksum_seed;
extern int inplace;
extern int allowed_lull;
extern int delay_updates;
extern mode_t orig_umask;
extern struct stats stats;
extern char *tmpdir;
extern char *partial_dir;
extern char *basis_dir[MAX_BASIS_DIRS+1];
extern char sender_file_sum[MAX_DIGEST_LEN];
extern struct file_list *cur_flist, *first_flist, *dir_flist;
extern filter_rule_list daemon_filter_list;

static struct bitbag *delayed_bits = NULL;
static int phase = 0, redoing = 0;
static flist_ndx_list batch_redo_list;
/* We're either updating the basis file or an identical copy: */
static int updating_basis_or_equiv;

#define TMPNAME_SUFFIX ".XXXXXX"
#define TMPNAME_SUFFIX_LEN ((int)sizeof TMPNAME_SUFFIX - 1)
#define MAX_UNIQUE_NUMBER 999999
#define MAX_UNIQUE_LOOP 100

/* get_tmpname() - create a tmp filename for a given filename
 *
 * If a tmpdir is defined, use that as the directory to put it in.  Otherwise,
 * the tmp filename is in the same directory as the given name.  Note that
 * there may be no directory at all in the given name!
 *
 * The tmp filename is basically the given filename with a dot prepended, and
 * .XXXXXX appended (for mkstemp() to put its unique gunk in).  We take care
 * to not exceed either the MAXPATHLEN or NAME_MAX, especially the last, as
 * the basename basically becomes 8 characters longer.  In such a case, the
 * original name is shortened sufficiently to make it all fit.
 *
 * If the make_unique arg is True, the XXXXXX string is replaced with a unique
 * string that doesn't exist at the time of the check.  This is intended to be
 * used for creating hard links, symlinks, devices, and special files, since
 * normal files should be handled by mkstemp() for safety.
 *
 * Of course, the only reason the file is based on the original name is to
 * make it easier to figure out what purpose a temp file is serving when a
 * transfer is in progress. */
int get_tmpname(char *fnametmp, const char *fname, BOOL make_unique)
{
	int maxname, length = 0;
	const char *f;
	char *suf;

	if (tmpdir) {
		/* Note: this can't overflow, so the return value is safe */
		length = strlcpy(fnametmp, tmpdir, MAXPATHLEN - 2);
		fnametmp[length++] = '/';
	}

	if ((f = strrchr(fname, '/')) != NULL) {
		++f;
		if (!tmpdir) {
			length = f - fname;
			/* copy up to and including the slash */
			strlcpy(fnametmp, fname, length + 1);
		}
	} else
		f = fname;

	if (!tmpdir) { /* using a tmpdir avoids the leading dot on our temp names */
		if (*f == '.') /* avoid an extra leading dot for OS X's sake */
			f++;
		fnametmp[length++] = '.';
	}

	/* The maxname value is bufsize, and includes space for the '\0'.
	 * NAME_MAX needs an extra -1 for the name's leading dot. */
	maxname = MIN(MAXPATHLEN - length - TMPNAME_SUFFIX_LEN,
		      NAME_MAX - 1 - TMPNAME_SUFFIX_LEN);

	if (maxname < 0) {
		rprintf(FERROR_XFER, "temporary filename too long: %s\n", fname);
		fnametmp[0] = '\0';
		return 0;
	}

	if (maxname) {
		int added = strlcpy(fnametmp + length, f, maxname);
		if (added >= maxname)
			added = maxname - 1;
		suf = fnametmp + length + added;

		/* Trim any dangling high-bit chars if the first-trimmed char (if any) is
		 * also a high-bit char, just in case we cut into a multi-byte sequence.
		 * We are guaranteed to stop because of the leading '.' we added. */
		if ((int)f[added] & 0x80) {
			while ((int)suf[-1] & 0x80)
				suf--;
		}
		/* trim one trailing dot before our suffix's dot */
		if (suf[-1] == '.')
			suf--;
	} else
		suf = fnametmp + length - 1; /* overwrite the leading dot with suffix's dot */

	if (make_unique) {
		static unsigned counter_limit;
		unsigned counter;

		if (!counter_limit) {
			counter_limit = (unsigned)getpid() + MAX_UNIQUE_LOOP;
			if (counter_limit > MAX_UNIQUE_NUMBER || counter_limit < MAX_UNIQUE_LOOP)
				counter_limit = MAX_UNIQUE_LOOP;
		}
		counter = counter_limit - MAX_UNIQUE_LOOP;

		/* This doesn't have to be very good because we don't need
		 * to worry about someone trying to guess the values:  all
		 * a conflict will do is cause a device, special file, hard
		 * link, or symlink to fail to be created.  Also: avoid
		 * using mktemp() due to gcc's annoying warning. */
		while (1) {
			snprintf(suf, TMPNAME_SUFFIX_LEN+1, ".%d", counter);
			if (access(fnametmp, 0) < 0)
				break;
			if (++counter >= counter_limit)
				return 0;
		}
	} else
		memcpy(suf, TMPNAME_SUFFIX, TMPNAME_SUFFIX_LEN+1);

	return 1;
}

/* Opens a temporary file for writing.
 * Success: Writes name into fnametmp, returns fd.
 * Failure: Clobbers fnametmp, returns -1.
 * Calling cleanup_set() is the caller's job. */
int open_tmpfile(char *fnametmp, const char *fname, struct file_struct *file)
{
	int fd;
	mode_t added_perms;

	if (!get_tmpname(fnametmp, fname, False))
		return -1;

	if (am_root < 0) {
		/* For --fake-super, the file must be useable by the copying
		 * user, just like it would be for root. */
		added_perms = S_IRUSR|S_IWUSR;
	} else {
		/* For a normal copy, we need to be able to tweak things like xattrs. */
		added_perms = S_IWUSR;
	}

	/* We initially set the perms without the setuid/setgid bits or group
	 * access to ensure that there is no race condition.  They will be
	 * correctly updated after the right owner and group info is set.
	 * (Thanks to snabb@epipe.fi for pointing this out.) */
	fd = do_mkstemp(fnametmp, (file->mode|added_perms) & INITACCESSPERMS);

#if 0
	/* In most cases parent directories will already exist because their
	 * information should have been previously transferred, but that may
	 * not be the case with -R */
	if (fd == -1 && relative_paths && errno == ENOENT
	 && make_path(fnametmp, MKP_SKIP_SLASH | MKP_DROP_NAME) == 0) {
		/* Get back to name with XXXXXX in it. */
		get_tmpname(fnametmp, fname, False);
		fd = do_mkstemp(fnametmp, (file->mode|added_perms) & INITACCESSPERMS);
	}
#endif

	if (fd == -1) {
		rsyserr(FERROR_XFER, errno, "mkstemp %s failed",
			full_fname(fnametmp));
		return -1;
	}

	return fd;
}

static int receive_data(int f_in, char *fname_r, int fd_r, OFF_T size_r,
			const char *fname, int fd, OFF_T total_size)
{
	static char file_sum1[MAX_DIGEST_LEN];
	struct map_struct *mapbuf;
	struct sum_struct sum;
	int32 len;
	OFF_T offset = 0;
	OFF_T offset2;
	char *data;
	int32 i;
	char *map = NULL;
#ifdef SUPPORT_PREALLOCATION
#ifdef PREALLOCATE_NEEDS_TRUNCATE
	OFF_T preallocated_len = 0;
#endif

	if (preallocate_files && fd != -1 && total_size > 0 && (!inplace || total_size > size_r)) {
		/* Try to preallocate enough space for file's eventual length.  Can
		 * reduce fragmentation on filesystems like ext4, xfs, and NTFS. */
		if (do_fallocate(fd, 0, total_size) == 0) {
#ifdef PREALLOCATE_NEEDS_TRUNCATE
			preallocated_len = total_size;
#endif
		} else
			rsyserr(FWARNING, errno, "do_fallocate %s", full_fname(fname));
	}
#endif

	read_sum_head(f_in, &sum);

	if (fd_r >= 0 && size_r > 0) {
		int32 read_size = MAX(sum.blength * 2, 16*1024);
		mapbuf = map_file(fd_r, size_r, read_size, sum.blength);
		if (DEBUG_GTE(DELTASUM, 2)) {
			rprintf(FINFO, "recv mapped %s of size %s\n",
				fname_r, big_num(size_r));
		}
	} else
		mapbuf = NULL;

	sum_init(checksum_seed);

	if (append_mode > 0) {
		OFF_T j;
		sum.flength = (OFF_T)sum.count * sum.blength;
		if (sum.remainder)
			sum.flength -= sum.blength - sum.remainder;
		if (append_mode == 2 && mapbuf) {
			for (j = CHUNK_SIZE; j < sum.flength; j += CHUNK_SIZE) {
				if (INFO_GTE(PROGRESS, 1))
					show_progress(offset, total_size);
				sum_update(map_ptr(mapbuf, offset, CHUNK_SIZE),
					   CHUNK_SIZE);
				offset = j;
			}
			if (offset < sum.flength) {
				int32 len = (int32)(sum.flength - offset);
				if (INFO_GTE(PROGRESS, 1))
					show_progress(offset, total_size);
				sum_update(map_ptr(mapbuf, offset, len), len);
			}
		}
		offset = sum.flength;
		if (fd != -1 && (j = do_lseek(fd, offset, SEEK_SET)) != offset) {
			rsyserr(FERROR_XFER, errno, "lseek of %s returned %s, not %s",
				full_fname(fname), big_num(j), big_num(offset));
			exit_cleanup(RERR_FILEIO);
		}
	}

	while ((i = recv_token(f_in, &data)) != 0) {
		if (INFO_GTE(PROGRESS, 1))
			show_progress(offset, total_size);

		if (allowed_lull)
			maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH | MSK_ACTIVE_RECEIVER);

		if (i > 0) {
			if (DEBUG_GTE(DELTASUM, 3)) {
				rprintf(FINFO,"data recv %d at %s\n",
					i, big_num(offset));
			}

			stats.literal_data += i;
			cleanup_got_literal = 1;

			sum_update(data, i);

			if (fd != -1 && write_file(fd,data,i) != i)
				goto report_write_error;
			offset += i;
			continue;
		}

		i = -(i+1);
		offset2 = i * (OFF_T)sum.blength;
		len = sum.blength;
		if (i == (int)sum.count-1 && sum.remainder != 0)
			len = sum.remainder;

		stats.matched_data += len;

		if (DEBUG_GTE(DELTASUM, 3)) {
			rprintf(FINFO,
				"chunk[%d] of size %ld at %s offset=%s%s\n",
				i, (long)len, big_num(offset2), big_num(offset),
				updating_basis_or_equiv && offset == offset2 ? " (seek)" : "");
		}

		if (mapbuf) {
			map = map_ptr(mapbuf,offset2,len);

			see_token(map, len);
			sum_update(map, len);
		}

		if (updating_basis_or_equiv) {
			if (offset == offset2 && fd != -1) {
				OFF_T pos;
				if (flush_write_file(fd) < 0)
					goto report_write_error;
				offset += len;
				if ((pos = do_lseek(fd, len, SEEK_CUR)) != offset) {
					rsyserr(FERROR_XFER, errno,
						"lseek of %s returned %s, not %s",
						full_fname(fname),
						big_num(pos), big_num(offset));
					exit_cleanup(RERR_FILEIO);
				}
				continue;
			}
		}
		if (fd != -1 && map && write_file(fd, map, len) != (int)len)
			goto report_write_error;
		offset += len;
	}

	if (flush_write_file(fd) < 0)
		goto report_write_error;

#ifdef HAVE_FTRUNCATE
	/* inplace: New data could be shorter than old data.
	 * preallocate_files: total_size could have been an overestimate.
	 *     Cut off any extra preallocated zeros from dest file. */
	if ((inplace
#ifdef PREALLOCATE_NEEDS_TRUNCATE
	  || preallocated_len > offset
#endif
	  ) && fd != -1 && do_ftruncate(fd, offset) < 0) {
		rsyserr(FERROR_XFER, errno, "ftruncate failed on %s",
			full_fname(fname));
	}
#endif

	if (INFO_GTE(PROGRESS, 1))
		end_progress(total_size);

	if (fd != -1 && offset > 0 && sparse_end(fd, offset) != 0) {
	    report_write_error:
		rsyserr(FERROR_XFER, errno, "write failed on %s",
			full_fname(fname));
		exit_cleanup(RERR_FILEIO);
	}

	if (sum_end(file_sum1) != checksum_len)
		overflow_exit("checksum_len"); /* Impossible... */

	if (mapbuf)
		unmap_file(mapbuf);

	read_buf(f_in, sender_file_sum, checksum_len);
	if (DEBUG_GTE(DELTASUM, 2))
		rprintf(FINFO,"got file_sum\n");
	if (fd != -1 && memcmp(file_sum1, sender_file_sum, checksum_len) != 0)
		return 0;
	return 1;
}


static void discard_receive_data(int f_in, OFF_T length)
{
	receive_data(f_in, NULL, -1, 0, NULL, -1, length);
}

static void handle_delayed_updates(char *local_name)
{
	char *fname, *partialptr;
	int ndx;

	for (ndx = -1; (ndx = bitbag_next_bit(delayed_bits, ndx)) >= 0; ) {
		struct file_struct *file = cur_flist->files[ndx];
		fname = local_name ? local_name : f_name(file, NULL);
		if ((partialptr = partial_dir_fname(fname)) != NULL) {
			if (make_backups > 0 && !make_backup(fname, False))
				continue;
			if (DEBUG_GTE(RECV, 1)) {
				rprintf(FINFO, "renaming %s to %s\n",
					partialptr, fname);
			}
			/* We don't use robust_rename() here because the
			 * partial-dir must be on the same drive. */
			if (do_rename(partialptr, fname) < 0) {
				rsyserr(FERROR_XFER, errno,
					"rename failed for %s (from %s)",
					full_fname(fname), partialptr);
			} else {
				if (remove_source_files
				 || (preserve_hard_links && F_IS_HLINKED(file)))
					send_msg_int(MSG_SUCCESS, ndx);
				handle_partial_dir(partialptr, PDIR_DELETE);
			}
		}
	}
}

static void no_batched_update(int ndx, BOOL is_redo)
{
	struct file_list *flist = flist_for_ndx(ndx, "no_batched_update");
	struct file_struct *file = flist->files[ndx - flist->ndx_start];

	rprintf(FERROR_XFER, "(No batched update for%s \"%s\")\n",
		is_redo ? " resend of" : "", f_name(file, NULL));

	if (inc_recurse && !dry_run)
		send_msg_int(MSG_NO_SEND, ndx);
}

static int we_want_redo(int desired_ndx)
{
	static int redo_ndx = -1;

	while (redo_ndx < desired_ndx) {
		if (redo_ndx >= 0)
			no_batched_update(redo_ndx, True);
		if ((redo_ndx = flist_ndx_pop(&batch_redo_list)) < 0)
			return 0;
	}

	if (redo_ndx == desired_ndx) {
		redo_ndx = -1;
		return 1;
	}

	return 0;
}

static int gen_wants_ndx(int desired_ndx, int flist_num)
{
	static int next_ndx = -1;
	static int done_cnt = 0;
	static BOOL got_eof = False;

	if (got_eof)
		return 0;

	/* TODO: integrate gen-reading I/O into perform_io() so this is not needed? */
	io_flush(FULL_FLUSH);

	while (next_ndx < desired_ndx) {
		if (inc_recurse && flist_num <= done_cnt)
			return 0;
		if (next_ndx >= 0)
			no_batched_update(next_ndx, False);
		if ((next_ndx = read_int(batch_gen_fd)) < 0) {
			if (inc_recurse) {
				done_cnt++;
				continue;
			}
			got_eof = True;
			return 0;
		}
	}

	if (next_ndx == desired_ndx) {
		next_ndx = -1;
		return 1;
	}

	return 0;
}

/**
 * main routine for receiver process.
 *
 * Receiver process runs on the same host as the generator process. */
int recv_files(int f_in, int f_out, char *local_name)
{
	int fd1,fd2;
	STRUCT_STAT st;
	int iflags, xlen;
	char *fname, fbuf[MAXPATHLEN];
	char xname[MAXPATHLEN];
	char fnametmp[MAXPATHLEN];
	char *fnamecmp, *partialptr;
	char fnamecmpbuf[MAXPATHLEN];
	uchar fnamecmp_type;
	struct file_struct *file;
	int itemizing = am_server ? logfile_format_has_i : stdout_format_has_i;
	enum logcode log_code = log_before_transfer ? FLOG : FINFO;
	int max_phase = protocol_version >= 29 ? 2 : 1;
	int dflt_perms = (ACCESSPERMS & ~orig_umask);
#ifdef SUPPORT_ACLS
	const char *parent_dirname = "";
#endif
	int ndx, recv_ok;

	if (DEBUG_GTE(RECV, 1))
		rprintf(FINFO, "recv_files(%d) starting\n", cur_flist->used);

	if (delay_updates)
		delayed_bits = bitbag_create(cur_flist->used + 1);

	while (1) {
		cleanup_disable();

		/* This call also sets cur_flist. */
		ndx = read_ndx_and_attrs(f_in, f_out, &iflags, &fnamecmp_type,
					 xname, &xlen);
		if (ndx == NDX_DONE) {
			if (!am_server && INFO_GTE(PROGRESS, 2) && cur_flist) {
				set_current_file_index(NULL, 0);
				end_progress(0);
			}
			if (inc_recurse && first_flist) {
				if (read_batch) {
					ndx = first_flist->used + first_flist->ndx_start;
					gen_wants_ndx(ndx, first_flist->flist_num);
				}
				flist_free(first_flist);
				if (first_flist)
					continue;
			} else if (read_batch && first_flist) {
				ndx = first_flist->used;
				gen_wants_ndx(ndx, first_flist->flist_num);
			}
			if (++phase > max_phase)
				break;
			if (DEBUG_GTE(RECV, 1))
				rprintf(FINFO, "recv_files phase=%d\n", phase);
			if (phase == 2 && delay_updates)
				handle_delayed_updates(local_name);
			write_int(f_out, NDX_DONE);
			continue;
		}

		if (ndx - cur_flist->ndx_start >= 0)
			file = cur_flist->files[ndx - cur_flist->ndx_start];
		else
			file = dir_flist->files[cur_flist->parent_ndx];
		fname = local_name ? local_name : f_name(file, fbuf);

		if (DEBUG_GTE(RECV, 1))
			rprintf(FINFO, "recv_files(%s)\n", fname);

#ifdef SUPPORT_XATTRS
		if (preserve_xattrs && iflags & ITEM_REPORT_XATTR && do_xfers
		 && !(want_xattr_optim && BITS_SET(iflags, ITEM_XNAME_FOLLOWS|ITEM_LOCAL_CHANGE)))
			recv_xattr_request(file, f_in);
#endif

		if (!(iflags & ITEM_TRANSFER)) {
			maybe_log_item(file, iflags, itemizing, xname);
#ifdef SUPPORT_XATTRS
			if (preserve_xattrs && iflags & ITEM_REPORT_XATTR && do_xfers
			 && !BITS_SET(iflags, ITEM_XNAME_FOLLOWS|ITEM_LOCAL_CHANGE))
				set_file_attrs(fname, file, NULL, fname, 0);
#endif
			if (iflags & ITEM_IS_NEW) {
				stats.created_files++;
				if (S_ISREG(file->mode)) {
					/* Nothing further to count. */
				} else if (S_ISDIR(file->mode))
					stats.created_dirs++;
#ifdef SUPPORT_LINKS
				else if (S_ISLNK(file->mode))
					stats.created_symlinks++;
#endif
				else if (IS_DEVICE(file->mode))
					stats.created_devices++;
				else
					stats.created_specials++;
			}
			continue;
		}
		if (phase == 2) {
			rprintf(FERROR,
				"got transfer request in phase 2 [%s]\n",
				who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}

		if (file->flags & FLAG_FILE_SENT) {
			if (csum_length == SHORT_SUM_LENGTH) {
				if (keep_partial && !partial_dir)
					make_backups = -make_backups; /* prevents double backup */
				if (append_mode)
					sparse_files = -sparse_files;
				append_mode = -append_mode;
				csum_length = SUM_LENGTH;
				redoing = 1;
			}
		} else {
			if (csum_length != SHORT_SUM_LENGTH) {
				if (keep_partial && !partial_dir)
					make_backups = -make_backups;
				if (append_mode)
					sparse_files = -sparse_files;
				append_mode = -append_mode;
				csum_length = SHORT_SUM_LENGTH;
				redoing = 0;
			}
			if (iflags & ITEM_IS_NEW)
				stats.created_files++;
		}

		if (!am_server && INFO_GTE(PROGRESS, 1))
			set_current_file_index(file, ndx);
		stats.xferred_files++;
		stats.total_transferred_size += F_LENGTH(file);

		cleanup_got_literal = 0;

		if (daemon_filter_list.head
		    && check_filter(&daemon_filter_list, FLOG, fname, 0) < 0) {
			rprintf(FERROR, "attempt to hack rsync failed.\n");
			exit_cleanup(RERR_PROTOCOL);
		}

		if (read_batch) {
			int wanted = redoing
				   ? we_want_redo(ndx)
				   : gen_wants_ndx(ndx, cur_flist->flist_num);
			if (!wanted) {
				rprintf(FINFO,
					"(Skipping batched update for%s \"%s\")\n",
					redoing ? " resend of" : "",
					fname);
				discard_receive_data(f_in, F_LENGTH(file));
				file->flags |= FLAG_FILE_SENT;
				continue;
			}
		}

		remember_initial_stats();

		if (!do_xfers) { /* log the transfer */
			log_item(FCLIENT, file, iflags, NULL);
			if (read_batch)
				discard_receive_data(f_in, F_LENGTH(file));
			continue;
		}
		if (write_batch < 0) {
			log_item(FCLIENT, file, iflags, NULL);
			if (!am_server)
				discard_receive_data(f_in, F_LENGTH(file));
			if (inc_recurse)
				send_msg_int(MSG_SUCCESS, ndx);
			continue;
		}

		partialptr = partial_dir ? partial_dir_fname(fname) : fname;

		if (protocol_version >= 29) {
			switch (fnamecmp_type) {
			case FNAMECMP_FNAME:
				fnamecmp = fname;
				break;
			case FNAMECMP_PARTIAL_DIR:
				fnamecmp = partialptr;
				break;
			case FNAMECMP_BACKUP:
				fnamecmp = get_backup_name(fname);
				break;
			case FNAMECMP_FUZZY:
				if (file->dirname) {
					pathjoin(fnamecmpbuf, sizeof fnamecmpbuf, file->dirname, xname);
					fnamecmp = fnamecmpbuf;
				} else
					fnamecmp = xname;
				break;
			default:
				if (fnamecmp_type > FNAMECMP_FUZZY && fnamecmp_type-FNAMECMP_FUZZY <= basis_dir_cnt) {
					fnamecmp_type -= FNAMECMP_FUZZY + 1;
					if (file->dirname) {
						stringjoin(fnamecmpbuf, sizeof fnamecmpbuf,
							   basis_dir[fnamecmp_type], "/", file->dirname, "/", xname, NULL);
					} else
						pathjoin(fnamecmpbuf, sizeof fnamecmpbuf, basis_dir[fnamecmp_type], xname);
				} else if (fnamecmp_type >= basis_dir_cnt) {
					rprintf(FERROR,
						"invalid basis_dir index: %d.\n",
						fnamecmp_type);
					exit_cleanup(RERR_PROTOCOL);
				} else
					pathjoin(fnamecmpbuf, sizeof fnamecmpbuf, basis_dir[fnamecmp_type], fname);
				fnamecmp = fnamecmpbuf;
				break;
			}
			if (!fnamecmp || (daemon_filter_list.head
			  && check_filter(&daemon_filter_list, FLOG, fname, 0) < 0)) {
				fnamecmp = fname;
				fnamecmp_type = FNAMECMP_FNAME;
			}
		} else {
			/* Reminder: --inplace && --partial-dir are never
			 * enabled at the same time. */
			if (inplace && make_backups > 0) {
				if (!(fnamecmp = get_backup_name(fname)))
					fnamecmp = fname;
				else
					fnamecmp_type = FNAMECMP_BACKUP;
			} else if (partial_dir && partialptr)
				fnamecmp = partialptr;
			else
				fnamecmp = fname;
		}

		/* open the file */
		fd1 = do_open(fnamecmp, O_RDONLY, 0);

		if (fd1 == -1 && protocol_version < 29) {
			if (fnamecmp != fname) {
				fnamecmp = fname;
				fd1 = do_open(fnamecmp, O_RDONLY, 0);
			}

			if (fd1 == -1 && basis_dir[0]) {
				/* pre-29 allowed only one alternate basis */
				pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
					 basis_dir[0], fname);
				fnamecmp = fnamecmpbuf;
				fd1 = do_open(fnamecmp, O_RDONLY, 0);
			}
		}

		updating_basis_or_equiv = inplace
		    && (fnamecmp == fname || fnamecmp_type == FNAMECMP_BACKUP);

		if (fd1 == -1) {
			st.st_mode = 0;
			st.st_size = 0;
		} else if (do_fstat(fd1,&st) != 0) {
			rsyserr(FERROR_XFER, errno, "fstat %s failed",
				full_fname(fnamecmp));
			discard_receive_data(f_in, F_LENGTH(file));
			close(fd1);
			if (inc_recurse)
				send_msg_int(MSG_NO_SEND, ndx);
			continue;
		}

		if (fd1 != -1 && S_ISDIR(st.st_mode) && fnamecmp == fname) {
			/* this special handling for directories
			 * wouldn't be necessary if robust_rename()
			 * and the underlying robust_unlink could cope
			 * with directories
			 */
			rprintf(FERROR_XFER, "recv_files: %s is a directory\n",
				full_fname(fnamecmp));
			discard_receive_data(f_in, F_LENGTH(file));
			close(fd1);
			if (inc_recurse)
				send_msg_int(MSG_NO_SEND, ndx);
			continue;
		}

		if (fd1 != -1 && !S_ISREG(st.st_mode)) {
			close(fd1);
			fd1 = -1;
		}

		/* If we're not preserving permissions, change the file-list's
		 * mode based on the local permissions and some heuristics. */
		if (!preserve_perms) {
			int exists = fd1 != -1;
#ifdef SUPPORT_ACLS
			const char *dn = file->dirname ? file->dirname : ".";
			if (parent_dirname != dn
			 && strcmp(parent_dirname, dn) != 0) {
				dflt_perms = default_perms_for_dir(dn);
				parent_dirname = dn;
			}
#endif
			file->mode = dest_mode(file->mode, st.st_mode,
					       dflt_perms, exists);
		}

		/* We now check to see if we are writing the file "inplace" */
		if (inplace)  {
			fd2 = do_open(fname, O_WRONLY|O_CREAT, 0600);
			if (fd2 == -1) {
				rsyserr(FERROR_XFER, errno, "open %s failed",
					full_fname(fname));
			} else if (updating_basis_or_equiv)
				cleanup_set(NULL, NULL, file, fd1, fd2);
		} else {
			fd2 = open_tmpfile(fnametmp, fname, file);
			if (fd2 != -1)
				cleanup_set(fnametmp, partialptr, file, fd1, fd2);
		}

		if (fd2 == -1) {
			discard_receive_data(f_in, F_LENGTH(file));
			if (fd1 != -1)
				close(fd1);
			if (inc_recurse)
				send_msg_int(MSG_NO_SEND, ndx);
			continue;
		}

		/* log the transfer */
		if (log_before_transfer)
			log_item(FCLIENT, file, iflags, NULL);
		else if (!am_server && INFO_GTE(NAME, 1) && INFO_EQ(PROGRESS, 1))
			rprintf(FINFO, "%s\n", fname);

		/* recv file data */
		recv_ok = receive_data(f_in, fnamecmp, fd1, st.st_size,
				       fname, fd2, F_LENGTH(file));

		log_item(log_code, file, iflags, NULL);

		if (fd1 != -1)
			close(fd1);
		if (close(fd2) < 0) {
			rsyserr(FERROR, errno, "close failed on %s",
				full_fname(fnametmp));
			exit_cleanup(RERR_FILEIO);
		}

		if ((recv_ok && (!delay_updates || !partialptr)) || inplace) {
			if (partialptr == fname)
				partialptr = NULL;
			if (!finish_transfer(fname, fnametmp, fnamecmp,
					     partialptr, file, recv_ok, 1))
				recv_ok = -1;
			else if (fnamecmp == partialptr) {
				do_unlink(partialptr);
				handle_partial_dir(partialptr, PDIR_DELETE);
			}
		} else if (keep_partial && partialptr) {
			if (!handle_partial_dir(partialptr, PDIR_CREATE)) {
				rprintf(FERROR,
				    "Unable to create partial-dir for %s -- discarding %s.\n",
				    local_name ? local_name : f_name(file, NULL),
				    recv_ok ? "completed file" : "partial file");
				do_unlink(fnametmp);
				recv_ok = -1;
			} else if (!finish_transfer(partialptr, fnametmp, fnamecmp, NULL,
						    file, recv_ok, !partial_dir))
				recv_ok = -1;
			else if (delay_updates && recv_ok) {
				bitbag_set_bit(delayed_bits, ndx);
				recv_ok = 2;
			} else
				partialptr = NULL;
		} else
			do_unlink(fnametmp);

		cleanup_disable();

		if (read_batch)
			file->flags |= FLAG_FILE_SENT;

		switch (recv_ok) {
		case 2:
			break;
		case 1:
			if (remove_source_files || inc_recurse
			 || (preserve_hard_links && F_IS_HLINKED(file)))
				send_msg_int(MSG_SUCCESS, ndx);
			break;
		case 0: {
			enum logcode msgtype = redoing ? FERROR_XFER : FWARNING;
			if (msgtype == FERROR_XFER || INFO_GTE(NAME, 1)) {
				char *errstr, *redostr, *keptstr;
				if (!(keep_partial && partialptr) && !inplace)
					keptstr = "discarded";
				else if (partial_dir)
					keptstr = "put into partial-dir";
				else
					keptstr = "retained";
				if (msgtype == FERROR_XFER) {
					errstr = "ERROR";
					redostr = "";
				} else {
					errstr = "WARNING";
					redostr = read_batch ? " (may try again)"
							     : " (will try again)";
				}
				rprintf(msgtype,
					"%s: %s failed verification -- update %s%s.\n",
					errstr, local_name ? f_name(file, NULL) : fname,
					keptstr, redostr);
			}
			if (!redoing) {
				if (read_batch)
					flist_ndx_push(&batch_redo_list, ndx);
				send_msg_int(MSG_REDO, ndx);
				file->flags |= FLAG_FILE_SENT;
			} else if (inc_recurse)
				send_msg_int(MSG_NO_SEND, ndx);
			break;
		    }
		case -1:
			if (inc_recurse)
				send_msg_int(MSG_NO_SEND, ndx);
			break;
		}
	}
	if (make_backups < 0)
		make_backups = -make_backups;

	if (phase == 2 && delay_updates) /* for protocol_version < 29 */
		handle_delayed_updates(local_name);

	if (DEBUG_GTE(RECV, 1))
		rprintf(FINFO,"recv_files finished\n");

	return 0;
}
