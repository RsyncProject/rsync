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
extern int csum_length;
extern struct stats stats;
extern int io_error;
extern int dry_run;
extern int am_server;
extern int am_daemon;
extern int protocol_version;


/**
 * @file
 *
 * The sender gets checksums from the generator, calculates deltas,
 * and transmits them to the receiver.  The sender process runs on the
 * machine holding the source files.
 **/
void read_sum_head(int f, struct sum_struct *sum)
{
	sum->count = read_int(f);
	sum->blength = read_int(f);
	if (protocol_version < 27) {
		sum->s2length = csum_length;
	} else {
		sum->s2length = read_int(f);
		if (sum->s2length > MD4_SUM_LENGTH) {
			rprintf(FERROR, "Invalid checksum length %ld\n",
			    (long)sum->s2length);
			exit_cleanup(RERR_PROTOCOL);
		}
	}
	sum->remainder = read_int(f);
}

/**
 * Receive the checksums for a buffer
 **/
static struct sum_struct *receive_sums(int f)
{
	struct sum_struct *s;
	int i;
	OFF_T offset = 0;

	s = new(struct sum_struct);
	if (!s) out_of_memory("receive_sums");

	read_sum_head(f, s);

	s->sums = NULL;

	if (verbose > 3) {
		rprintf(FINFO, "count=%ld n=%u rem=%u\n",
			(long)s->count, s->blength, s->remainder);
	}

	if (s->count == 0)
		return(s);

	s->sums = new_array(struct sum_buf, s->count);
	if (!s->sums) out_of_memory("receive_sums");

	for (i = 0; i < (int) s->count; i++) {
		s->sums[i].sum1 = read_int(f);
		read_buf(f, s->sums[i].sum2, s->s2length);

		s->sums[i].offset = offset;
		s->sums[i].i = i;

		if (i == (int) s->count-1 && s->remainder != 0) {
			s->sums[i].len = s->remainder;
		} else {
			s->sums[i].len = s->blength;
		}
		offset += s->sums[i].len;

		if (verbose > 3)
			rprintf(FINFO, "chunk[%d] len=%d offset=%.0f sum1=%08x\n",
				i, s->sums[i].len, (double)s->sums[i].offset, s->sums[i].sum1);
	}

	s->flength = offset;

	return s;
}



void send_files(struct file_list *flist, int f_out, int f_in)
{
	int fd = -1;
	struct sum_struct *s;
	struct map_struct *buf = NULL;
	STRUCT_STAT st;
	char fname[MAXPATHLEN];
	int i;
	struct file_struct *file;
	int phase = 0;
	extern struct stats stats;
	struct stats initial_stats;
	extern int write_batch;
	extern int read_batch;
	int checksums_match;
	int buff_len;
	char buff[CHUNK_SIZE];
	int j;
	int done;

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

		stats.current_file_index = i;
		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;

		if (file->basedir) {
			/* N.B. We're sure that this fits, so offset is OK. */
			offset = strlcpy(fname, file->basedir, sizeof fname);
			if (!offset || fname[offset-1] != '/')
				fname[offset++] = '/';
		} else
			offset = 0;
		f_name_to(file, fname + offset);

		if (verbose > 2)
			rprintf(FINFO, "send_files(%d, %s)\n", i, fname);

		if (dry_run) {
			if (!am_server && verbose) {	/* log transfer */
				rprintf(FINFO, "%s\n", fname+offset);
			}
			write_int(f_out, i);
			continue;
		}

		initial_stats = stats;

		s = receive_sums(f_in);
		if (!s) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR, "receive_sums failed\n");
			return;
		}

		if (write_batch)
			write_batch_csum_info(&i, s);

		if (!read_batch) {
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
					rprintf(FERROR, "send_files failed to open %s: %s\n",
						full_fname(fname), strerror(errno));
				}
				free_sums(s);
				continue;
			}

			/* map the local file */
			if (do_fstat(fd, &st) != 0) {
				io_error |= IOERR_GENERAL;
				rprintf(FERROR, "fstat failed: %s\n", strerror(errno));
				free_sums(s);
				close(fd);
				return;
			}

			if (st.st_size > 0) {
				buf = map_file(fd, st.st_size);
			} else {
				buf = NULL;
			}

			if (verbose > 2)
				rprintf(FINFO, "send_files mapped %s of size %.0f\n",
					fname, (double)st.st_size);

			write_int(f_out, i);

			if (write_batch)
				write_batch_delta_file((char *)&i, sizeof i);

			write_sum_head(f_out, s);
		}

		if (verbose > 2 && !read_batch)
			rprintf(FINFO, "calling match_sums %s\n", fname);

		if (!am_server && verbose) {	/* log transfer */
			rprintf(FINFO, "%s\n", fname+offset);
		}

		set_compression(fname);

		if (read_batch) {
			/* read checksums originally computed on sender side */
			read_batch_csum_info(i, s, &checksums_match);
			if (checksums_match) {
				read_batch_delta_file((char*)&j, sizeof (int));
				if (j != i) {    /* if flist index entries don't match*/
					rprintf(FINFO, "index mismatch in send_files\n");
					rprintf(FINFO, "read index = %d flist ndx = %d\n", j, i);
					close_batch_delta_file();
					close_batch_csums_file();
					exit_cleanup(1);
				} else {
					write_int(f_out, j);
					write_sum_head(f_out, s);
					done = 0;
					while (!done) {
						read_batch_delta_file((char*)&buff_len, sizeof (int));
						write_int(f_out, buff_len);
						if (buff_len == 0) {
							done = 1;
						} else {
							if (buff_len > 0) {
								read_batch_delta_file(buff, buff_len);
								write_buf(f_out, buff, buff_len);
							}
						}
					}  /* end while  */
					read_batch_delta_file( buff, MD4_SUM_LENGTH);
					write_buf(f_out, buff, MD4_SUM_LENGTH);

				}  /* j=i */
			} else {  /* not checksum match */
				rprintf (FINFO, "readbatch & checksums don't match\n");
				rprintf (FINFO, "filename=%s is being skipped\n", fname);
				continue;
			}
		} else  {
			match_sums(f_out, s, buf, st.st_size);
			log_send(file, &initial_stats);
		}

		if (!read_batch) {
			if (buf) {
				j = unmap_file(buf);
				if (j) {
					io_error |= IOERR_GENERAL;
					rprintf(FERROR,
					    "read errors mapping %s: (%d) %s\n",
					    full_fname(fname), j, strerror(j));
				}
			}
			close(fd);
		}

		free_sums(s);

		if (verbose > 2)
			rprintf(FINFO, "sender finished %s\n", fname);
	}

	if (verbose > 2)
		rprintf(FINFO, "send files finished\n");

	match_report();

	write_int(f_out, -1);
	if (write_batch || read_batch) {
		close_batch_csums_file();
		close_batch_delta_file();
	}

}





