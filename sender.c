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
extern int am_server;
extern int am_daemon;
extern int log_before_transfer;
extern int log_format_has_i;
extern int daemon_log_format_has_i;
extern int csum_length;
extern int io_error;
extern int protocol_version;
extern int remove_sent_files;
extern int updating_basis_file;
extern int make_backups;
extern int do_progress;
extern int inplace;
extern struct stats stats;
extern struct file_list *the_file_list;
extern char *log_format;


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

void successful_send(int ndx)
{
	char fname[MAXPATHLEN];
	struct file_struct *file;
	unsigned int offset;

	if (ndx < 0 || ndx >= the_file_list->count)
		return;

	file = the_file_list->files[ndx];
	/* The generator might tell us about symlinks we didn't send. */
	if (!(file->flags & FLAG_SENT) && !S_ISLNK(file->mode))
		return;
	if (file->dir.root) {
		offset = stringjoin(fname, sizeof fname,
				    file->dir.root, "/", NULL);
	} else
		offset = 0;
	f_name_to(file, fname + offset);
	if (remove_sent_files && do_unlink(fname) == 0 && verbose > 1) {
		rprintf(FINFO, "sender removed %s\n",
			safe_fname(fname + offset));
	}
}

static void write_item_attrs(int f_out, int ndx, int iflags, char *buf,
			     uchar fnamecmp_type, int len)
{
	write_int(f_out, ndx);
	if (protocol_version < 29)
		return;
	write_shortint(f_out, iflags);
	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		write_byte(f_out, fnamecmp_type);
	if (iflags & ITEM_XNAME_FOLLOWS) {
		if (len < 0)
			len = strlen(buf);
		write_vstring(f_out, buf, len);
	}
}

/* This is also used by receive.c with f_out = -1. */
int read_item_attrs(int f_in, int f_out, int ndx, char *buf, uchar *type_ptr)
{
	int len;
	uchar fnamecmp_type = FNAMECMP_FNAME;
	int iflags = protocol_version >= 29 ? read_shortint(f_in)
		   : ITEM_TRANSFER | ITEM_MISSING_DATA;
	int isave = iflags; /* XXX remove soon */

	/* Handle the new keep-alive (no-op) packet. */
	if (ndx == the_file_list->count && iflags == ITEM_IS_NEW)
		;
	else if (ndx < 0 || ndx >= the_file_list->count) {
		rprintf(FERROR, "Invalid file index %d (count=%d) [%s]\n",
			ndx, the_file_list->count, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	} else if (iflags == ITEM_IS_NEW) {
		rprintf(FERROR, "Invalid itemized flag word [%s]\n",
			who_am_i());
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

	/* XXX Temporary backward compatibility when talking to 2.6.4pre[12] */
	if (protocol_version >= 29 && iflags & ITEM_TRANSFER
	    && !S_ISREG(the_file_list->files[ndx]->mode)) {
		iflags &= ~ITEM_TRANSFER;
		iflags |= ITEM_LOCAL_CHANGE;
	}

	if (iflags & ITEM_TRANSFER) {
		if (!S_ISREG(the_file_list->files[ndx]->mode)) {
			rprintf(FERROR,
				"received index of non-regular file: %d [%s]\n",
				ndx, who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}
	} else if (f_out >= 0) {
		write_item_attrs(f_out, ndx, isave /*XXX iflags */,
				 buf, fnamecmp_type, len);
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
	char fnametmp[MAXPATHLEN];
	uchar fnamecmp_type;
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

		iflags = read_item_attrs(f_in, f_out, i, fnametmp,
					 &fnamecmp_type);
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
		fname2 = f_name_to(file, fname + offset);

		if (verbose > 2)
			rprintf(FINFO, "send_files(%d, %s)\n", i, fname);

		if (!(iflags & ITEM_TRANSFER)) {
			maybe_log_item(file, iflags, itemizing, fnametmp);
			continue;
		}

		if (protocol_version >= 29) {
			updating_basis_file = inplace
					   && fnamecmp_type == FNAMECMP_FNAME;
		} else
			updating_basis_file = inplace && !make_backups;

		stats.current_file_index = i;
		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;

		if (dry_run) { /* log the transfer */
			if (!am_server && log_format)
				log_item(file, &stats, iflags, NULL);
			write_item_attrs(f_out, i, iflags, fnametmp,
					 fnamecmp_type, -1);
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

		write_item_attrs(f_out, i, iflags, fnametmp, fnamecmp_type, -1);
		write_sum_head(f_out, s);

		if (verbose > 2) {
			rprintf(FINFO, "calling match_sums %s\n",
				safe_fname(fname));
		}

		if (log_before_transfer)
			log_item(file, &initial_stats, iflags, NULL);
		else if (!am_server && verbose && do_progress)
			rprintf(FINFO, "%s\n", safe_fname(fname2));

		set_compression(fname);

		match_sums(f_out, s, mbuf, st.st_size);
		if (do_progress)
			end_progress(st.st_size);

		if (!log_before_transfer)
			log_item(file, &initial_stats, iflags, NULL);

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

		/* Flag that we actually sent this entry. */
		file->flags |= FLAG_SENT;
	}
	make_backups = save_make_backups;

	if (verbose > 2)
		rprintf(FINFO, "send files finished\n");

	match_report();

	write_int(f_out, -1);
}
