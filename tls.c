/*
 * Trivial ls for comparing two directories after running an rsync.
 *
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2020 Wayne Davison
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
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

/* The problem with using the system's own ls is that some features
 * have little quirks that make directories look different when for
 * our purposes they're the same -- for example, the BSD braindamage
 * about setting the mode on symlinks based on your current umask.
 *
 * All the filenames must be given on the command line -- tls does not
 * even read directories, let alone recurse.  The typical usage is
 * "find|sort|xargs tls".
 *
 * The format is not exactly the same as any particular Unix ls(1).
 *
 * A key requirement for this program is that the output be "very
 * reproducible."  So we mask away information that can accidentally
 * change. */

#include "rsync.h"
#include <popt.h>
#include "lib/sysxattrs.h"

#define PROGRAM "tls"

/* These are to make syscall.o shut up. */
int dry_run = 0;
int am_root = 0;
int am_sender = 1;
int read_only = 1;
int list_only = 0;
int link_times = 0;
int link_owner = 0;
int nsec_times = 0;

#ifdef SUPPORT_XATTRS

#ifdef HAVE_LINUX_XATTRS
#define XSTAT_ATTR "user.rsync.%stat"
#else
#define XSTAT_ATTR "rsync.%stat"
#endif

static int stat_xattr(const char *fname, STRUCT_STAT *fst)
{
	unsigned int mode;
	int rdev_major, rdev_minor, uid, gid, len;
	char buf[256];

	if (am_root >= 0 || IS_DEVICE(fst->st_mode) || IS_SPECIAL(fst->st_mode))
		return -1;

	len = sys_lgetxattr(fname, XSTAT_ATTR, buf, sizeof buf - 1);
	if (len >= (int)sizeof buf) {
		len = -1;
		errno = ERANGE;
	}
	if (len < 0) {
		if (errno == ENOTSUP || errno == ENOATTR)
			return -1;
		if (errno == EPERM && S_ISLNK(fst->st_mode)) {
			fst->st_uid = 0;
			fst->st_gid = 0;
			return 0;
		}
		fprintf(stderr, "failed to read xattr %s for %s: %s\n",
			XSTAT_ATTR, fname, strerror(errno));
		return -1;
	}
	buf[len] = '\0';

	if (sscanf(buf, "%o %d,%d %d:%d",
		   &mode, &rdev_major, &rdev_minor, &uid, &gid) != 5) {
		fprintf(stderr, "Corrupt %s xattr attached to %s: \"%s\"\n",
			XSTAT_ATTR, fname, buf);
		exit(1);
	}

#if _S_IFLNK != 0120000
	if ((mode & (_S_IFMT)) == 0120000)
		mode = (mode & ~(_S_IFMT)) | _S_IFLNK;
#endif
	fst->st_mode = mode;

	fst->st_rdev = MAKEDEV(rdev_major, rdev_minor);
	fst->st_uid = uid;
	fst->st_gid = gid;

	return 0;
}

#endif

static int display_atimes = 0;
#ifdef SUPPORT_CRTIMES
static int display_crtimes = 0;
#endif

static void failed(char const *what, char const *where)
{
	fprintf(stderr, PROGRAM ": %s %s: %s\n",
		what, where, strerror(errno));
	exit(1);
}

static void storetime(char *dest, size_t destsize, time_t t, int nsecs)
{
	if (t) {
		int len;
		struct tm *mt = gmtime(&t);

		len = snprintf(dest, destsize,
			" %04d-%02d-%02d %02d:%02d:%02d",
			(int)mt->tm_year + 1900,
			(int)mt->tm_mon + 1,
			(int)mt->tm_mday,
			(int)mt->tm_hour,
			(int)mt->tm_min,
			(int)mt->tm_sec);
		if (nsecs >= 0 && len >= 0)
			snprintf(dest + len, destsize - len, ".%09d", nsecs);
	} else {
		int has_nsecs = nsecs >= 0 ? 1 : 0;
		int len = MIN(20 + 10*has_nsecs, (int)destsize - 1);
		memset(dest, ' ', len);
		dest[len] = '\0';
	}
}

static void list_file(const char *fname)
{
	STRUCT_STAT buf;
#ifdef SUPPORT_CRTIMES
	time_t crtime = 0;
#endif
	char permbuf[PERMSTRING_SIZE];
	char mtimebuf[50];
	char atimebuf[50];
	char crtimebuf[50];
	char linkbuf[4096];
	int nsecs;

	if (do_lstat(fname, &buf) < 0)
		failed("stat", fname);
#ifdef SUPPORT_CRTIMES
	if (display_crtimes && (crtime = get_create_time(fname)) == 0)
		failed("get_create_time", fname);
#endif
#ifdef SUPPORT_XATTRS
	if (am_root < 0)
		stat_xattr(fname, &buf);
#endif

	/* The size of anything but a regular file is probably not
	 * worth thinking about. */
	if (!S_ISREG(buf.st_mode))
		buf.st_size = 0;

	/* On some BSD platforms the mode bits of a symlink are
	 * undefined.  Also it tends not to be possible to reset a
	 * symlink's mtime, so we default to ignoring it too. */
	if (S_ISLNK(buf.st_mode)) {
		int len;
		buf.st_mode &= ~0777;
		if (!link_times)
			buf.st_mtime = (time_t)0;
		if (!link_owner)
			buf.st_uid = buf.st_gid = 0;
		strlcpy(linkbuf, " -> ", sizeof linkbuf);
		/* const-cast required for silly UNICOS headers */
		len = do_readlink((char*)fname, linkbuf+4, sizeof linkbuf - 4);
		if (len == -1)
			failed("do_readlink", fname);
		else
			/* it's not nul-terminated */
			linkbuf[4+len] = 0;
	} else {
		linkbuf[0] = '\0';
	}

	permstring(permbuf, buf.st_mode);
#ifdef ST_MTIME_NSEC
	if (nsec_times)
		nsecs = (int)buf.ST_MTIME_NSEC;
	else
#endif
		nsecs = -1;
	storetime(mtimebuf, sizeof mtimebuf, buf.st_mtime, nsecs);
	if (display_atimes)
		storetime(atimebuf, sizeof atimebuf, S_ISDIR(buf.st_mode) ? 0 : buf.st_atime, -1);
	else
		atimebuf[0] = '\0';
#ifdef SUPPORT_CRTIMES
	if (display_crtimes)
		storetime(crtimebuf, sizeof crtimebuf, crtime, -1);
	else
#endif
		crtimebuf[0] = '\0';

	/* TODO: Perhaps escape special characters in fname? */
	printf("%s ", permbuf);

	if (S_ISCHR(buf.st_mode) || S_ISBLK(buf.st_mode)) {
		printf("%5ld,%6ld", (long)major(buf.st_rdev), (long)minor(buf.st_rdev));
	} else
		printf("%15s", do_big_num(buf.st_size, 1, NULL));

	printf(" %6ld.%-6ld %6ld%s%s%s %s%s\n",
	       (long)buf.st_uid, (long)buf.st_gid, (long)buf.st_nlink,
	       mtimebuf, atimebuf, crtimebuf, fname, linkbuf);
}

static struct poptOption long_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"atimes",          'U', POPT_ARG_NONE,   &display_atimes, 0, 0, 0},
#ifdef SUPPORT_CRTIMES
  {"crtimes",         'N', POPT_ARG_NONE,   &display_crtimes, 0, 0, 0},
#endif
  {"link-times",      'l', POPT_ARG_NONE,   &link_times, 0, 0, 0 },
  {"link-owner",      'L', POPT_ARG_NONE,   &link_owner, 0, 0, 0 },
#ifdef SUPPORT_XATTRS
  {"fake-super",      'f', POPT_ARG_VAL,    &am_root, -1, 0, 0 },
#endif
#ifdef ST_MTIME_NSEC
  {"nsec",            's', POPT_ARG_NONE,   &nsec_times, 0, 0, 0 },
#endif
  {"help",            'h', POPT_ARG_NONE,   0, 'h', 0, 0 },
  {0,0,0,0,0,0,0}
};

static void NORETURN tls_usage(int ret)
{
  FILE *F = ret ? stderr : stdout;
  fprintf(F,"usage: " PROGRAM " [OPTIONS] FILE ...\n");
  fprintf(F,"Trivial file listing program for portably checking rsync\n");
  fprintf(F,"\nOptions:\n");
  fprintf(F," -U, --atimes                display access (last-used) times\n");
#ifdef SUPPORT_CRTIMES
  fprintf(F," -N, --crtimes               display create times (newness)\n");
#endif
  fprintf(F," -l, --link-times            display the time on a symlink\n");
  fprintf(F," -L, --link-owner            display the owner+group on a symlink\n");
#ifdef SUPPORT_XATTRS
  fprintf(F," -f, --fake-super            display attributes including fake-super xattrs\n");
#endif
  fprintf(F," -h, --help                  show this help\n");
  exit(ret);
}

int
main(int argc, char *argv[])
{
	poptContext pc;
	const char **extra_args;
	int opt;

	pc = poptGetContext(PROGRAM, argc, (const char **)argv, long_options, 0);
	while ((opt = poptGetNextOpt(pc)) != -1) {
		switch (opt) {
		case 'h':
			tls_usage(0);
		default:
			fprintf(stderr, "%s: %s\n",
				poptBadOption(pc, POPT_BADOPTION_NOALIAS),
				poptStrerror(opt));
			tls_usage(1);
		}
	}

	extra_args = poptGetArgs(pc);
	if (!extra_args || *extra_args == NULL)
		tls_usage(1);

	for (; *extra_args; extra_args++)
		list_file(*extra_args);
	poptFreeContext(pc);

	return 0;
}
