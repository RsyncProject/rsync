/* -*- c-file-style: "linux" -*-
   
   Weiss 1/1999
   Batch utilities for rsync.

*/

#include "rsync.h"
#include <time.h>

extern char *batch_prefix;

struct file_list *batch_flist;

static char rsync_flist_file[] = ".rsync_flist";
static char rsync_csums_file[] = ".rsync_csums";
static char rsync_delta_file[] = ".rsync_delta";
static char rsync_argvs_file[] = ".rsync_argvs";

static int fdb;
static int fdb_delta;
static int fdb_open;
static int fdb_close;

void write_batch_flist_file(char *buff, int bytes_to_write)
{
	char filename[MAXPATHLEN];

	if (fdb_open) {
		/* Set up file extension */
		strlcpy(filename, batch_prefix, sizeof(filename));
		strlcat(filename, rsync_flist_file, sizeof(filename));

		/*
		 * Open batch flist file for writing;
		 * create it if it doesn't exist
		 */
		fdb = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
			    S_IREAD | S_IWRITE);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				filename, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Write buffer to batch flist file */

	if (write(fdb, buff, bytes_to_write) == -1) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			filename, strerror(errno));
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

	SIVAL(b, 0, buf != NULL ? strlen(buf) : 0);

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
	char buff[256]; /* XXX */
	char buff2[MAXPATHLEN + 6];
	char filename[MAXPATHLEN];

	/* Set up file extension */
	strlcpy(filename, batch_prefix, sizeof(filename));
	strlcat(filename, rsync_argvs_file, sizeof(filename));

	/*
	 * Open batch argvs file for writing;
	 * create it if it doesn't exist
	 */
	fdb = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
		      S_IREAD | S_IWRITE | S_IEXEC);
	if (fdb == -1) {
		rprintf(FERROR, "Batch file %s open error: %s\n",
			filename, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
	buff[0] = '\0';

	/* Write argvs info to batch file */

	for (i = 0; i < argc; ++i) {
		if (i == argc - 2) /* Skip source directory on cmdline */
		    continue;
		/*
		 * FIXME:
		 * I think directly manipulating argv[] is probably bogus
		 */
		if (!strncmp(argv[i], "--write-batch",
			strlen("--write-batch"))) {
			/* Safer to change it here than script */
			/*
			 * Change to --read-batch=prefix
			 * to get ready for remote
			 */
			strlcat(buff, "--read-batch=", sizeof(buff));
			strlcat(buff, batch_prefix, sizeof(buff));
		} else
		if (i == argc - 1) {
		    snprintf(buff2, sizeof(buff2), "${1:-%s}", argv[i]);
		    strlcat(buff, buff2, sizeof(buff));
		}
		else {
			strlcat(buff, argv[i], sizeof(buff));
		}

		if (i < (argc - 1)) {
			strlcat(buff, " ", sizeof(buff));
		}
	}
	strlcat(buff, "\n", sizeof(buff));
	if (!write(fdb, buff, strlen(buff))) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			filename, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
	close(fdb);
}

struct file_list *create_flist_from_batch(void)
{
	unsigned char flags;

	fdb_open = 1;
	fdb_close = 0;

	batch_flist = new(struct file_list);
	if (!batch_flist) {
		out_of_memory("create_flist_from_batch");
	}
	batch_flist->count = 0;
	batch_flist->malloced = 1000;
	batch_flist->files = new_array(struct file_struct *,
				       batch_flist->malloced);
	if (!batch_flist->files) {
		out_of_memory("create_flist_from_batch");
	}

	for (flags = read_batch_flags(); flags; flags = read_batch_flags()) {

		int i = batch_flist->count;

		if (i >= batch_flist->malloced) {
			if (batch_flist->malloced < 1000)
				batch_flist->malloced += 1000;
			else
				batch_flist->malloced *= 2;
			batch_flist->files
				= realloc_array(batch_flist->files,
						struct file_struct *,
						batch_flist->malloced);
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
	char filename[MAXPATHLEN];

	if (fdb_open) {
		/* Set up file extension */
		strlcpy(filename, batch_prefix, sizeof(filename));
		strlcat(filename, rsync_flist_file, sizeof(filename));

		/* Open batch flist file for reading */
		fdb = do_open(filename, O_RDONLY, 0);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				filename, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Read flist batch file */

	switch (bytes_read = read(fdb, buff, len)) {
	    case -1:
		rprintf(FERROR, "Batch file %s read error: %s\n",
			filename, strerror(errno));
		close(fdb);
		exit_cleanup(1);
		break;
	    case 0:	/* EOF */
		close(fdb);
	}

	return bytes_read;
}

unsigned char read_batch_flags(void)
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

	file = new(struct file_struct);
	if (!file)
		out_of_memory("read_batch_flist_info");
	memset((char *) file, 0, sizeof(*file));

	*fptr = file;

	/*
	 * Keep these in sync with bytes_to_write assignment
	 * in write_batch_flist_info()
	 */
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
	char filename[MAXPATHLEN];

	if (fdb_open) {
		/* Set up file extension */
		strlcpy(filename, batch_prefix, sizeof(filename));
		strlcat(filename, rsync_csums_file, sizeof(filename));

		/*
		 * Open batch csums file for writing;
		 * create it if it doesn't exist
		 */
		fdb = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
			    S_IREAD | S_IWRITE);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				filename, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Write buffer to batch csums file */

	if (write(fdb, buff, bytes_to_write) == -1) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			filename, strerror(errno));
		close(fdb);
		exit_cleanup(1);
	}
}

void close_batch_csums_file(void)
{
	close(fdb);
}


/**
 * Write csum info to batch file 
 *
 * @todo This will break if s->count is ever larger than maxint.  The
 * batch code should probably be changed to consistently use the
 * variable-length integer routines, which is probably a compatible
 * change.
 **/
void write_batch_csum_info(int *flist_entry, int flist_count,
			   struct sum_struct *s)
{
	size_t i;
	int int_count;
	extern int csum_length;

	fdb_open = 1;

	write_batch_csums_file(flist_entry, sizeof(int));
	int_count = s ? (int) s->count : 0;
	write_batch_csums_file(&int_count, sizeof int_count);
	
	if (s) {
		for (i = 0; i < s->count; i++) {
			write_batch_csums_file(&s->sums[i].sum1, sizeof(uint32));
			if ((*flist_entry == flist_count - 1)
			    && (i == s->count - 1)) {
				fdb_close = 1;
			}
			write_batch_csums_file(s->sums[i].sum2, csum_length);
		}
	}
}

int read_batch_csums_file(char *buff, int len)
{
	static int fdb_open = 1;
	int bytes_read;
	char filename[MAXPATHLEN];

	if (fdb_open) {
		/* Set up file extension */
		strlcpy(filename, batch_prefix, sizeof(filename));
		strlcat(filename, rsync_csums_file, sizeof(filename));

		/* Open batch flist file for reading */
		fdb = do_open(filename, O_RDONLY, 0);
		if (fdb == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				filename, strerror(errno));
			close(fdb);
			exit_cleanup(1);
		}
		fdb_open = 0;
	}

	/* Read csums batch file */

	bytes_read = read(fdb, buff, len);

	if (bytes_read == -1) {
		rprintf(FERROR, "Batch file %s read error: %s\n",
			filename, strerror(errno));
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
		rprintf(FINFO, "file_flist_entry (%d) != flist_entry (%d)\n",
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
			    (memcmp(s->sums[i].sum2, file_sum2, csum_length)
				!= 0)) {
				*checksums_match = 0;
			}
		}		/*  end for  */
	}
}

void write_batch_delta_file(char *buff, int bytes_to_write)
{
	static int fdb_delta_open = 1;
	char filename[MAXPATHLEN];

	if (fdb_delta_open) {
		/* Set up file extension */
		strlcpy(filename, batch_prefix, sizeof(filename));
		strlcat(filename, rsync_delta_file, sizeof(filename));

		/*
		 * Open batch delta file for writing;
		 * create it if it doesn't exist
		 */
		fdb_delta = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
			    S_IREAD | S_IWRITE);
		if (fdb_delta == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				filename, strerror(errno));
			close(fdb_delta);
			exit_cleanup(1);
		}
		fdb_delta_open = 0;
	}

	/* Write buffer to batch delta file */

	if (write(fdb_delta, buff, bytes_to_write) == -1) {
		rprintf(FERROR, "Batch file %s write error: %s\n",
			filename, strerror(errno));
		close(fdb_delta);
		exit_cleanup(1);
	}
}

void close_batch_delta_file(void)
{
	close(fdb_delta);
}

int read_batch_delta_file(char *buff, int len)
{
	static int fdb_delta_open = 1;
	int bytes_read;
	char filename[MAXPATHLEN];

	if (fdb_delta_open) {
		/* Set up file extension */
		strlcpy(filename, batch_prefix, sizeof(filename));
		strlcat(filename, rsync_delta_file, sizeof(filename));

		/* Open batch flist file for reading */
		fdb_delta = do_open(filename, O_RDONLY, 0);
		if (fdb_delta == -1) {
			rprintf(FERROR, "Batch file %s open error: %s\n",
				filename, strerror(errno));
			close(fdb_delta);
			exit_cleanup(1);
		}
		fdb_delta_open = 0;
	}

	/* Read delta batch file */

	bytes_read = read(fdb_delta, buff, len);

	if (bytes_read == -1) {
		rprintf(FERROR, "Batch file %s read error: %s\n",
			filename, strerror(errno));
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
