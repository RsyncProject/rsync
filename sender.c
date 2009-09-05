/*
 * Routines only used by the sending process.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003-2009 Wayne Davison
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

extern int verbose;
extern int do_xfers;
extern int am_server;
extern int am_daemon;
extern int inc_recurse;
extern int log_before_transfer;
extern int stdout_format_has_i;
extern int logfile_format_has_i;
extern int csum_length;
extern int append_mode;
extern int io_error;
extern int allowed_lull;
extern int preserve_xattrs;
extern int protocol_version;
extern int remove_source_files;
extern int updating_basis_file;
extern int make_backups;
extern int do_progress;
extern int inplace;
extern int batch_fd;
extern int write_batch;
extern struct stats stats;
extern struct file_list *cur_flist, *first_flist, *dir_flist;

/**
 * @file
 *
 * The sender gets checksums from the generator, calculates deltas,
 * and transmits them to the receiver.  The sender process runs on the
 * machine holding the source files.
 **/

/**
 * Receive the checksums for a buffer
 **/
static struct sum_struct *receive_sums(int f)
{
	struct sum_struct *s;
	int32 i;
	int lull_mod = allowed_lull * 5;
	OFF_T offset = 0;

	if (!(s = new(struct sum_struct)))
		out_of_memory("receive_sums");

	read_sum_head(f, s);

	s->sums = NULL;

	if (verbose > 3) {
		rprintf(FINFO, "count=%.0f n=%ld rem=%ld\n",
			(double)s->count, (long)s->blength, (long)s->remainder);
	}

	if (append_mode > 0) {
		s->flength = (OFF_T)s->count * s->blength;
		if (s->remainder)
			s->flength -= s->blength - s->remainder;
		return s;
	}

	if (s->count == 0)
		return(s);

	if (!(s->sums = new_array(struct sum_buf, s->count)))
		out_of_memory("receive_sums");

	for (i = 0; i < s->count; i++) {
		s->sums[i].sum1 = read_int(f);
		read_buf(f, s->sums[i].sum2, s->s2length);

		s->sums[i].offset = offset;
		s->sums[i].flags = 0;

		if (i == s->count-1 && s->remainder != 0)
			s->sums[i].len = s->remainder;
		else
			s->sums[i].len = s->blength;
		offset += s->sums[i].len;

		if (allowed_lull && !(i % lull_mod))
			maybe_send_keepalive();

		if (verbose > 3) {
			rprintf(FINFO,
				"chunk[%d] len=%d offset=%.0f sum1=%08x\n",
				i, s->sums[i].len, (double)s->sums[i].offset,
				s->sums[i].sum1);
		}
	}

	s->flength = offset;

	return s;
}

void successful_send(int ndx)
{
	char fname[MAXPATHLEN];
	struct file_struct *file;
	struct file_list *flist;

	if (!remove_source_files)
		return;

	flist = flist_for_ndx(ndx, "successful_send");
	file = flist->files[ndx - flist->ndx_start];
	if (!change_pathname(file, NULL, 0))
		return;
	f_name(file, fname);

	if (do_unlink(fname) == 0) {
		if (verbose > 1)
			rprintf(FINFO, "sender removed %s\n", fname);
	} else
		rsyserr(FERROR, errno, "sender failed to remove %s", fname);
}

static void write_ndx_and_attrs(int f_out, int ndx, int iflags,
				const char *fname, struct file_struct *file,
				uchar fnamecmp_type, char *buf, int len)
{
	write_ndx(f_out, ndx);
	if (protocol_version < 29)
		return;
	write_shortint(f_out, iflags);
	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		write_byte(f_out, fnamecmp_type);
	if (iflags & ITEM_XNAME_FOLLOWS)
		write_vstring(f_out, buf, len);
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs && iflags & ITEM_REPORT_XATTR && do_xfers)
		send_xattr_request(fname, file, f_out);
#endif
}

void send_files(int f_in, int f_out)
{
	int fd = -1;
	struct sum_struct *s;
	struct map_struct *mbuf = NULL;
	STRUCT_STAT st;
	char fname[MAXPATHLEN], xname[MAXPATHLEN];
	const char *path, *slash;
	uchar fnamecmp_type;
	int iflags, xlen;
	struct file_struct *file;
	int phase = 0, max_phase = protocol_version >= 29 ? 2 : 1;
	struct stats initial_stats;
	int itemizing = am_server ? logfile_format_has_i : stdout_format_has_i;
	enum logcode log_code = log_before_transfer ? FLOG : FINFO;
	int f_xfer = write_batch < 0 ? batch_fd : f_out;
	int save_io_error = io_error;
	int ndx, j;

	if (verbose > 2)
		rprintf(FINFO, "send_files starting\n");

	while (1) {
		if (inc_recurse)
			send_extra_file_list(f_out, FILECNT_LOOKAHEAD);

		/* This call also sets cur_flist. */
		ndx = read_ndx_and_attrs(f_in, &iflags, &fnamecmp_type,
					 xname, &xlen);
		if (ndx == NDX_DONE) {
			if (inc_recurse && first_flist) {
				flist_free(first_flist);
				if (first_flist) {
					write_ndx(f_out, NDX_DONE);
					continue;
				}
			}
			if (++phase > max_phase)
				break;
			if (verbose > 2)
				rprintf(FINFO, "send_files phase=%d\n", phase);
			write_ndx(f_out, NDX_DONE);
			continue;
		}

		if (inc_recurse)
			send_extra_file_list(f_out, FILECNT_LOOKAHEAD);

		if (ndx - cur_flist->ndx_start >= 0)
			file = cur_flist->files[ndx - cur_flist->ndx_start];
		else
			file = dir_flist->files[cur_flist->parent_ndx];
		if (F_PATHNAME(file)) {
			path = F_PATHNAME(file);
			slash = "/";
		} else {
			path = slash = "";
		}
		if (!change_pathname(file, NULL, 0))
			continue;
		f_name(file, fname);

		if (verbose > 2)
			rprintf(FINFO, "send_files(%d, %s%s%s)\n", ndx, path,slash,fname);

#ifdef SUPPORT_XATTRS
		if (preserve_xattrs && iflags & ITEM_REPORT_XATTR && do_xfers)
			recv_xattr_request(file, f_in);
#endif

		if (!(iflags & ITEM_TRANSFER)) {
			maybe_log_item(file, iflags, itemizing, xname);
			write_ndx_and_attrs(f_out, ndx, iflags, fname, file,
					    fnamecmp_type, xname, xlen);
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
				/* For inplace: redo phase turns off the backup
				 * flag so that we do a regular inplace send. */
				make_backups = -make_backups;
				append_mode = -append_mode;
				csum_length = SUM_LENGTH;
			}
		} else {
			if (csum_length != SHORT_SUM_LENGTH) {
				make_backups = -make_backups;
				append_mode = -append_mode;
				csum_length = SHORT_SUM_LENGTH;
			}
		}

		updating_basis_file = inplace && (protocol_version >= 29
			? fnamecmp_type == FNAMECMP_FNAME : make_backups <= 0);

		if (!am_server && do_progress)
			set_current_file_index(file, ndx);
		stats.num_transferred_files++;
		stats.total_transferred_size += F_LENGTH(file);

		if (!do_xfers) { /* log the transfer */
			log_item(FCLIENT, file, &stats, iflags, NULL);
			write_ndx_and_attrs(f_out, ndx, iflags, fname, file,
					    fnamecmp_type, xname, xlen);
			continue;
		}

		initial_stats = stats;

		if (!(s = receive_sums(f_in))) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR_XFER, "receive_sums failed\n");
			exit_cleanup(RERR_PROTOCOL);
		}

		fd = do_open(fname, O_RDONLY, 0);
		if (fd == -1) {
			if (errno == ENOENT) {
				enum logcode c = am_daemon
				    && protocol_version < 28 ? FERROR
							     : FWARNING;
				io_error |= IOERR_VANISHED;
				rprintf(c, "file has vanished: %s\n",
					full_fname(fname));
			} else {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR_XFER, errno,
					"send_files failed to open %s",
					full_fname(fname));
			}
			free_sums(s);
			if (protocol_version >= 30)
				send_msg_int(MSG_NO_SEND, ndx);
			continue;
		}

		/* map the local file */
		if (do_fstat(fd, &st) != 0) {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR_XFER, errno, "fstat failed");
			free_sums(s);
			close(fd);
			exit_cleanup(RERR_PROTOCOL);
		}

		if (st.st_size) {
			int32 read_size = MAX(s->blength * 3, MAX_MAP_SIZE);
			mbuf = map_file(fd, st.st_size, read_size, s->blength);
		} else
			mbuf = NULL;

		if (verbose > 2) {
			rprintf(FINFO, "send_files mapped %s%s%s of size %.0f\n",
				path,slash,fname, (double)st.st_size);
		}

		write_ndx_and_attrs(f_out, ndx, iflags, fname, file,
				    fnamecmp_type, xname, xlen);
		write_sum_head(f_xfer, s);

		if (verbose > 2)
			rprintf(FINFO, "calling match_sums %s%s%s\n", path,slash,fname);

		if (log_before_transfer)
			log_item(FCLIENT, file, &initial_stats, iflags, NULL);
		else if (!am_server && verbose && do_progress)
			rprintf(FCLIENT, "%s\n", fname);

		set_compression(fname);

		match_sums(f_xfer, s, mbuf, st.st_size);
		if (do_progress)
			end_progress(st.st_size);

		log_item(log_code, file, &initial_stats, iflags, NULL);

		if (mbuf) {
			j = unmap_file(mbuf);
			if (j) {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR_XFER, j,
					"read errors mapping %s",
					full_fname(fname));
			}
		}
		close(fd);

		free_sums(s);

		if (verbose > 2)
			rprintf(FINFO, "sender finished %s%s%s\n", path,slash,fname);

		/* Flag that we actually sent this entry. */
		file->flags |= FLAG_FILE_SENT;
	}
	if (make_backups < 0)
		make_backups = -make_backups;

	if (io_error != save_io_error && protocol_version >= 30)
		send_msg_int(MSG_IO_ERROR, io_error);

	if (verbose > 2)
		rprintf(FINFO, "send files finished\n");

	match_report();

	write_ndx(f_out, NDX_DONE);
}
