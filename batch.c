/* -*- c-file-style: "linux" -*-

   Weiss 1/1999
   Batch utilities for rsync.

*/

#include "rsync.h"
#include <time.h>

extern char *batch_name;
extern int eol_nulls;
extern int recurse;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int always_checksum;

extern struct exclude_list_struct exclude_list;

static int *flag_ptr[] = {
	&recurse,
	&preserve_uid,
	&preserve_gid,
	&preserve_links,
	&preserve_devices,
	&preserve_hard_links,
	&always_checksum,
	NULL
};

static char *flag_name[] = {
	"--recurse (-r)",
	"--owner (-o)",
	"--group (-g)",
	"--links (-l)",
	"--devices (-D)",
	"--hard-links (-H)",
	"--checksum (-c)",
	NULL
};

void write_stream_flags(int fd)
{
	int i, flags;

	/* Start the batch file with a bitmap of data-stream-affecting
	 * flags. */
	for (i = 0, flags = 0; flag_ptr[i]; i++) {
		if (*flag_ptr[i])
			flags |= 1 << i;
	}
	write_int(fd, flags);
}

void read_stream_flags(int fd)
{
	int i, flags;

	for (i = 0, flags = read_int(fd); flag_ptr[i]; i++) {
		int set = flags & (1 << i) ? 1 : 0;
		if (*flag_ptr[i] != set) {
			if (verbose) {
				rprintf(FINFO,
					"%sing the %s option to match the batchfile.\n",
					set ? "Sett" : "Clear", flag_name[i]);
			}
			*flag_ptr[i] = set;
		}
	}
}

static void write_arg(int fd, char *arg)
{
	char *x, *s;

	if (*arg == '-' && (x = strchr(arg, '=')) != NULL) {
		write(fd, arg, x - arg + 1);
		arg += x - arg + 1;
	}

	if (strpbrk(arg, " \"'&;|[]()$#!*?^\\") != NULL) {
		write(fd, "'", 1);
		for (s = arg; (x = strchr(s, '\'')) != NULL; s = x + 1) {
			write(fd, s, x - s + 1);
			write(fd, "'", 1);
		}
		write(fd, s, strlen(s));
		write(fd, "'", 1);
		return;
	}

	write(fd, arg, strlen(arg));
}

static void write_excludes(int fd)
{
	struct exclude_struct *ent;

	write_sbuf(fd, " <<'#E#'\n");
	for (ent = exclude_list.head; ent; ent = ent->next) {
		char *p = ent->pattern;
		if (ent->match_flags & MATCHFLG_INCLUDE)
			write_buf(fd, "+ ", 2);
		else if (((*p == '-' || *p == '+') && p[1] == ' ')
		    || *p == '#' || *p == ';')
			write_buf(fd, "- ", 2);
		write_sbuf(fd, p);
		if (ent->match_flags & MATCHFLG_DIRECTORY)
			write_byte(fd, '/');
		write_byte(fd, eol_nulls ? 0 : '\n');
	}
	if (eol_nulls)
		write_sbuf(fd, ";\n");
	write_sbuf(fd, "#E#");
}

/* This routine tries to write out an equivalent --read-batch command
 * given the user's --write-batch args.  However, it doesn't really
 * understand most of the options, so it uses some overly simple
 * heuristics to munge the command line into something that will
 * (hopefully) work. */
void write_batch_shell_file(int argc, char *argv[], int file_arg_cnt)
{
	int fd, i;
	char *p, filename[MAXPATHLEN];

	stringjoin(filename, sizeof filename,
		   batch_name, ".sh", NULL);
	fd = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
		     S_IRUSR | S_IWUSR | S_IEXEC);
	if (fd < 0) {
		rsyserr(FERROR, errno, "Batch file %s open error", filename);
		exit_cleanup(1);
	}

	/* Write argvs info to BATCH.sh file */
	write_arg(fd, argv[0]);
	if (exclude_list.head)
		write_sbuf(fd, " --exclude-from=-");
	for (i = 1; i < argc - file_arg_cnt; i++) {
		p = argv[i];
		if (strncmp(p, "--files-from", 12) == 0
		    || strncmp(p, "--include", 9) == 0
		    || strncmp(p, "--exclude", 9) == 0) {
			if (strchr(p, '=') == NULL)
				i++;
			continue;
		}
		write(fd, " ", 1);
		if (strncmp(p, "--write-batch", 13) == 0) {
			write(fd, "--read-batch", 12);
			if (p[13] == '=') {
				write(fd, "=", 1);
				write_arg(fd, p + 14);
			}
		} else
			write_arg(fd, p);
	}
	if ((p = find_colon(argv[argc - 1])) != NULL) {
		if (*++p == ':')
			p++;
	} else
		p = argv[argc - 1];
	write(fd, " ${1:-", 6);
	write_arg(fd, p);
	write_byte(fd, '}');
	if (exclude_list.head)
		write_excludes(fd);
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
