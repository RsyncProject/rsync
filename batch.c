/* -*- c-file-style: "linux" -*-
   
   Weiss 1/1999
   Batch utilities for rsync.

*/

#include "rsync.h"
#include <time.h>

char rsync_flist_file[27] = "rsync_flist.";
char rsync_csums_file[27] = "rsync_csums.";
char rsync_delta_file[27] = "rsync_delta.";
char rsync_argvs_file[27] = "rsync_argvs.";

char batch_file_ext[15];

int fdb;
int fdb_delta;
int fdb_open;
int fdb_close;

struct file_list *batch_flist;

void create_batch_file_ext()
{
	struct tm *timeptr;
	time_t elapsed_seconds;

	/* Save run date and time to use for batch file extensions */
	time(&elapsed_seconds);
	timeptr = localtime(&elapsed_seconds);

	sprintf(batch_file_ext, "%4d%02d%02d%02d%02d%02d",
		timeptr->tm_year + 1900, timeptr->tm_mon + 1,
		timeptr->tm_mday, timeptr->tm_hour, timeptr->tm_min,
		timeptr->tm_sec);
	rprintf(FINFO,"batch file extension: %s\n", batch_file_ext);
}

void set_batch_file_ext(char *ext)
{
	strcpy(batch_file_ext, ext);
}

void write_batch_flist_file(char *buff, int bytes_to_write)
{

	if (fdb_open) {
		/* Set up file extension */
		strcat(rsync_flist_file, batch_file_ext);

		/* Open batch flist file for writing; create it if it doesn't exist */
		fdb =
		    do_open(rsync_flist_file, O_WRONLY | O_CREAT | O_TRUNC,
			    S_IREAD | S_IWRITE);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				rsync_flist_file, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Write buffer to batch flist file */

	if (write(fdb, buff, bytes_to_write) == -1) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			rsync_flist_file, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}


	if (fdb_close) {
		close(fdb);
	}
}

void write_batch_flist_info(int flist_count, struct file_struct **fptr)
{
	int i;
	int bytes_to_write;

	/* Write flist info to batch file */

	bytes_to_write =
	    sizeof(unsigned) +
	    sizeof(time_t) +
	    sizeof(OFF_T) +
	    sizeof(mode_t) +
	    sizeof(INO64_T) +
	    sizeof(DEV64_T) +
	    sizeof(DEV64_T) +
	    sizeof(uid_t) +
	    sizeof(gid_t);

	fdb_open = 1;
	fdb_close = 0;

	for (i = 0; i < flist_count; i++) {
		write_batch_flist_file((char *) fptr[i], bytes_to_write);
		write_char_bufs(fptr[i]->basename);
		write_char_bufs(fptr[i]->dirname);
		write_char_bufs(fptr[i]->basedir);
		write_char_bufs(fptr[i]->link);
		if (i == flist_count - 1) {
			fdb_close = 1;
		}
		write_char_bufs(fptr[i]->sum);
	}

}

void write_char_bufs(char *buf)
{
	/* Write the size of the string which will follow  */

	char b[4];
	if (buf != NULL)
		SIVAL(b, 0, strlen(buf));
	else {
		SIVAL(b, 0, 0);
	}

	write_batch_flist_file(b, sizeof(int));

	/*  Write the string if there is one */

	if (buf != NULL) {
		write_batch_flist_file(buf, strlen(buf));
	}
}

void write_batch_argvs_file(int argc, char *argv[])
{
	int fdb;
	int i;
	char buff[256];

	strcat(rsync_argvs_file, batch_file_ext);


	/* Open batch argvs file for writing; create it if it doesn't exist */
	fdb = do_open(rsync_argvs_file, O_WRONLY | O_CREAT | O_TRUNC,
		      S_IREAD | S_IWRITE | S_IEXEC);
	if (fdb == -1) {
		rprintf(FERROR, "Batch file %s open error: %s\n",
			rsync_argvs_file, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
	buff[0] = '\0';
	/* Write argvs info to batch file */

	for (i = 0; i < argc; ++i) {
		if (i == argc - 2)
		    continue;
		/*
		 * FIXME:
		 * I think directly manipulating argv[] is probably bogus
		 */
		if (!strcmp(argv[i], "--write-batch")) {
			/* Safer to change it here than script */
			/* Change to --read-batch + ext * to get ready for remote */
			strlcat(buff, "--read-batch ", sizeof(buff));
			strlcat(buff, batch_file_ext, sizeof(buff));
		} else {
			strlcat(buff, argv[i], sizeof(buff));
		}

		if (i < (argc - 1)) {
			strlcat(buff, " ", sizeof(buff));
		}
	}
	strlcat(buff, "\n", sizeof(buff));
	if (!write(fdb, buff, strlen(buff))) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			rsync_argvs_file, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
	close(fdb);
}

struct file_list *create_flist_from_batch()
{
	unsigned char flags;

	fdb_open = 1;
	fdb_close = 0;

	batch_flist = (struct file_list *) malloc(sizeof(batch_flist[0]));
	if (!batch_flist) {
		out_of_memory("create_flist_from_batch");
	}
	batch_flist->count = 0;
	batch_flist->malloced = 1000;
	batch_flist->files =
	    (struct file_struct **) malloc(sizeof(batch_flist->files[0]) *
					   batch_flist->malloced);
	if (!batch_flist->files) {
		out_of_memory("create_flist_from_batch");	/* dw -- will exit */
	}

	for (flags = read_batch_flags(); flags; flags = read_batch_flags()) {

		int i = batch_flist->count;

		if (i >= batch_flist->malloced) {
			if (batch_flist->malloced < 1000)
				batch_flist->malloced += 1000;
			else
				batch_flist->malloced *= 2;
			batch_flist->files =
			    (struct file_struct **) realloc(batch_flist->
							    files,
							    sizeof
							    (batch_flist->
							     files[0]) *
							    batch_flist->
							    malloced);
			if (!batch_flist->files)
				out_of_memory("create_flist_from_batch");
		}
		read_batch_flist_info(&batch_flist->files[i]);
		batch_flist->files[i]->flags = flags;

		batch_flist->count++;
	}

	return batch_flist;

}

int read_batch_flist_file(char *buff, int len)
{
	int bytes_read;

	if (fdb_open) {

		/*  Set up file extension  */
		strcat(rsync_flist_file, batch_file_ext);

		/* Open batch flist file for reading */
		fdb = do_open(rsync_flist_file, O_RDONLY, 0);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				rsync_flist_file, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Read flist batch file */

	bytes_read = read(fdb, buff, len);

	if (bytes_read == -1) {
		rprintf(FERROR, "Batch file %s read error: %s\n",
			rsync_flist_file, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
	if (bytes_read == 0) {	/* EOF */
		close(fdb);
	}
	return bytes_read;
}

unsigned char read_batch_flags()
{
	int flags;

	if (read_batch_flist_file((char *) &flags, 4)) {
		return 1;
	} else {
		return 0;
	}
}

void read_batch_flist_info(struct file_struct **fptr)
{
	int int_str_len;
	char char_str_len[4];
	char buff[256];
	struct file_struct *file;

	file = (struct file_struct *) malloc(sizeof(*file));
	if (!file)
		out_of_memory("read_batch_flist_info");
	memset((char *) file, 0, sizeof(*file));

	(*fptr) = file;

	read_batch_flist_file((char *) &file->modtime, sizeof(time_t));
	read_batch_flist_file((char *) &file->length, sizeof(OFF_T));
	read_batch_flist_file((char *) &file->mode, sizeof(mode_t));
	read_batch_flist_file((char *) &file->inode, sizeof(INO64_T));
	read_batch_flist_file((char *) &file->dev, sizeof(DEV64_T));
	read_batch_flist_file((char *) &file->rdev, sizeof(DEV64_T));
	read_batch_flist_file((char *) &file->uid, sizeof(uid_t));
	read_batch_flist_file((char *) &file->gid, sizeof(gid_t));
	read_batch_flist_file(char_str_len, sizeof(char_str_len));
	int_str_len = IVAL(char_str_len, 0);
	if (int_str_len > 0) {
		read_batch_flist_file(buff, int_str_len);
		buff[int_str_len] = '\0';
		file->basename = strdup(buff);
	} else {
		file->basename = NULL;
	}

	read_batch_flist_file(char_str_len, sizeof(char_str_len));
	int_str_len = IVAL(char_str_len, 0);
	if (int_str_len > 0) {
		read_batch_flist_file(buff, int_str_len);
		buff[int_str_len] = '\0';
		file[0].dirname = strdup(buff);
	} else {
		file[0].dirname = NULL;
	}

	read_batch_flist_file(char_str_len, sizeof(char_str_len));
	int_str_len = IVAL(char_str_len, 0);
	if (int_str_len > 0) {
		read_batch_flist_file(buff, int_str_len);
		buff[int_str_len] = '\0';
		file[0].basedir = strdup(buff);
	} else {
		file[0].basedir = NULL;
	}

	read_batch_flist_file(char_str_len, sizeof(char_str_len));
	int_str_len = IVAL(char_str_len, 0);
	if (int_str_len > 0) {
		read_batch_flist_file(buff, int_str_len);
		buff[int_str_len] = '\0';
		file[0].link = strdup(buff);
	} else {
		file[0].link = NULL;
	}

	read_batch_flist_file(char_str_len, sizeof(char_str_len));
	int_str_len = IVAL(char_str_len, 0);
	if (int_str_len > 0) {
		read_batch_flist_file(buff, int_str_len);
		buff[int_str_len] = '\0';
		file[0].sum = strdup(buff);
	} else {
		file[0].sum = NULL;
	}
}

void write_batch_csums_file(void *buff, int bytes_to_write)
{

	static int fdb_open = 1;

	if (fdb_open) {
		/* Set up file extension */
		strcat(rsync_csums_file, batch_file_ext);

		/* Open batch csums file for writing; create it if it doesn't exist */
		fdb =
		    do_open(rsync_csums_file, O_WRONLY | O_CREAT | O_TRUNC,
			    S_IREAD | S_IWRITE);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				rsync_csums_file, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Write buffer to batch csums file */

	if (write(fdb, buff, bytes_to_write) == -1) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			rsync_csums_file, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
}

void close_batch_csums_file()
{
	close(fdb);

}

void write_batch_csum_info(int *flist_entry, int flist_count,
			   struct sum_struct *s)
{
	size_t i;
	unsigned int int_zero = 0;
	extern int csum_length;

	fdb_open = 1;

	/* Write csum info to batch file */

	/* FIXME: This will break if s->count is ever not exactly an int. */
	write_batch_csums_file(flist_entry, sizeof(int));
	if (s)
		write_batch_csums_file(&s->count, sizeof(int));
	else
		write_batch_csums_file(&int_zero, sizeof (int));
	
	if (s) {
		for (i = 0; i < s->count; i++) {
			write_batch_csums_file(&s->sums[i].sum1, sizeof(uint32));
			if ((*flist_entry == flist_count - 1)
			    && (i == s->count - 1)) {
				fdb_close = 1;
			}
			write_batch_csums_file(s->sums[i].sum2,
					       csum_length);
		}
	}
}

int read_batch_csums_file(char *buff, int len)
{
	static int fdb_open = 1;
	int bytes_read;

	if (fdb_open) {

		/*  Set up file extension  */
		strcat(rsync_csums_file, batch_file_ext);

		/* Open batch flist file for reading */
		fdb = do_open(rsync_csums_file, O_RDONLY, 0);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				rsync_csums_file, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Read csums batch file */

	bytes_read = read(fdb, buff, len);

	if (bytes_read == -1) {
		rprintf(FERROR, "Batch file %s read error: %s\n",
			rsync_csums_file, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
	return bytes_read;
}


void read_batch_csum_info(int flist_entry, struct sum_struct *s,
			  int *checksums_match)
{
	int i;
	int file_flist_entry;
	int file_chunk_ct;
	uint32 file_sum1;
	char file_sum2[SUM_LENGTH];
	extern int csum_length;


	read_batch_csums_file((char *) &file_flist_entry, sizeof(int));
	if (file_flist_entry != flist_entry) {
		rprintf(FINFO, "file_list_entry NE flist_entry\n");
		rprintf(FINFO, "file_flist_entry = %d  flist_entry = %d\n",
			file_flist_entry, flist_entry);
		close(fdb);
		exit_cleanup(1);

	} else {
		read_batch_csums_file((char *) &file_chunk_ct,
				      sizeof(int));
		*checksums_match = 1;
		for (i = 0; i < file_chunk_ct; i++) {

			read_batch_csums_file((char *) &file_sum1,
					      sizeof(uint32));
			read_batch_csums_file(file_sum2, csum_length);

			if ((s->sums[i].sum1 != file_sum1) ||
			    (memcmp
			     (s->sums[i].sum2, file_sum2,
			      csum_length) != 0)) {
				*checksums_match = 0;
			}
		}		/*  end for  */
	}

}

void write_batch_delta_file(char *buff, int bytes_to_write)
{
	static int fdb_delta_open = 1;

	if (fdb_delta_open) {
		/* Set up file extension */
		strcat(rsync_delta_file, batch_file_ext);

		/* Open batch delta file for writing; create it if it doesn't exist */
		fdb_delta =
		    do_open(rsync_delta_file, O_WRONLY | O_CREAT | O_TRUNC,
			    S_IREAD | S_IWRITE);
		if (fdb_delta == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				rsync_delta_file, strerror(errno));
			close(fdb_delta);
			exit_cleanup(1);
		}
		fdb_delta_open = 0;
	}

	/* Write buffer to batch delta file */

	if (write(fdb_delta, buff, bytes_to_write) == -1) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			rsync_delta_file, strerror(errno));
		close(fdb_delta);
		exit_cleanup(1);
	}
}
void close_batch_delta_file()
{
	close(fdb_delta);

}

int read_batch_delta_file(char *buff, int len)
{
	static int fdb_delta_open = 1;
	int bytes_read;

	if (fdb_delta_open) {

		/*  Set up file extension  */
		strcat(rsync_delta_file, batch_file_ext);

		/* Open batch flist file for reading */
		fdb_delta = do_open(rsync_delta_file, O_RDONLY, 0);
		if (fdb_delta == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				rsync_delta_file, strerror(errno));
			close(fdb_delta);
			exit_cleanup(1);
		}
		fdb_delta_open = 0;
	}

	/* Read delta batch file */

	bytes_read = read(fdb_delta, buff, len);

	if (bytes_read == -1) {
		rprintf(FERROR, "Batch file %s read error: %s\n",
			rsync_delta_file, strerror(errno));
		close(fdb_delta);
		exit_cleanup(1);
	}
	return bytes_read;
}


void show_flist(int index, struct file_struct **fptr)
{
	/*  for debugging    show_flist(flist->count, flist->files * */

	int i;
	for (i = 0; i < index; i++) {
		rprintf(FINFO, "flist->flags=%#x\n", fptr[i]->flags);
		rprintf(FINFO, "flist->modtime=%#lx\n",
			(long unsigned) fptr[i]->modtime);
		rprintf(FINFO, "flist->length=%.0f\n",
			(double) fptr[i]->length);
		rprintf(FINFO, "flist->mode=%#o\n", (int) fptr[i]->mode);
		rprintf(FINFO, "flist->basename=%s\n", fptr[i]->basename);
		if (fptr[i]->dirname)
			rprintf(FINFO, "flist->dirname=%s\n",
				fptr[i]->dirname);
		if (fptr[i]->basedir)
			rprintf(FINFO, "flist->basedir=%s\n",
				fptr[i]->basedir);
	}
}

void show_argvs(int argc, char *argv[])
{
	/*  for debugging  * */

	int i;
	rprintf(FINFO, "BATCH.C:show_argvs,argc=%d\n", argc);
	for (i = 0; i < argc; i++) {
		/*    if (argv[i])   */
		rprintf(FINFO, "i=%d,argv[i]=%s\n", i, argv[i]);

	}
}
