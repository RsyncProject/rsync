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
extern int dry_run;
extern int log_before_transfer;
extern int log_format_has_i;
extern int daemon_log_format_has_i;
extern int am_server;
extern int am_daemon;
extern int csum_length;
extern struct stats stats;
extern int io_error;
extern int protocol_version;
extern int updating_basis_file;
extern int make_backups;
extern int do_progress;
extern int inplace;
extern char *log_format;
extern struct stats stats;


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
	OFF_T offset = 0;

	if (!(s = new(struct sum_struct)))
		out_of_memory("receive_sums");

	read_sum_head(f, s);

	s->sums = NULL;

	if (verbose > 3) {
		rprintf(FINFO, "count=%.0f n=%ld rem=%ld\n",
			(double)s->count, (long)s->blength, (long)s->remainder);
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



void send_files(struct file_list *flist, int f_out, int f_in)
{
	int fd = -1;
	struct sum_struct *s;
	struct map_struct *mbuf = NULL;
	STRUCT_STAT st;
	char *fname2, fname[MAXPATHLEN];
	int iflags;
	struct file_struct *file;
	int phase = 0;
	struct stats initial_stats;
	int save_make_backups = make_backups;
	int itemizing = am_daemon ? daemon_log_format_has_i
		      : !am_server && log_format_has_i;
	int i, j;

	if (verbose > 2)
		rprintf(FINFO, "send_files starting\n");

	while (1) {
		unsigned int offset;

		i = read_int(f_in);
		if (i == -1) {
			if (phase == 0) {
				phase++;
				csum_length = SUM_LENGTH;
				write_int(f_out, -1);
				if (verbose > 2)
					rprintf(FINFO, "send_files phase=%d\n", phase);
				/* For inplace: redo phase turns off the backup
				 * flag so that we do a regular inplace send. */
				make_backups = 0;
				continue;
			}
			break;
		}

		if (i < 0 || i >= flist->count) {
			rprintf(FERROR, "Invalid file index %d (count=%d)\n",
				i, flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}

		file = flist->files[i];
		if (file->dir.root) {
			/* N.B. We're sure that this fits, so offset is OK. */
			offset = strlcpy(fname, file->dir.root, sizeof fname);
			if (!offset || fname[offset-1] != '/')
				fname[offset++] = '/';
		} else
			offset = 0;
		fname2 = f_name_to(file, fname + offset);

		if (verbose > 2)
			rprintf(FINFO, "send_files(%d, %s)\n", i, fname);

		if (protocol_version >= 29) {
			iflags = read_shortint(f_in);
			if (!(iflags & ITEM_UPDATING) || !S_ISREG(file->mode)) {
				int see_item = itemizing && (iflags || verbose > 1);
				write_int(f_out, i);
				write_shortint(f_out, iflags);
				if (am_server) {
					if (am_daemon && !dry_run && see_item)
						log_recv(file, &stats, iflags);
				} else if (see_item || iflags & ITEM_UPDATING
				    || (S_ISDIR(file->mode)
				     && iflags & ITEM_REPORT_TIME))
					log_recv(file, &stats, iflags);
				continue;
			}
		} else
			iflags = ITEM_UPDATING | ITEM_MISSING_DATA;

		if (inplace && protocol_version >= 29) {
			uchar fnamecmp_type = read_byte(f_in);
			updating_basis_file = fnamecmp_type == FNAMECMP_FNAME;
		} else
			updating_basis_file = inplace && !make_backups;

		if (!S_ISREG(file->mode)) {
			rprintf(FERROR, "[%s] got index of non-regular file: %d\n",
				who_am_i(), i);
			exit_cleanup(RERR_PROTOCOL);
		}

		stats.current_file_index = i;
		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;

		if (dry_run) { /* log the transfer */
			if (!am_server && log_format)
				log_send(file, &stats, iflags);
			write_int(f_out, i);
			if (protocol_version >= 29)
				write_shortint(f_out, iflags);
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
				safe_fname(fname), (double)st.st_size);
		}

		write_int(f_out, i);
		if (protocol_version >= 29)
			write_shortint(f_out, iflags);
		write_sum_head(f_out, s);

		if (verbose > 2) {
			rprintf(FINFO, "calling match_sums %s\n",
				safe_fname(fname));
		}

		if (log_before_transfer)
			log_send(file, &initial_stats, iflags);
		else if (!am_server && verbose && do_progress)
			rprintf(FINFO, "%s\n", safe_fname(fname2));

		set_compression(fname);

		match_sums(f_out, s, mbuf, st.st_size);
		if (!log_before_transfer)
			log_send(file, &initial_stats, iflags);

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

		if (verbose > 2) {
			rprintf(FINFO, "sender finished %s\n",
				safe_fname(fname));
		}
	}
	make_backups = save_make_backups;

	if (verbose > 2)
		rprintf(FINFO, "send files finished\n");

	match_report();

	write_int(f_out, -1);
}
