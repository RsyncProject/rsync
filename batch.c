/* -*- c-file-style: "linux" -*-

   Weiss 1/1999
   Batch utilities for rsync.

*/

#include "rsync.h"
#include <time.h>

extern char *batch_name;

void write_batch_argvs_file(int argc, char *argv[])
{
	int fd, i;
	char filename[MAXPATHLEN];

	stringjoin(filename, sizeof filename,
		   batch_name, ".sh", NULL);
	fd = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
		     S_IRUSR | S_IWUSR | S_IEXEC);
	if (fd < 0) {
		rsyserr(FERROR, errno, "Batch file %s open error", filename);
		exit_cleanup(1);
	}

	/* Write argvs info to BATCH.rsync_argvs file */
	for (i = 0; i < argc; i++) {
		if (i == argc - 2) /* Skip source directory on cmdline */
			continue;
		if (strncmp(argv[i], "--files-from", 12) == 0) {
			if (strchr(argv[i], '=') == NULL)
				i++;
			continue;
		}
		if (i != 0)
			write(fd, " ", 1);
		if (strncmp(argv[i], "--write-batch", 13) == 0) {
			write(fd, "--read", 6);
			write(fd, argv[i] + 7, strlen(argv[i] + 7));
		} else if (i == argc - 1) {
			char *p = find_colon(argv[i]);
			if (p) {
				if (*++p == ':')
					p++;
			} else
				p = argv[i];
			write(fd, "${1:-", 5);
			write(fd, p, strlen(p));
			write(fd, "}", 1);
		} else
			write(fd, argv[i], strlen(argv[i]));
	}
	if (write(fd, "\n", 1) != 1 || close(fd) < 0) {
		rsyserr(FERROR, errno, "Batch file %s write error", filename);
		exit_cleanup(1);
	}
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
