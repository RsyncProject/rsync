/* -*- c-file-style: "linux" -*-

   Copyright (C) 1996-2000 by Andrew Tridgell
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
extern int delete_after;
extern int csum_length;
extern struct stats stats;
extern int dry_run;
extern int read_batch;
extern int batch_gen_fd;
extern int am_server;
extern int protocol_version;
extern int relative_paths;
extern int keep_dirlinks;
extern int preserve_hard_links;
extern int preserve_perms;
extern int io_error;
extern char *tmpdir;
extern char *partial_dir;
extern char *basis_dir[];
extern int basis_dir_cnt;
extern int make_backups;
extern int do_progress;
extern int cleanup_got_literal;
extern int module_id;
extern int ignore_errors;
extern int orig_umask;
extern int keep_partial;
extern int checksum_seed;
extern int inplace;
extern int delay_updates;

extern struct filter_list_struct server_filter_list;


/* This deletes any files on the receiving side that are not present on the
 * sending side.  This is used by --delete-before and --delete-after. */
void delete_files(struct file_list *flist)
{
	char fbuf[MAXPATHLEN];
	int j;

	for (j = 0; j < flist->count; j++) {
		struct file_struct *file = flist->files[j];

		if (!(file->flags & FLAG_DEL_HERE))
			continue;

		f_name_to(file, fbuf);
		if (verbose > 1 && file->flags & FLAG_TOP_DIR)
			rprintf(FINFO, "deleting in %s\n", safe_fname(fbuf));

		delete_in_dir(flist, fbuf, file);
	}
}


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
	char *f;
	int     length = 0;
	int	maxname;

	if (tmpdir) {
		/* Note: this can't overflow, so the return value is safe */
		length = strlcpy(fnametmp, tmpdir, MAXPATHLEN - 2);
		fnametmp[length++] = '/';
		fnametmp[length] = '\0';	/* always NULL terminated */
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
	fnametmp[length] = '\0';		/* always NULL terminated */

	maxname = MIN(MAXPATHLEN - 7 - length, NAME_MAX - 8);

	if (maxname < 1) {
		rprintf(FERROR, "temporary filename too long: %s\n",
			safe_fname(fname));
		fnametmp[0] = '\0';
		return 0;
	}

	strlcpy(fnametmp + length, f, maxname);
	strcat(fnametmp + length, ".XXXXXX");

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
				safe_fname(fname_r), (double)size_r);
		}
	} else
		mapbuf = NULL;

	sum_init(checksum_seed);

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

		if (inplace) {
			if (offset == offset2 && fd != -1) {
				if (flush_write_file(fd) < 0)
					goto report_write_error;
				offset += len;
				if (do_lseek(fd, len, SEEK_CUR) != offset) {
					rsyserr(FERROR, errno,
						"lseek failed on %s",
						full_fname(fname));
					exit_cleanup(RERR_FILEIO);
				}
				continue;
			}
		}
		if (fd != -1 && write_file(fd, map, len) != (int)len)
			goto report_write_error;
		offset += len;
	}

	if (flush_write_file(fd) < 0)
		goto report_write_error;

#if HAVE_FTRUNCATE
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


/**
 * main routine for receiver process.
 *
 * Receiver process runs on the same host as the generator process. */
int recv_files(int f_in, struct file_list *flist, char *local_name,
	       int f_in_name)
{
	int next_gen_i = -1;
	int fd1,fd2;
	STRUCT_STAT st;
	char *fname, fbuf[MAXPATHLEN];
	char template[MAXPATHLEN];
	char fnametmp[MAXPATHLEN];
	char *fnamecmp, *partialptr;
	char fnamecmpbuf[MAXPATHLEN];
	uchar *delayed_bits = NULL;
	struct file_struct *file;
	struct stats initial_stats;
	int save_make_backups = make_backups;
	int i, recv_ok, phase = 0;

	if (verbose > 2)
		rprintf(FINFO,"recv_files(%d) starting\n",flist->count);

	if (flist->hlink_pool) {
		pool_destroy(flist->hlink_pool);
		flist->hlink_pool = NULL;
	}

	if (delay_updates) {
		int sz = (flist->count + 7) / 8;
		if (!(delayed_bits = new_array(uchar, sz)))
			out_of_memory("recv_files");
		memset(delayed_bits, 0, sz);
	}

	while (1) {
		cleanup_disable();

		i = read_int(f_in);
		if (i == -1) {
			if (read_batch) {
				if (next_gen_i != flist->count) {
					do {
						if (f_in_name >= 0
						    && next_gen_i >= 0)
							read_byte(f_in_name);
					} while (read_int(batch_gen_fd) != -1);
				}
				next_gen_i = -1;
			}

			if (phase)
				break;

			phase = 1;
			csum_length = SUM_LENGTH;
			if (verbose > 2)
				rprintf(FINFO, "recv_files phase=%d\n", phase);
			send_msg(MSG_DONE, "", 0);
			if (keep_partial && !partial_dir)
				make_backups = 0; /* prevents double backup */
			continue;
		}

		if (i < 0 || i >= flist->count) {
			rprintf(FERROR,"Invalid file index %d in recv_files (count=%d)\n",
				i, flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}

		file = flist->files[i];
		if (S_ISDIR(file->mode)) {
			rprintf(FERROR, "[%s] got index of directory: %d\n",
				who_am_i(), i);
			exit_cleanup(RERR_PROTOCOL);
		}

		stats.current_file_index = i;
		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;
		cleanup_got_literal = 0;

		fname = local_name ? local_name : f_name_to(file, fbuf);

		if (server_filter_list.head
		    && check_filter(&server_filter_list, fname, 0) < 0) {
			rprintf(FERROR, "attempt to hack rsync failed.\n");
			exit_cleanup(RERR_PROTOCOL);
		}

		if (verbose > 2)
			rprintf(FINFO, "recv_files(%s)\n", safe_fname(fname));

		if (dry_run) {
			if (!am_server && verbose) /* log the transfer */
				rprintf(FINFO, "%s\n", safe_fname(fname));
			continue;
		}

		initial_stats = stats;

		if (read_batch) {
			while (i > next_gen_i) {
				if (f_in_name >= 0 && next_gen_i >= 0)
					read_byte(f_in_name);
				next_gen_i = read_int(batch_gen_fd);
				if (next_gen_i == -1)
					next_gen_i = flist->count;
			}
			if (i < next_gen_i) {
				rprintf(FINFO, "skipping update for \"%s\"\n",
					safe_fname(fname));
				discard_receive_data(f_in, file->length);
				continue;
			}
			next_gen_i = -1;
		}

		partialptr = partial_dir ? partial_dir_fname(fname) : fname;

		if (f_in_name >= 0) {
			uchar j;
			switch (j = read_byte(f_in_name)) {
			case FNAMECMP_FNAME:
				fnamecmp = fname;
				break;
			case FNAMECMP_PARTIAL_DIR:
				fnamecmp = partialptr ? partialptr : fname;
				break;
			case FNAMECMP_BACKUP:
				fnamecmp = get_backup_name(fname);
				break;
			default:
				if (j >= basis_dir_cnt) {
					rprintf(FERROR,
						"invalid basis_dir index: %d.\n",
						j);
					exit_cleanup(RERR_PROTOCOL);
				}
				pathjoin(fnamecmpbuf, sizeof fnamecmpbuf,
					 basis_dir[j], fname);
				fnamecmp = fnamecmpbuf;
				break;
			}
		} else
			fnamecmp = fname;

		/* open the file */
		fd1 = do_open(fnamecmp, O_RDONLY, 0);

		if (fd1 != -1 && do_fstat(fd1,&st) != 0) {
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

		if (fd1 != -1 && !preserve_perms) {
			/* if the file exists already and we aren't preserving
			 * permissions then act as though the remote end sent
			 * us the file permissions we already have */
			file->mode = st.st_mode;
		}

		/* We now check to see if we are writing file "inplace" */
		if (inplace)  {
			fd2 = do_open(fname, O_WRONLY|O_CREAT, 0);
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

			strlcpy(template, fnametmp, sizeof template);

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
			    && create_directory_path(fnametmp, orig_umask) == 0) {
				strlcpy(fnametmp, template, sizeof fnametmp);
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

			if (partialptr)
				cleanup_set(fnametmp, partialptr, file, fd1, fd2);
		}

		if (!am_server && verbose) /* log the transfer */
			rprintf(FINFO, "%s\n", safe_fname(fname));

		/* recv file data */
		recv_ok = receive_data(f_in, fnamecmp, fd1, st.st_size,
				       fname, fd2, file->length);

		log_recv(file, &initial_stats);

		if (fd1 != -1)
			close(fd1);
		if (close(fd2) < 0) {
			rsyserr(FERROR, errno, "close failed on %s",
				full_fname(fnametmp));
			exit_cleanup(RERR_FILEIO);
		}

		if ((recv_ok && (!delay_updates || !partialptr)) || inplace) {
			finish_transfer(fname, fnametmp, file, recv_ok, 1);
			if (partialptr != fname && fnamecmp == partialptr) {
				do_unlink(partialptr);
				handle_partial_dir(partialptr, PDIR_DELETE);
			}
		} else if (keep_partial && partialptr
		    && handle_partial_dir(partialptr, PDIR_CREATE)) {
			finish_transfer(partialptr, fnametmp, file, recv_ok,
					!partial_dir);
			if (delay_updates && recv_ok)
				delayed_bits[i/8] |= 1 << (i % 8);
		} else {
			partialptr = NULL;
			do_unlink(fnametmp);
		}

		cleanup_disable();

		if (!recv_ok) {
			int msgtype = csum_length == SUM_LENGTH || read_batch ?
				FERROR : FINFO;
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
					errstr, safe_fname(fname),
					keptstr, redostr);
			}
			if (csum_length != SUM_LENGTH) {
				char buf[4];
				SIVAL(buf, 0, i);
				send_msg(MSG_REDO, buf, 4);
			}
		}
	}
	make_backups = save_make_backups;

	if (delay_updates) {
		for (i = 0; i < flist->count; i++) {
			struct file_struct *file = flist->files[i];
			if (!file->basename
			 || !(delayed_bits[i/8] & (1 << (i % 8))))
				continue;
			fname = local_name ? local_name : f_name(file);
			partialptr = partial_dir_fname(fname);
			if (partialptr) {
				if (make_backups && !make_backup(fname))
					continue;
				if (verbose > 2) {
					rprintf(FINFO, "renaming %s to %s\n",
						safe_fname(partialptr),
						safe_fname(fname));
				}
				if (do_rename(partialptr, fname) < 0) {
					rsyserr(FERROR, errno,
						"rename failed for %s (from %s)",
						full_fname(fname),
						safe_fname(partialptr));
				} else {
					handle_partial_dir(partialptr,
							   PDIR_DELETE);
				}
			}
		}
	}

	if (delete_after && !local_name && flist->count > 0)
		delete_files(flist);

	if (verbose > 2)
		rprintf(FINFO,"recv_files finished\n");

	return 0;
}
