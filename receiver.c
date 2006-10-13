/*
 * Routines only used by the receiving process.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003, 2004, 2005, 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#include "rsync.h"

extern int verbose;
extern int do_xfers;
extern int am_server;
extern int do_progress;
extern int log_before_transfer;
extern int stdout_format_has_i;
extern int logfile_format_has_i;
extern int csum_length;
extern int read_batch;
extern int write_batch;
extern int batch_gen_fd;
extern int protocol_version;
extern int relative_paths;
extern int preserve_hard_links;
extern int preserve_perms;
extern int basis_dir_cnt;
extern int make_backups;
extern int cleanup_got_literal;
extern int remove_source_files;
extern int append_mode;
extern int sparse_files;
extern int keep_partial;
extern int checksum_seed;
extern int inplace;
extern int delay_updates;
extern struct stats stats;
extern char *stdout_format;
extern char *tmpdir;
extern char *partial_dir;
extern char *basis_dir[];
extern struct file_list *the_file_list;
extern struct filter_list_struct server_filter_list;

static struct bitbag *delayed_bits = NULL;
static int phase = 0;
/* We're either updating the basis file or an identical copy: */
static int updating_basis;


/*
 * get_tmpname() - create a tmp filename for a given filename
 *
 *   If a tmpdir is defined, use that as the directory to
 *   put it in.  Otherwise, the tmp filename is in the same
 *   directory as the given name.  Note that there may be no
 *   directory at all in the given name!
 *
 *   The tmp filename is basically the given filename with a
 *   dot prepended, and .XXXXXX appended (for mkstemp() to
 *   put its unique gunk in).  Take care to not exceed
 *   either the MAXPATHLEN or NAME_MAX, esp. the last, as
 *   the basename basically becomes 8 chars longer. In that
 *   case, the original name is shortened sufficiently to
 *   make it all fit.
 *
 *   Of course, there's no real reason for the tmp name to
 *   look like the original, except to satisfy us humans.
 *   As long as it's unique, rsync will work.
 */

static int get_tmpname(char *fnametmp, char *fname)
{
	int maxname, added, length = 0;
	char *f;

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
	fnametmp[length++] = '.';

	/* The maxname value is bufsize, and includes space for the '\0'.
	 * (Note that NAME_MAX get -8 for the leading '.' above.) */
	maxname = MIN(MAXPATHLEN - 7 - length, NAME_MAX - 8);

	if (maxname < 1) {
		rprintf(FERROR, "temporary filename too long: %s\n", fname);
		fnametmp[0] = '\0';
		return 0;
	}

	added = strlcpy(fnametmp + length, f, maxname);
	if (added >= maxname)
		added = maxname - 1;
	memcpy(fnametmp + length + added, ".XXXXXX", 8);

	return 1;
}


static int receive_data(int f_in, char *fname_r, int fd_r, OFF_T size_r,
			char *fname, int fd, OFF_T total_size)
{
	static char file_sum1[MD4_SUM_LENGTH];
	static char file_sum2[MD4_SUM_LENGTH];
	struct map_struct *mapbuf;
	struct sum_struct sum;
	int32 len;
	OFF_T offset = 0;
	OFF_T offset2;
	char *data;
	int32 i;
	char *map = NULL;

	read_sum_head(f_in, &sum);

	if (fd_r >= 0 && size_r > 0) {
		int32 read_size = MAX(sum.blength * 2, 16*1024);
		mapbuf = map_file(fd_r, size_r, read_size, sum.blength);
		if (verbose > 2) {
			rprintf(FINFO, "recv mapped %s of size %.0f\n",
				fname_r, (double)size_r);
		}
	} else
		mapbuf = NULL;

	sum_init(checksum_seed);

	if (append_mode) {
		OFF_T j;
		sum.flength = (OFF_T)sum.count * sum.blength;
		if (sum.remainder)
			sum.flength -= sum.blength - sum.remainder;
		for (j = CHUNK_SIZE; j < sum.flength; j += CHUNK_SIZE) {
			if (do_progress)
				show_progress(offset, total_size);
			sum_update(map_ptr(mapbuf, offset, CHUNK_SIZE),
				   CHUNK_SIZE);
			offset = j;
		}
		if (offset < sum.flength) {
			int32 len = sum.flength - offset;
			if (do_progress)
				show_progress(offset, total_size);
			sum_update(map_ptr(mapbuf, offset, len), len);
			offset = sum.flength;
		}
		if (fd != -1 && (j = do_lseek(fd, offset, SEEK_SET)) != offset) {
			rsyserr(FERROR, errno, "lseek of %s returned %.0f, not %.0f",
				full_fname(fname), (double)j, (double)offset);
			exit_cleanup(RERR_FILEIO);
		}
	}

	while ((i = recv_token(f_in, &data)) != 0) {
		if (do_progress)
			show_progress(offset, total_size);

		if (i > 0) {
			if (verbose > 3) {
				rprintf(FINFO,"data recv %d at %.0f\n",
					i,(double)offset);
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

		if (verbose > 3) {
			rprintf(FINFO,
				"chunk[%d] of size %ld at %.0f offset=%.0f\n",
				i, (long)len, (double)offset2, (double)offset);
		}

		if (mapbuf) {
			map = map_ptr(mapbuf,offset2,len);

			see_token(map, len);
			sum_update(map, len);
		}

		if (updating_basis) {
			if (offset == offset2 && fd != -1) {
				OFF_T pos;
				if (flush_write_file(fd) < 0)
					goto report_write_error;
				offset += len;
				if ((pos = do_lseek(fd, len, SEEK_CUR)) != offset) {
					rsyserr(FERROR, errno,
						"lseek of %s returned %.0f, not %.0f",
						full_fname(fname),
						(double)pos, (double)offset);
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
	if (inplace && fd != -1)
		ftruncate(fd, offset);
#endif

	if (do_progress)
		end_progress(total_size);

	if (fd != -1 && offset > 0 && sparse_end(fd) != 0) {
	    report_write_error:
		rsyserr(FERROR, errno, "write failed on %s",
			full_fname(fname));
		exit_cleanup(RERR_FILEIO);
	}

	sum_end(file_sum1);

	if (mapbuf)
		unmap_file(mapbuf);

	read_buf(f_in,file_sum2,MD4_SUM_LENGTH);
	if (verbose > 2)
		rprintf(FINFO,"got file_sum\n");
	if (fd != -1 && memcmp(file_sum1, file_sum2, MD4_SUM_LENGTH) != 0)
		return 0;
	return 1;
}


static void discard_receive_data(int f_in, OFF_T length)
{
	receive_data(f_in, NULL, -1, 0, NULL, -1, length);
}

static void handle_delayed_updates(struct file_list *flist, char *local_name)
{
	char *fname, *partialptr, numbuf[4];
	int i;

	for (i = -1; (i = bitbag_next_bit(delayed_bits, i)) >= 0; ) {
		struct file_struct *file = flist->files[i];
		fname = local_name ? local_name : f_name(file, NULL);
		if ((partialptr = partial_dir_fname(fname)) != NULL) {
			if (make_backups && !make_backup(fname))
				continue;
			if (verbose > 2) {
				rprintf(FINFO, "renaming %s to %s\n",
					partialptr, fname);
			}
			/* We don't use robust_rename() here because the
			 * partial-dir must be on the same drive. */
			if (do_rename(partialptr, fname) < 0) {
				rsyserr(FERROR, errno,
					"rename failed for %s (from %s)",
					full_fname(fname), partialptr);
			} else {
				if (remove_source_files
				    || (preserve_hard_links
				     && file->link_u.links)) {
					SIVAL(numbuf, 0, i);
					send_msg(MSG_SUCCESS,numbuf,4);
				}
				handle_partial_dir(partialptr, PDIR_DELETE);
			}
		}
	}
}

static int get_next_gen_i(int batch_gen_fd, int next_gen_i, int desired_i)
{
	while (next_gen_i < desired_i) {
		if (next_gen_i >= 0) {
			rprintf(FINFO,
				"(No batched update for%s \"%s\")\n",
				phase ? " resend of" : "",
				f_name(the_file_list->files[next_gen_i], NULL));
		}
		next_gen_i = read_int(batch_gen_fd);
		if (next_gen_i == -1)
			next_gen_i = the_file_list->count;
	}
	return next_gen_i;
}


/**
 * main routine for receiver process.
 *
 * Receiver process runs on the same host as the generator process. */
int recv_files(int f_in, struct file_list *flist, char *local_name)
{
	int next_gen_i = -1;
	int fd1,fd2;
	STRUCT_STAT st;
	int iflags, xlen;
	char *fname, fbuf[MAXPATHLEN];
	char xname[MAXPATHLEN];
	char fnametmp[MAXPATHLEN];
	char *fnamecmp, *partialptr, numbuf[4];
	char fnamecmpbuf[MAXPATHLEN];
	uchar fnamecmp_type;
	struct file_struct *file;
	struct stats initial_stats;
	int save_make_backups = make_backups;
	int itemizing = am_server ? logfile_format_has_i : stdout_format_has_i;
	enum logcode log_code = log_before_transfer ? FLOG : FINFO;
	int max_phase = protocol_version >= 29 ? 2 : 1;
	int i, recv_ok;

	if (verbose > 2)
		rprintf(FINFO,"recv_files(%d) starting\n",flist->count);

	if (flist->hlink_pool) {
		pool_destroy(flist->hlink_pool);
		flist->hlink_pool = NULL;
	}

	if (delay_updates)
		delayed_bits = bitbag_create(flist->count);

	updating_basis = inplace;

	while (1) {
		cleanup_disable();

		i = read_int(f_in);
		if (i == -1) {
			if (read_batch) {
				get_next_gen_i(batch_gen_fd, next_gen_i,
					       flist->count);
				next_gen_i = -1;
			}
			if (++phase > max_phase)
				break;
			csum_length = SUM_LENGTH;
			if (verbose > 2)
				rprintf(FINFO, "recv_files phase=%d\n", phase);
			if (phase == 2 && delay_updates)
				handle_delayed_updates(flist, local_name);
			send_msg(MSG_DONE, "", 0);
			if (keep_partial && !partial_dir)
				make_backups = 0; /* prevents double backup */
			if (append_mode) {
				append_mode = 0;
				sparse_files = 0;
			}
			continue;
		}

		iflags = read_item_attrs(f_in, -1, i, &fnamecmp_type,
					 xname, &xlen);
		if (iflags == ITEM_IS_NEW) /* no-op packet */
			continue;

		file = flist->files[i];
		fname = local_name ? local_name : f_name(file, fbuf);

		if (verbose > 2)
			rprintf(FINFO, "recv_files(%s)\n", fname);

		if (!(iflags & ITEM_TRANSFER)) {
			maybe_log_item(file, iflags, itemizing, xname);
			continue;
		}
		if (phase == 2) {
			rprintf(FERROR,
				"got transfer request in phase 2 [%s]\n",
				who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}

		stats.current_file_index = i;
		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;
		cleanup_got_literal = 0;

		if (server_filter_list.head
		    && check_filter(&server_filter_list, fname, 0) < 0) {
			rprintf(FERROR, "attempt to hack rsync failed.\n");
			exit_cleanup(RERR_PROTOCOL);
		}

		if (!do_xfers) { /* log the transfer */
			log_item(FCLIENT, file, &stats, iflags, NULL);
			if (read_batch)
				discard_receive_data(f_in, file->length);
			continue;
		}
		if (write_batch < 0) {
			log_item(FINFO, file, &stats, iflags, NULL);
			if (!am_server)
				discard_receive_data(f_in, file->length);
			continue;
		}

		if (read_batch) {
			next_gen_i = get_next_gen_i(batch_gen_fd, next_gen_i, i);
			if (i < next_gen_i) {
				rprintf(FINFO,
					"(Skipping batched update for \"%s\")\n",
					fname);
				discard_receive_data(f_in, file->length);
				continue;
			}
			next_gen_i = -1;
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
				updating_basis = 0;
				if (file->dirname) {
					pathjoin(fnamecmpbuf, MAXPATHLEN,
						 file->dirname, xname);
					fnamecmp = fnamecmpbuf;
				} else
					fnamecmp = xname;
				break;
			default:
				updating_basis = 0;
				if (fnamecmp_type >= basis_dir_cnt) {
					rprintf(FERROR,
						"invalid basis_dir index: %d.\n",
						fnamecmp_type);
					exit_cleanup(RERR_PROTOCOL);
				}
				pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
					 basis_dir[fnamecmp_type], fname);
				fnamecmp = fnamecmpbuf;
				break;
			}
			if (!fnamecmp || (server_filter_list.head
			  && check_filter(&server_filter_list, fname, 0) < 0))
				fnamecmp = fname;
		} else {
			/* Reminder: --inplace && --partial-dir are never
			 * enabled at the same time. */
			if (inplace && make_backups) {
				if (!(fnamecmp = get_backup_name(fname)))
					fnamecmp = fname;
			} else if (partial_dir && partialptr)
				fnamecmp = partialptr;
			else
				fnamecmp = fname;
		}

		initial_stats = stats;

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

		if (fd1 == -1) {
			st.st_mode = 0;
			st.st_size = 0;
		} else if (do_fstat(fd1,&st) != 0) {
			rsyserr(FERROR, errno, "fstat %s failed",
				full_fname(fnamecmp));
			discard_receive_data(f_in, file->length);
			close(fd1);
			continue;
		}

		if (fd1 != -1 && S_ISDIR(st.st_mode) && fnamecmp == fname) {
			/* this special handling for directories
			 * wouldn't be necessary if robust_rename()
			 * and the underlying robust_unlink could cope
			 * with directories
			 */
			rprintf(FERROR,"recv_files: %s is a directory\n",
				full_fname(fnamecmp));
			discard_receive_data(f_in, file->length);
			close(fd1);
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
			file->mode = dest_mode(file->mode, st.st_mode, exists);
		}

		/* We now check to see if we are writing the file "inplace" */
		if (inplace)  {
			fd2 = do_open(fname, O_WRONLY|O_CREAT, 0600);
			if (fd2 == -1) {
				rsyserr(FERROR, errno, "open %s failed",
					full_fname(fname));
				discard_receive_data(f_in, file->length);
				if (fd1 != -1)
					close(fd1);
				continue;
			}
		} else {
			if (!get_tmpname(fnametmp,fname)) {
				discard_receive_data(f_in, file->length);
				if (fd1 != -1)
					close(fd1);
				continue;
			}

			/* we initially set the perms without the
			 * setuid/setgid bits to ensure that there is no race
			 * condition. They are then correctly updated after
			 * the lchown. Thanks to snabb@epipe.fi for pointing
			 * this out.  We also set it initially without group
			 * access because of a similar race condition. */
			fd2 = do_mkstemp(fnametmp, file->mode & INITACCESSPERMS);

			/* in most cases parent directories will already exist
			 * because their information should have been previously
			 * transferred, but that may not be the case with -R */
			if (fd2 == -1 && relative_paths && errno == ENOENT
			    && create_directory_path(fnametmp) == 0) {
				/* Get back to name with XXXXXX in it. */
				get_tmpname(fnametmp, fname);
				fd2 = do_mkstemp(fnametmp, file->mode & INITACCESSPERMS);
			}
			if (fd2 == -1) {
				rsyserr(FERROR, errno, "mkstemp %s failed",
					full_fname(fnametmp));
				discard_receive_data(f_in, file->length);
				if (fd1 != -1)
					close(fd1);
				continue;
			}

			cleanup_set(fnametmp, partialptr, file, fd1, fd2);
		}

		/* log the transfer */
		if (log_before_transfer)
			log_item(FCLIENT, file, &initial_stats, iflags, NULL);
		else if (!am_server && verbose && do_progress)
			rprintf(FINFO, "%s\n", fname);

		/* recv file data */
		recv_ok = receive_data(f_in, fnamecmp, fd1, st.st_size,
				       fname, fd2, file->length);

		log_item(log_code, file, &initial_stats, iflags, NULL);

		if (fd1 != -1)
			close(fd1);
		if (close(fd2) < 0) {
			rsyserr(FERROR, errno, "close failed on %s",
				full_fname(fnametmp));
			exit_cleanup(RERR_FILEIO);
		}

		if ((recv_ok && (!delay_updates || !partialptr)) || inplace) {
			char *temp_copy_name;
			if (partialptr == fname)
				partialptr = temp_copy_name = NULL;
			else if (*partial_dir == '/')
				temp_copy_name = NULL;
			else
				temp_copy_name = partialptr;
			finish_transfer(fname, fnametmp, temp_copy_name,
					file, recv_ok, 1);
			if (fnamecmp == partialptr) {
				do_unlink(partialptr);
				handle_partial_dir(partialptr, PDIR_DELETE);
			}
		} else if (keep_partial && partialptr
		    && handle_partial_dir(partialptr, PDIR_CREATE)) {
			finish_transfer(partialptr, fnametmp, NULL,
					file, recv_ok, !partial_dir);
			if (delay_updates && recv_ok) {
				bitbag_set_bit(delayed_bits, i);
				recv_ok = -1;
			}
		} else {
			partialptr = NULL;
			do_unlink(fnametmp);
		}

		cleanup_disable();

		if (recv_ok > 0) {
			if (remove_source_files
			    || (preserve_hard_links && file->link_u.links)) {
				SIVAL(numbuf, 0, i);
				send_msg(MSG_SUCCESS, numbuf, 4);
			}
		} else if (!recv_ok) {
			int msgtype = phase || read_batch ? FERROR : FINFO;
			if (msgtype == FERROR || verbose) {
				char *errstr, *redostr, *keptstr;
				if (!(keep_partial && partialptr) && !inplace)
					keptstr = "discarded";
				else if (partial_dir)
					keptstr = "put into partial-dir";
				else
					keptstr = "retained";
				if (msgtype == FERROR) {
					errstr = "ERROR";
					redostr = "";
				} else {
					errstr = "WARNING";
					redostr = " (will try again)";
				}
				rprintf(msgtype,
					"%s: %s failed verification -- update %s%s.\n",
					errstr, fname, keptstr, redostr);
			}
			if (!phase) {
				SIVAL(numbuf, 0, i);
				send_msg(MSG_REDO, numbuf, 4);
			}
		}
	}
	make_backups = save_make_backups;

	if (phase == 2 && delay_updates) /* for protocol_version < 29 */
		handle_delayed_updates(flist, local_name);

	if (verbose > 2)
		rprintf(FINFO,"recv_files finished\n");

	return 0;
}
