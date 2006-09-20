/*
 * Routines only used by the sending process.
 *
 * Copyright (C) 1996 Andrew Tridgell
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
extern int am_daemon;
extern int log_before_transfer;
extern int stdout_format_has_i;
extern int logfile_format_has_i;
extern int csum_length;
extern int append_mode;
extern int io_error;
extern int allowed_lull;
extern int protocol_version;
extern int remove_source_files;
extern int updating_basis_file;
extern int make_backups;
extern int do_progress;
extern int inplace;
extern int batch_fd;
extern int write_batch;
extern struct stats stats;
extern struct file_list *the_file_list;
extern char *stdout_format;


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

	if (append_mode) {
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
	unsigned int offset;

	if (ndx < 0 || ndx >= the_file_list->count)
		return;

	file = the_file_list->files[ndx];
	if (file->dir.root) {
		offset = stringjoin(fname, sizeof fname,
				    file->dir.root, "/", NULL);
	} else
		offset = 0;
	f_name(file, fname + offset);
	if (remove_source_files) {
		if (do_unlink(fname) == 0) {
			if (verbose > 1)
				rprintf(FINFO, "sender removed %s\n", fname + offset);
		} else
			rsyserr(FERROR, errno, "sender failed to remove %s", fname + offset);
	}
}

static void write_ndx_and_attrs(int f_out, int ndx, int iflags,
				uchar fnamecmp_type, char *buf, int len)
{
	write_int(f_out, ndx);
	if (protocol_version < 29)
		return;
	write_shortint(f_out, iflags);
	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		write_byte(f_out, fnamecmp_type);
	if (iflags & ITEM_XNAME_FOLLOWS)
		write_vstring(f_out, buf, len);
}

/* This is also used by receive.c with f_out = -1. */
int read_item_attrs(int f_in, int f_out, int ndx, uchar *type_ptr,
		    char *buf, int *len_ptr)
{
	int len;
	uchar fnamecmp_type = FNAMECMP_FNAME;
	int iflags = protocol_version >= 29 ? read_shortint(f_in)
		   : ITEM_TRANSFER | ITEM_MISSING_DATA;

	/* Handle the new keep-alive (no-op) packet. */
	if (ndx == the_file_list->count && iflags == ITEM_IS_NEW)
		;
	else if (ndx < 0 || ndx >= the_file_list->count) {
		rprintf(FERROR, "Invalid file index: %d (count=%d) [%s]\n",
			ndx, the_file_list->count, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	} else if (iflags == ITEM_IS_NEW) {
		rprintf(FERROR, "Invalid itemized flag word: %x [%s]\n",
			iflags, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}

	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		fnamecmp_type = read_byte(f_in);
	*type_ptr = fnamecmp_type;

	if (iflags & ITEM_XNAME_FOLLOWS) {
		if ((len = read_vstring(f_in, buf, MAXPATHLEN)) < 0)
			exit_cleanup(RERR_PROTOCOL);
	} else {
		*buf = '\0';
		len = -1;
	}
	*len_ptr = len;

	if (iflags & ITEM_TRANSFER) {
		if (!S_ISREG(the_file_list->files[ndx]->mode)) {
			rprintf(FERROR,
				"received request to transfer non-regular file: %d [%s]\n",
				ndx, who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}
	} else if (f_out >= 0) {
		write_ndx_and_attrs(f_out, ndx, iflags,
				    fnamecmp_type, buf, len);
	}

	return iflags;
}

void send_files(struct file_list *flist, int f_out, int f_in)
{
	int fd = -1;
	struct sum_struct *s;
	struct map_struct *mbuf = NULL;
	STRUCT_STAT st;
	char *fname2, fname[MAXPATHLEN];
	char xname[MAXPATHLEN];
	uchar fnamecmp_type;
	int iflags, xlen;
	struct file_struct *file;
	int phase = 0, max_phase = protocol_version >= 29 ? 2 : 1;
	struct stats initial_stats;
	int save_make_backups = make_backups;
	int itemizing = am_server ? logfile_format_has_i : stdout_format_has_i;
	enum logcode log_code = log_before_transfer ? FLOG : FINFO;
	int f_xfer = write_batch < 0 ? batch_fd : f_out;
	int i, j;

	if (verbose > 2)
		rprintf(FINFO, "send_files starting\n");

	while (1) {
		unsigned int offset;

		i = read_int(f_in);
		if (i == -1) {
			if (++phase > max_phase)
				break;
			csum_length = SUM_LENGTH;
			if (verbose > 2)
				rprintf(FINFO, "send_files phase=%d\n", phase);
			write_int(f_out, -1);
			/* For inplace: redo phase turns off the backup
			 * flag so that we do a regular inplace send. */
			make_backups = 0;
			append_mode = 0;
			continue;
		}

		iflags = read_item_attrs(f_in, f_out, i, &fnamecmp_type,
					 xname, &xlen);
		if (iflags == ITEM_IS_NEW) /* no-op packet */
			continue;

		file = flist->files[i];
		if (file->dir.root) {
			/* N.B. We're sure that this fits, so offset is OK. */
			offset = strlcpy(fname, file->dir.root, sizeof fname);
			if (!offset || fname[offset-1] != '/')
				fname[offset++] = '/';
		} else
			offset = 0;
		fname2 = f_name(file, fname + offset);

		if (verbose > 2)
			rprintf(FINFO, "send_files(%d, %s)\n", i, fname);

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

		updating_basis_file = inplace && (protocol_version >= 29
			? fnamecmp_type == FNAMECMP_FNAME : !make_backups);

		stats.current_file_index = i;
		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;

		if (!do_xfers) { /* log the transfer */
			log_item(FCLIENT, file, &stats, iflags, NULL);
			write_ndx_and_attrs(f_out, i, iflags, fnamecmp_type,
					    xname, xlen);
			continue;
		}

		initial_stats = stats;

		if (!(s = receive_sums(f_in))) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR, "receive_sums failed\n");
			return;
		}

		fd = do_open(fname, O_RDONLY, 0);
		if (fd == -1) {
			if (errno == ENOENT) {
				enum logcode c = am_daemon
				    && protocol_version < 28 ? FERROR
							     : FINFO;
				io_error |= IOERR_VANISHED;
				rprintf(c, "file has vanished: %s\n",
					full_fname(fname));
			} else {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR, errno,
					"send_files failed to open %s",
					full_fname(fname));
			}
			free_sums(s);
			continue;
		}

		/* map the local file */
		if (do_fstat(fd, &st) != 0) {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR, errno, "fstat failed");
			free_sums(s);
			close(fd);
			return;
		}

		if (st.st_size) {
			int32 read_size = MAX(s->blength * 3, MAX_MAP_SIZE);
			mbuf = map_file(fd, st.st_size, read_size, s->blength);
		} else
			mbuf = NULL;

		if (verbose > 2) {
			rprintf(FINFO, "send_files mapped %s of size %.0f\n",
				fname, (double)st.st_size);
		}

		write_ndx_and_attrs(f_out, i, iflags, fnamecmp_type,
				    xname, xlen);
		write_sum_head(f_xfer, s);

		if (verbose > 2)
			rprintf(FINFO, "calling match_sums %s\n", fname);

		if (log_before_transfer)
			log_item(FCLIENT, file, &initial_stats, iflags, NULL);
		else if (!am_server && verbose && do_progress)
			rprintf(FCLIENT, "%s\n", fname2);

		set_compression(fname);

		match_sums(f_xfer, s, mbuf, st.st_size);
		if (do_progress)
			end_progress(st.st_size);

		log_item(log_code, file, &initial_stats, iflags, NULL);

		if (mbuf) {
			j = unmap_file(mbuf);
			if (j) {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR, j,
					"read errors mapping %s",
					full_fname(fname));
			}
		}
		close(fd);

		free_sums(s);

		if (verbose > 2)
			rprintf(FINFO, "sender finished %s\n", fname);

		/* Flag that we actually sent this entry. */
		file->flags |= FLAG_SENT;
	}
	make_backups = save_make_backups;

	if (verbose > 2)
		rprintf(FINFO, "send files finished\n");

	match_report();

	write_int(f_out, -1);
}
