/*
 * Support for the batch-file options.
 *
 * Copyright (C) 1999 Weiss
 * Copyright (C) 2004 Chris Shoemaker
 * Copyright (C) 2004-2022 Wayne Davison
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
#include <zlib.h>
#include <time.h>

extern int eol_nulls;
extern int recurse;
extern int xfer_dirs;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_acls;
extern int preserve_xattrs;
extern int always_checksum;
extern int do_compression;
extern int inplace;
extern int append_mode;
extern int write_batch;
extern int protocol_version;
extern int raw_argc, cooked_argc;
extern char **raw_argv, **cooked_argv;
extern char *batch_name;
#ifdef ICONV_OPTION
extern char *iconv_opt;
#endif

extern filter_rule_list filter_list;

int batch_fd = -1;
int batch_sh_fd = -1;
int batch_stream_flags;

static int tweaked_append;
static int tweaked_append_verify;
static int tweaked_iconv;

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
	&tweaked_iconv,		/* 9  (protocol 30) */
	&preserve_acls,		/* 10 (protocol 30) */
	&preserve_xattrs,	/* 11 (protocol 30) */
	&inplace,		/* 12 (protocol 30) */
	&tweaked_append,	/* 13 (protocol 30) */
	&tweaked_append_verify,	/* 14 (protocol 30) */
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
	"--iconv",
	"--acls (-A)",
	"--xattrs (-X)",
	"--inplace",
	"--append",
	"--append-verify",
	NULL
};

void write_stream_flags(int fd)
{
	int i, flags;

	tweaked_append = append_mode == 1;
	tweaked_append_verify = append_mode == 2;
#ifdef ICONV_OPTION
	tweaked_iconv = iconv_opt != NULL;
#endif

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
	batch_stream_flags = read_int(fd);
}

void check_batch_flags(void)
{
	int i;

	if (protocol_version < 29)
		flag_ptr[7] = NULL;
	else if (protocol_version < 30)
		flag_ptr[9] = NULL;
	tweaked_append = append_mode == 1;
	tweaked_append_verify = append_mode == 2;
#ifdef ICONV_OPTION
	tweaked_iconv = iconv_opt != NULL;
#endif
	for (i = 0; flag_ptr[i]; i++) {
		int set = batch_stream_flags & (1 << i) ? 1 : 0;
		if (*flag_ptr[i] != set) {
			if (i == 9) {
				rprintf(FERROR,
					"%s specify the --iconv option to use this batch file.\n",
					set ? "Please" : "Do not");
				exit_cleanup(RERR_SYNTAX);
			}
			if (INFO_GTE(MISC, 1)) {
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

	if (tweaked_append)
		append_mode = 1;
	else if (tweaked_append_verify)
		append_mode = 2;
}

static int write_arg(const char *arg)
{
	const char *x, *s;
	int len, err = 0;

	if (*arg == '-' && (x = strchr(arg, '=')) != NULL) {
		err |= write(batch_sh_fd, arg, x - arg + 1) != x - arg + 1;
		arg += x - arg + 1;
	}

	if (strpbrk(arg, " \"'&;|[]()$#!*?^\\") != NULL) {
		err |= write(batch_sh_fd, "'", 1) != 1;
		for (s = arg; (x = strchr(s, '\'')) != NULL; s = x + 1) {
			err |= write(batch_sh_fd, s, x - s + 1) != x - s + 1;
			err |= write(batch_sh_fd, "'", 1) != 1;
		}
		len = strlen(s);
		err |= write(batch_sh_fd, s, len) != len;
		err |= write(batch_sh_fd, "'", 1) != 1;
		return err;
	}

	len = strlen(arg);
	err |= write(batch_sh_fd, arg, len) != len;

	return err;
}

/* Writes out a space and then an option (or other string) with an optional "=" + arg suffix. */
static int write_opt(const char *opt, const char *arg)
{
	int len = strlen(opt);
	int err = write(batch_sh_fd, " ", 1) != 1;
	err = write(batch_sh_fd, opt, len) != len ? 1 : 0;
	if (arg) {
		err |= write(batch_sh_fd, "=", 1) != 1;
		err |= write_arg(arg);
	}
	return err;
}

static void write_filter_rules(int fd)
{
	filter_rule *ent;

	write_sbuf(fd, " <<'#E#'\n");
	for (ent = filter_list.head; ent; ent = ent->next) {
		unsigned int plen;
		char *p = get_rule_prefix(ent, "- ", 0, &plen);
		write_buf(fd, p, plen);
		write_sbuf(fd, ent->pattern);
		if (ent->rflags & FILTRULE_DIRECTORY)
			write_byte(fd, '/');
		write_byte(fd, eol_nulls ? 0 : '\n');
	}
	if (eol_nulls)
		write_sbuf(fd, ";\n");
	write_sbuf(fd, "#E#");
}

/* This sets batch_fd and (for --write-batch) batch_sh_fd. */
void open_batch_files(void)
{
	if (write_batch) {
		char filename[MAXPATHLEN];

		stringjoin(filename, sizeof filename, batch_name, ".sh", NULL);

		batch_sh_fd = do_open(filename, O_WRONLY | O_CREAT | O_TRUNC, S_IRUSR | S_IWUSR | S_IXUSR);
		if (batch_sh_fd < 0) {
			rsyserr(FERROR, errno, "Batch file %s open error", full_fname(filename));
			exit_cleanup(RERR_FILESELECT);
		}

		batch_fd = do_open(batch_name, O_WRONLY | O_CREAT | O_TRUNC, S_IRUSR | S_IWUSR);
	} else if (strcmp(batch_name, "-") == 0)
		batch_fd = STDIN_FILENO;
	else
		batch_fd = do_open(batch_name, O_RDONLY, S_IRUSR | S_IWUSR);

	if (batch_fd < 0) {
		rsyserr(FERROR, errno, "Batch file %s open error", full_fname(batch_name));
		exit_cleanup(RERR_FILEIO);
	}
}

/* This routine tries to write out an equivalent --read-batch command
 * given the user's --write-batch args.  However, it doesn't really
 * understand most of the options, so it uses some overly simple
 * heuristics to munge the command line into something that will
 * (hopefully) work. */
void write_batch_shell_file(void)
{
	int i, j, len, err = 0;
	char *p, *p2;

	/* Write argvs info to BATCH.sh file */
	err |= write_arg(raw_argv[0]);
	if (filter_list.head) {
		if (protocol_version >= 29)
			err |= write_opt("--filter", "._-");
		else
			err |= write_opt("--exclude-from", "-");
	}

	/* Elide the filename args from the option list, but scan for them in reverse. */
	for (i = raw_argc-1, j = cooked_argc-1; i > 0 && j >= 0; i--) {
		if (strcmp(raw_argv[i], cooked_argv[j]) == 0) {
			raw_argv[i] = NULL;
			j--;
		}
	}

	for (i = 1; i < raw_argc; i++) {
		if (!(p = raw_argv[i]))
			continue;
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
		if (strncmp(p, "--write-batch", len = 13) == 0
		 || strncmp(p, "--only-write-batch", len = 18) == 0)
			err |= write_opt("--read-batch", p[len] == '=' ? p + len + 1 : NULL);
		else {
			err |= write(batch_sh_fd, " ", 1) != 1;
			err |= write_arg(p);
		}
	}
	if (!(p = check_for_hostspec(cooked_argv[cooked_argc - 1], &p2, &i)))
		p = cooked_argv[cooked_argc - 1];
	err |= write_opt("${1:-", NULL);
	err |= write_arg(p);
	err |= write(batch_sh_fd, "}", 1) != 1;
	if (filter_list.head)
		write_filter_rules(batch_sh_fd);
	if (write(batch_sh_fd, "\n", 1) != 1 || close(batch_sh_fd) < 0 || err) {
		rsyserr(FERROR, errno, "Batch file %s.sh write error", batch_name);
		exit_cleanup(RERR_FILEIO);
	}
	batch_sh_fd = -1;
}
