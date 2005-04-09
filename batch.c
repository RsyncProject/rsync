/* -*- c-file-style: "linux" -*-

   Weiss 1/1999
   Batch utilities for rsync.

*/

#include "rsync.h"
#include <time.h>

extern int am_sender;
extern int eol_nulls;
extern int recurse;
extern int xfer_dirs;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int always_checksum;
extern int do_compression;
extern int protocol_version;
extern char *batch_name;

extern struct filter_list_struct filter_list;

static int *flag_ptr[] = {
	&recurse,		/* 0 */
	&preserve_uid,		/* 1 */
	&preserve_gid,		/* 2 */
	&preserve_links,	/* 3 */
	&preserve_devices,	/* 4 */
	&preserve_hard_links,	/* 5 */
	&always_checksum,	/* 6 */
	&xfer_dirs,		/* 7 (protocol 29) */
	&do_compression,	/* 8 (protocol 29) */
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
	"--dirs (-d)",
	"--compress (-z)",
	NULL
};

void write_stream_flags(int fd)
{
	int i, flags;

	/* Start the batch file with a bitmap of data-stream-affecting
	 * flags. */
	if (protocol_version < 29)
		flag_ptr[7] = NULL;
	for (i = 0, flags = 0; flag_ptr[i]; i++) {
		if (*flag_ptr[i])
			flags |= 1 << i;
	}
	write_int(fd, flags);
}

void read_stream_flags(int fd)
{
	int i, flags;

	if (protocol_version < 29)
		flag_ptr[7] = NULL;
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
	if (protocol_version < 29) {
		if (recurse)
			xfer_dirs |= 1;
		else if (xfer_dirs < 2)
			xfer_dirs = 0;
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

static void write_filter_rules(int fd)
{
	struct filter_struct *ent;

	write_sbuf(fd, " <<'#E#'\n");
	for (ent = filter_list.head; ent; ent = ent->next) {
		unsigned int plen;
		char *p = get_rule_prefix(ent->match_flags, "- ", 0, &plen);
		write_buf(fd, p, plen);
		write_sbuf(fd, ent->pattern);
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
	int fd, i, len;
	char *p, filename[MAXPATHLEN];

	stringjoin(filename, sizeof filename,
		   batch_name, ".sh", NULL);
	fd = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC,
		     S_IRUSR | S_IWUSR | S_IEXEC);
	if (fd < 0) {
		rsyserr(FERROR, errno, "Batch file %s open error",
			safe_fname(filename));
		exit_cleanup(1);
	}

	/* Write argvs info to BATCH.sh file */
	write_arg(fd, argv[0]);
	if (filter_list.head) {
		if (protocol_version >= 29)
			write_sbuf(fd, " --filter=._-");
		else
			write_sbuf(fd, " --exclude-from=-");
	}
	for (i = 1; i < argc - file_arg_cnt; i++) {
		p = argv[i];
		if (strncmp(p, "--files-from", 12) == 0
		    || strncmp(p, "--filter", 8) == 0
		    || strncmp(p, "--include", 9) == 0
		    || strncmp(p, "--exclude", 9) == 0) {
			if (strchr(p, '=') == NULL)
				i++;
			continue;
		}
		if (strcmp(p, "-f") == 0) {
			i++;
			continue;
		}
		write(fd, " ", 1);
		if (strncmp(p, "--write-batch", len = 13) == 0
		 || strncmp(p, "--only-write-batch", len = 18) == 0) {
			write(fd, "--read-batch", 12);
			if (p[len] == '=') {
				write(fd, "=", 1);
				write_arg(fd, p + len + 1);
			}
		} else
			write_arg(fd, p);
	}
	if (!(p = check_for_hostspec(argv[argc - 1], &p, &i)))
		p = argv[argc - 1];
	write(fd, " ${1:-", 6);
	write_arg(fd, p);
	write_byte(fd, '}');
	if (filter_list.head)
		write_filter_rules(fd);
	if (write(fd, "\n", 1) != 1 || close(fd) < 0) {
		rsyserr(FERROR, errno, "Batch file %s write error",
			safe_fname(filename));
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
		rprintf(FINFO, "flist->basename=%s\n",
			safe_fname(fptr[i]->basename));
		if (fptr[i]->dirname) {
			rprintf(FINFO, "flist->dirname=%s\n",
				safe_fname(fptr[i]->dirname));
		}
		if (am_sender && fptr[i]->dir.root) {
			rprintf(FINFO, "flist->dir.root=%s\n",
				safe_fname(fptr[i]->dir.root));
		}
	}
}

/* for debugging */
void show_argvs(int argc, char *argv[])
{
	int i;

	rprintf(FINFO, "BATCH.C:show_argvs,argc=%d\n", argc);
	for (i = 0; i < argc; i++)
		rprintf(FINFO, "i=%d,argv[i]=%s\n", i, safe_fname(argv[i]));
}
