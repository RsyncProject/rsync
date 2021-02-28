/*
 * Utility routines used in rsync.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
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
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"
#include "ifuncs.h"
#include "itypes.h"
#include "inums.h"

extern int dry_run;
extern int module_id;
extern int protect_args;
extern int modify_window;
extern int relative_paths;
extern int preserve_times;
extern int preserve_xattrs;
extern int preallocate_files;
extern char *module_dir;
extern unsigned int module_dirlen;
extern char *partial_dir;
extern filter_rule_list daemon_filter_list;

int sanitize_paths = 0;

char curr_dir[MAXPATHLEN];
unsigned int curr_dir_len;
int curr_dir_depth; /* This is only set for a sanitizing daemon. */

/* Set a fd into nonblocking mode. */
void set_nonblocking(int fd)
{
	int val;

	if ((val = fcntl(fd, F_GETFL)) == -1)
		return;
	if (!(val & NONBLOCK_FLAG)) {
		val |= NONBLOCK_FLAG;
		fcntl(fd, F_SETFL, val);
	}
}

/* Set a fd into blocking mode. */
void set_blocking(int fd)
{
	int val;

	if ((val = fcntl(fd, F_GETFL)) == -1)
		return;
	if (val & NONBLOCK_FLAG) {
		val &= ~NONBLOCK_FLAG;
		fcntl(fd, F_SETFL, val);
	}
}

/**
 * Create a file descriptor pair - like pipe() but use socketpair if
 * possible (because of blocking issues on pipes).
 *
 * Always set non-blocking.
 */
int fd_pair(int fd[2])
{
	int ret;

#ifdef HAVE_SOCKETPAIR
	ret = socketpair(AF_UNIX, SOCK_STREAM, 0, fd);
#else
	ret = pipe(fd);
#endif

	if (ret == 0) {
		set_nonblocking(fd[0]);
		set_nonblocking(fd[1]);
	}

	return ret;
}

void print_child_argv(const char *prefix, char **cmd)
{
	int cnt = 0;
	rprintf(FCLIENT, "%s ", prefix);
	for (; *cmd; cmd++) {
		/* Look for characters that ought to be quoted.  This
		* is not a great quoting algorithm, but it's
		* sufficient for a log message. */
		if (strspn(*cmd, "abcdefghijklmnopqrstuvwxyz"
			   "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
			   "0123456789"
			   ",.-_=+@/") != strlen(*cmd)) {
			rprintf(FCLIENT, "\"%s\" ", *cmd);
		} else {
			rprintf(FCLIENT, "%s ", *cmd);
		}
		cnt++;
	}
	rprintf(FCLIENT, " (%d args)\n", cnt);
}

/* This returns 0 for success, 1 for a symlink if symlink time-setting
 * is not possible, or -1 for any other error. */
int set_times(const char *fname, STRUCT_STAT *stp)
{
	static int switch_step = 0;

	if (DEBUG_GTE(TIME, 1)) {
		rprintf(FINFO,
			"set modtime, atime of %s to (%ld) %s, (%ld) %s\n",
			fname, (long)stp->st_mtime,
			timestring(stp->st_mtime), (long)stp->st_atime, timestring(stp->st_atime));
	}

	switch (switch_step) {
#ifdef HAVE_SETATTRLIST
#include "case_N.h"
		if (do_setattrlist_times(fname, stp) == 0)
			break;
		if (errno != ENOSYS)
			return -1;
		switch_step++;
#endif

#ifdef HAVE_UTIMENSAT
#include "case_N.h"
		if (do_utimensat(fname, stp) == 0)
			break;
		if (errno != ENOSYS)
			return -1;
		switch_step++;
#endif

#ifdef HAVE_LUTIMES
#include "case_N.h"
		if (do_lutimes(fname, stp) == 0)
			break;
		if (errno != ENOSYS)
			return -1;
		switch_step++;
#endif

#include "case_N.h"
		switch_step++;
		if (preserve_times & PRESERVE_LINK_TIMES) {
			preserve_times &= ~PRESERVE_LINK_TIMES;
			if (S_ISLNK(stp->st_mode))
				return 1;
		}

#include "case_N.h"
#ifdef HAVE_UTIMES
		if (do_utimes(fname, stp) == 0)
			break;
#else
		if (do_utime(fname, stp) == 0)
			break;
#endif

		return -1;
	}

	return 0;
}

/* Create any necessary directories in fname.  Any missing directories are
 * created with default permissions.  Returns < 0 on error, or the number
 * of directories created. */
int make_path(char *fname, int flags)
{
	char *end, *p;
	int ret = 0;

	if (flags & MKP_SKIP_SLASH) {
		while (*fname == '/')
			fname++;
	}

	while (*fname == '.' && fname[1] == '/')
		fname += 2;

	if (flags & MKP_DROP_NAME) {
		end = strrchr(fname, '/');
		if (!end || end == fname)
			return 0;
		*end = '\0';
	} else
		end = fname + strlen(fname);

	/* Try to find an existing dir, starting from the deepest dir. */
	for (p = end; ; ) {
		if (dry_run) {
			STRUCT_STAT st;
			if (do_stat(fname, &st) == 0) {
				if (S_ISDIR(st.st_mode))
					errno = EEXIST;
				else
					errno = ENOTDIR;
			}
		} else if (do_mkdir(fname, ACCESSPERMS) == 0) {
			ret++;
			break;
		}

		if (errno != ENOENT) {
			STRUCT_STAT st;
			if (errno != EEXIST || (do_stat(fname, &st) == 0 && !S_ISDIR(st.st_mode)))
				ret = -ret - 1;
			break;
		}
		while (1) {
			if (p == fname) {
				/* We got a relative path that doesn't exist, so assume that '.'
				 * is there and just break out and create the whole thing. */
				p = NULL;
				goto double_break;
			}
			if (*--p == '/') {
				if (p == fname) {
					/* We reached the "/" dir, which we assume is there. */
					goto double_break;
				}
				*p = '\0';
				break;
			}
		}
	}
  double_break:

	/* Make all the dirs that we didn't find on the way here. */
	while (p != end) {
		if (p)
			*p = '/';
		else
			p = fname;
		p += strlen(p);
		if (ret < 0) /* Skip mkdir on error, but keep restoring the path. */
			continue;
		if (do_mkdir(fname, ACCESSPERMS) < 0)
			ret = -ret - 1;
		else
			ret++;
	}

	if (flags & MKP_DROP_NAME)
		*end = '/';

	return ret;
}

/**
 * Write @p len bytes at @p ptr to descriptor @p desc, retrying if
 * interrupted.
 *
 * @retval len upon success
 *
 * @retval <0 write's (negative) error code
 *
 * Derived from GNU C's cccp.c.
 */
int full_write(int desc, const char *ptr, size_t len)
{
	int total_written;

	total_written = 0;
	while (len > 0) {
		int written = write(desc, ptr, len);
		if (written < 0)  {
			if (errno == EINTR)
				continue;
			return written;
		}
		total_written += written;
		ptr += written;
		len -= written;
	}
	return total_written;
}

/**
 * Read @p len bytes at @p ptr from descriptor @p desc, retrying if
 * interrupted.
 *
 * @retval >0 the actual number of bytes read
 *
 * @retval 0 for EOF
 *
 * @retval <0 for an error.
 *
 * Derived from GNU C's cccp.c. */
static int safe_read(int desc, char *ptr, size_t len)
{
	int n_chars;

	if (len == 0)
		return len;

	do {
		n_chars = read(desc, ptr, len);
	} while (n_chars < 0 && errno == EINTR);

	return n_chars;
}

/* Copy a file.  If ofd < 0, copy_file unlinks and opens the "dest" file.
 * Otherwise, it just writes to and closes the provided file descriptor.
 * In either case, if --xattrs are being preserved, the dest file will
 * have its xattrs set from the source file.
 *
 * This is used in conjunction with the --temp-dir, --backup, and
 * --copy-dest options. */
int copy_file(const char *source, const char *dest, int ofd, mode_t mode)
{
	int ifd;
	char buf[1024 * 8];
	int len;   /* Number of bytes read into `buf'. */
	OFF_T prealloc_len = 0, offset = 0;

	if ((ifd = do_open(source, O_RDONLY, 0)) < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "open %s", full_fname(source));
		errno = save_errno;
		return -1;
	}

	if (ofd < 0) {
		if (robust_unlink(dest) && errno != ENOENT) {
			int save_errno = errno;
			rsyserr(FERROR_XFER, errno, "unlink %s", full_fname(dest));
			close(ifd);
			errno = save_errno;
			return -1;
		}

#ifdef SUPPORT_XATTRS
		if (preserve_xattrs)
			mode |= S_IWUSR;
#endif
		mode &= INITACCESSPERMS;
		if ((ofd = do_open(dest, O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, mode)) < 0) {
			int save_errno = errno;
			rsyserr(FERROR_XFER, save_errno, "open %s", full_fname(dest));
			close(ifd);
			errno = save_errno;
			return -1;
		}
	}

#ifdef SUPPORT_PREALLOCATION
	if (preallocate_files) {
		STRUCT_STAT srcst;

		/* Try to preallocate enough space for file's eventual length.  Can
		 * reduce fragmentation on filesystems like ext4, xfs, and NTFS. */
		if (do_fstat(ifd, &srcst) < 0)
			rsyserr(FWARNING, errno, "fstat %s", full_fname(source));
		else if (srcst.st_size > 0) {
			prealloc_len = do_fallocate(ofd, 0, srcst.st_size);
			if (prealloc_len < 0)
				rsyserr(FWARNING, errno, "do_fallocate %s", full_fname(dest));
		}
	}
#endif

	while ((len = safe_read(ifd, buf, sizeof buf)) > 0) {
		if (full_write(ofd, buf, len) < 0) {
			int save_errno = errno;
			rsyserr(FERROR_XFER, errno, "write %s", full_fname(dest));
			close(ifd);
			close(ofd);
			errno = save_errno;
			return -1;
		}
		offset += len;
	}

	if (len < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "read %s", full_fname(source));
		close(ifd);
		close(ofd);
		errno = save_errno;
		return -1;
	}

	if (close(ifd) < 0) {
		rsyserr(FWARNING, errno, "close failed on %s",
			full_fname(source));
	}

	/* Source file might have shrunk since we fstatted it.
	 * Cut off any extra preallocated zeros from dest file. */
	if (offset < prealloc_len && do_ftruncate(ofd, offset) < 0) {
		/* If we fail to truncate, the dest file may be wrong, so we
		 * must trigger the "partial transfer" error. */
		rsyserr(FERROR_XFER, errno, "ftruncate %s", full_fname(dest));
	}

	if (close(ofd) < 0) {
		int save_errno = errno;
		rsyserr(FERROR_XFER, errno, "close failed on %s",
			full_fname(dest));
		errno = save_errno;
		return -1;
	}

#ifdef SUPPORT_XATTRS
	if (preserve_xattrs)
		copy_xattrs(source, dest);
#endif

	return 0;
}

/* MAX_RENAMES should be 10**MAX_RENAMES_DIGITS */
#define MAX_RENAMES_DIGITS 3
#define MAX_RENAMES 1000

/**
 * Robust unlink: some OS'es (HPUX) refuse to unlink busy files, so
 * rename to <path>/.rsyncNNN instead.
 *
 * Note that successive rsync runs will shuffle the filenames around a
 * bit as long as the file is still busy; this is because this function
 * does not know if the unlink call is due to a new file coming in, or
 * --delete trying to remove old .rsyncNNN files, hence it renames it
 * each time.
 **/
int robust_unlink(const char *fname)
{
#ifndef ETXTBSY
	return do_unlink(fname);
#else
	static int counter = 1;
	int rc, pos, start;
	char path[MAXPATHLEN];

	rc = do_unlink(fname);
	if (rc == 0 || errno != ETXTBSY)
		return rc;

	if ((pos = strlcpy(path, fname, MAXPATHLEN)) >= MAXPATHLEN)
		pos = MAXPATHLEN - 1;

	while (pos > 0 && path[pos-1] != '/')
		pos--;
	pos += strlcpy(path+pos, ".rsync", MAXPATHLEN-pos);

	if (pos > (MAXPATHLEN-MAX_RENAMES_DIGITS-1)) {
		errno = ETXTBSY;
		return -1;
	}

	/* start where the last one left off to reduce chance of clashes */
	start = counter;
	do {
		snprintf(&path[pos], MAX_RENAMES_DIGITS+1, "%03d", counter);
		if (++counter >= MAX_RENAMES)
			counter = 1;
	} while ((rc = access(path, 0)) == 0 && counter != start);

	if (INFO_GTE(MISC, 1)) {
		rprintf(FWARNING, "renaming %s to %s because of text busy\n",
			fname, path);
	}

	/* maybe we should return rename()'s exit status? Nah. */
	if (do_rename(fname, path) != 0) {
		errno = ETXTBSY;
		return -1;
	}
	return 0;
#endif
}

/* Returns 0 on successful rename, 1 if we successfully copied the file
 * across filesystems, -2 if copy_file() failed, and -1 on other errors.
 * If partialptr is not NULL and we need to do a copy, copy the file into
 * the active partial-dir instead of over the destination file. */
int robust_rename(const char *from, const char *to, const char *partialptr,
		  int mode)
{
	int tries = 4;

	/* A resumed in-place partial-dir transfer might call us with from and
	 * to pointing to the same buf if the transfer failed yet again. */
	if (from == to)
		return 0;

	while (tries--) {
		if (do_rename(from, to) == 0)
			return 0;

		switch (errno) {
#ifdef ETXTBSY
		case ETXTBSY:
			if (robust_unlink(to) != 0) {
				errno = ETXTBSY;
				return -1;
			}
			errno = ETXTBSY;
			break;
#endif
		case EXDEV:
			if (partialptr) {
				if (!handle_partial_dir(partialptr,PDIR_CREATE))
					return -2;
				to = partialptr;
			}
			if (copy_file(from, to, -1, mode) != 0)
				return -2;
			do_unlink(from);
			return 1;
		default:
			return -1;
		}
	}
	return -1;
}

static pid_t all_pids[10];
static int num_pids;

/** Fork and record the pid of the child. **/
pid_t do_fork(void)
{
	pid_t newpid = fork();

	if (newpid != 0  &&  newpid != -1) {
		all_pids[num_pids++] = newpid;
	}
	return newpid;
}

/**
 * Kill all children.
 *
 * @todo It would be kind of nice to make sure that they are actually
 * all our children before we kill them, because their pids may have
 * been recycled by some other process.  Perhaps when we wait for a
 * child, we should remove it from this array.  Alternatively we could
 * perhaps use process groups, but I think that would not work on
 * ancient Unix versions that don't support them.
 **/
void kill_all(int sig)
{
	int i;

	for (i = 0; i < num_pids; i++) {
		/* Let's just be a little careful where we
		 * point that gun, hey?  See kill(2) for the
		 * magic caused by negative values. */
		pid_t p = all_pids[i];

		if (p == getpid())
			continue;
		if (p <= 0)
			continue;

		kill(p, sig);
	}
}

/** Lock a byte range in a open file */
int lock_range(int fd, int offset, int len)
{
	struct flock lock;

	lock.l_type = F_WRLCK;
	lock.l_whence = SEEK_SET;
	lock.l_start = offset;
	lock.l_len = len;
	lock.l_pid = 0;

	return fcntl(fd,F_SETLK,&lock) == 0;
}

#define ENSURE_MEMSPACE(buf, type, sz, req) \
	do { if ((req) > sz) buf = realloc_array(buf, type, sz = MAX(sz * 2, req)); } while(0)

static inline void call_glob_match(const char *name, int len, int from_glob,
				   char *arg, int abpos, int fbpos);

static struct glob_data {
	char *arg_buf, *filt_buf, **argv;
	int absize, fbsize, maxargs, argc;
} glob;

static void glob_match(char *arg, int abpos, int fbpos)
{
	int len;
	char *slash;

	while (*arg == '.' && arg[1] == '/') {
		if (fbpos < 0) {
			ENSURE_MEMSPACE(glob.filt_buf, char, glob.fbsize, glob.absize);
			memcpy(glob.filt_buf, glob.arg_buf, abpos + 1);
			fbpos = abpos;
		}
		ENSURE_MEMSPACE(glob.arg_buf, char, glob.absize, abpos + 3);
		glob.arg_buf[abpos++] = *arg++;
		glob.arg_buf[abpos++] = *arg++;
		glob.arg_buf[abpos] = '\0';
	}
	if ((slash = strchr(arg, '/')) != NULL) {
		*slash = '\0';
		len = slash - arg;
	} else
		len = strlen(arg);
	if (strpbrk(arg, "*?[")) {
		struct dirent *di;
		DIR *d;

		if (!(d = opendir(abpos ? glob.arg_buf : ".")))
			return;
		while ((di = readdir(d)) != NULL) {
			char *dname = d_name(di);
			if (dname[0] == '.' && (dname[1] == '\0'
			  || (dname[1] == '.' && dname[2] == '\0')))
				continue;
			if (!wildmatch(arg, dname))
				continue;
			call_glob_match(dname, strlen(dname), 1,
					slash ? arg + len + 1 : NULL,
					abpos, fbpos);
		}
		closedir(d);
	} else {
		call_glob_match(arg, len, 0,
				slash ? arg + len + 1 : NULL,
				abpos, fbpos);
	}
	if (slash)
		*slash = '/';
}

static inline void call_glob_match(const char *name, int len, int from_glob,
				   char *arg, int abpos, int fbpos)
{
	char *use_buf;

	ENSURE_MEMSPACE(glob.arg_buf, char, glob.absize, abpos + len + 2);
	memcpy(glob.arg_buf + abpos, name, len);
	abpos += len;
	glob.arg_buf[abpos] = '\0';

	if (fbpos >= 0) {
		ENSURE_MEMSPACE(glob.filt_buf, char, glob.fbsize, fbpos + len + 2);
		memcpy(glob.filt_buf + fbpos, name, len);
		fbpos += len;
		glob.filt_buf[fbpos] = '\0';
		use_buf = glob.filt_buf;
	} else
		use_buf = glob.arg_buf;

	if (from_glob || (arg && len)) {
		STRUCT_STAT st;
		int is_dir;

		if (do_stat(glob.arg_buf, &st) != 0)
			return;
		is_dir = S_ISDIR(st.st_mode) != 0;
		if (arg && !is_dir)
			return;

		if (daemon_filter_list.head
		 && check_filter(&daemon_filter_list, FLOG, use_buf, is_dir) < 0)
			return;
	}

	if (arg) {
		glob.arg_buf[abpos++] = '/';
		glob.arg_buf[abpos] = '\0';
		if (fbpos >= 0) {
			glob.filt_buf[fbpos++] = '/';
			glob.filt_buf[fbpos] = '\0';
		}
		glob_match(arg, abpos, fbpos);
	} else {
		ENSURE_MEMSPACE(glob.argv, char *, glob.maxargs, glob.argc + 1);
		glob.argv[glob.argc++] = strdup(glob.arg_buf);
	}
}

/* This routine performs wild-card expansion of the pathname in "arg".  Any
 * daemon-excluded files/dirs will not be matched by the wildcards.  Returns 0
 * if a wild-card string is the only returned item (due to matching nothing). */
int glob_expand(const char *arg, char ***argv_p, int *argc_p, int *maxargs_p)
{
	int ret, save_argc;
	char *s;

	if (!arg) {
		if (glob.filt_buf)
			free(glob.filt_buf);
		free(glob.arg_buf);
		memset(&glob, 0, sizeof glob);
		return -1;
	}

	if (sanitize_paths)
		s = sanitize_path(NULL, arg, "", 0, SP_KEEP_DOT_DIRS);
	else {
		s = strdup(arg);
		clean_fname(s, CFN_KEEP_DOT_DIRS | CFN_KEEP_TRAILING_SLASH | CFN_COLLAPSE_DOT_DOT_DIRS);
	}

	ENSURE_MEMSPACE(glob.arg_buf, char, glob.absize, MAXPATHLEN);
	*glob.arg_buf = '\0';

	glob.argc = save_argc = *argc_p;
	glob.argv = *argv_p;
	glob.maxargs = *maxargs_p;

	ENSURE_MEMSPACE(glob.argv, char *, glob.maxargs, 100);

	glob_match(s, 0, -1);

	/* The arg didn't match anything, so add the failed arg to the list. */
	if (glob.argc == save_argc) {
		ENSURE_MEMSPACE(glob.argv, char *, glob.maxargs, glob.argc + 1);
		glob.argv[glob.argc++] = s;
		ret = 0;
	} else {
		free(s);
		ret = 1;
	}

	*maxargs_p = glob.maxargs;
	*argv_p = glob.argv;
	*argc_p = glob.argc;

	return ret;
}

/* This routine is only used in daemon mode. */
void glob_expand_module(char *base1, char *arg, char ***argv_p, int *argc_p, int *maxargs_p)
{
	char *p, *s;
	char *base = base1;
	int base_len = strlen(base);

	if (!arg || !*arg)
		return;

	if (strncmp(arg, base, base_len) == 0)
		arg += base_len;

	if (protect_args) {
		glob_expand(arg, argv_p, argc_p, maxargs_p);
		return;
	}

	arg = strdup(arg);

	if (asprintf(&base," %s/", base1) < 0)
		out_of_memory("glob_expand_module");
	base_len++;

	for (s = arg; *s; s = p + base_len) {
		if ((p = strstr(s, base)) != NULL)
			*p = '\0'; /* split it at this point */
		glob_expand(s, argv_p, argc_p, maxargs_p);
		if (!p)
			break;
	}

	free(arg);
	free(base);
}

/**
 * Convert a string to lower case
 **/
void strlower(char *s)
{
	while (*s) {
		if (isUpper(s))
			*s = toLower(s);
		s++;
	}
}

/**
 * Split a string into tokens based (usually) on whitespace & commas.  If the
 * string starts with a comma (after skipping any leading whitespace), then
 * splitting is done only on commas. No empty tokens are ever returned. */
char *conf_strtok(char *str)
{
	static int commas_only = 0;

	if (str) {
		while (isSpace(str)) str++;
		if (*str == ',') {
			commas_only = 1;
			str++;
		} else
			commas_only = 0;
	}

	while (commas_only) {
		char *end, *tok = strtok(str, ",");
		if (!tok)
			return NULL;
		/* Trim just leading and trailing whitespace. */
		while (isSpace(tok))
			tok++;
		end = tok + strlen(tok);
		while (end > tok && isSpace(end-1))
			*--end = '\0';
		if (*tok)
			return tok;
		str = NULL;
	}

	return strtok(str, " ,\t\r\n");
}

/* Join strings p1 & p2 into "dest" with a guaranteed '/' between them.  (If
 * p1 ends with a '/', no extra '/' is inserted.)  Returns the length of both
 * strings + 1 (if '/' was inserted), regardless of whether the null-terminated
 * string fits into destsize. */
size_t pathjoin(char *dest, size_t destsize, const char *p1, const char *p2)
{
	size_t len = strlcpy(dest, p1, destsize);
	if (len < destsize - 1) {
		if (!len || dest[len-1] != '/')
			dest[len++] = '/';
		if (len < destsize - 1)
			len += strlcpy(dest + len, p2, destsize - len);
		else {
			dest[len] = '\0';
			len += strlen(p2);
		}
	}
	else
		len += strlen(p2) + 1; /* Assume we'd insert a '/'. */
	return len;
}

/* Join any number of strings together, putting them in "dest".  The return
 * value is the length of all the strings, regardless of whether the null-
 * terminated whole fits in destsize.  Your list of string pointers must end
 * with a NULL to indicate the end of the list. */
size_t stringjoin(char *dest, size_t destsize, ...)
{
	va_list ap;
	size_t len, ret = 0;
	const char *src;

	va_start(ap, destsize);
	while (1) {
		if (!(src = va_arg(ap, const char *)))
			break;
		len = strlen(src);
		ret += len;
		if (destsize > 1) {
			if (len >= destsize)
				len = destsize - 1;
			memcpy(dest, src, len);
			destsize -= len;
			dest += len;
		}
	}
	*dest = '\0';
	va_end(ap);

	return ret;
}

int count_dir_elements(const char *p)
{
	int cnt = 0, new_component = 1;
	while (*p) {
		if (*p++ == '/')
			new_component = (*p != '.' || (p[1] != '/' && p[1] != '\0'));
		else if (new_component) {
			new_component = 0;
			cnt++;
		}
	}
	return cnt;
}

/* Turns multiple adjacent slashes into a single slash (possible exception:
 * the preserving of two leading slashes at the start), drops all leading or
 * interior "." elements unless CFN_KEEP_DOT_DIRS is flagged.  Will also drop
 * a trailing '.' after a '/' if CFN_DROP_TRAILING_DOT_DIR is flagged, removes
 * a trailing slash (perhaps after removing the aforementioned dot) unless
 * CFN_KEEP_TRAILING_SLASH is flagged, and will also collapse ".." elements
 * (except at the start) if CFN_COLLAPSE_DOT_DOT_DIRS is flagged.  If the
 * resulting name would be empty, returns ".". */
int clean_fname(char *name, int flags)
{
	char *limit = name - 1, *t = name, *f = name;
	int anchored;

	if (!name)
		return 0;

#define DOT_IS_DOT_DOT_DIR(bp) (bp[1] == '.' && (bp[2] == '/' || !bp[2]))

	if ((anchored = *f == '/') != 0) {
		*t++ = *f++;
#ifdef __CYGWIN__
		/* If there are exactly 2 slashes at the start, preserve
		 * them.  Would break daemon excludes unless the paths are
		 * really treated differently, so used this sparingly. */
		if (*f == '/' && f[1] != '/')
			*t++ = *f++;
#endif
	} else if (flags & CFN_KEEP_DOT_DIRS && *f == '.' && f[1] == '/') {
		*t++ = *f++;
		*t++ = *f++;
	} else if (flags & CFN_REFUSE_DOT_DOT_DIRS && *f == '.' && DOT_IS_DOT_DOT_DIR(f))
		return -1;
	while (*f) {
		/* discard extra slashes */
		if (*f == '/') {
			f++;
			continue;
		}
		if (*f == '.') {
			/* discard interior "." dirs */
			if (f[1] == '/' && !(flags & CFN_KEEP_DOT_DIRS)) {
				f += 2;
				continue;
			}
			if (f[1] == '\0' && flags & CFN_DROP_TRAILING_DOT_DIR)
				break;
			/* collapse ".." dirs */
			if (flags & (CFN_COLLAPSE_DOT_DOT_DIRS|CFN_REFUSE_DOT_DOT_DIRS) && DOT_IS_DOT_DOT_DIR(f)) {
				char *s = t - 1;
				if (flags & CFN_REFUSE_DOT_DOT_DIRS)
					return -1;
				if (s == name && anchored) {
					f += 2;
					continue;
				}
				while (s > limit && *--s != '/') {}
				if (s != t - 1 && (s < name || *s == '/')) {
					t = s + 1;
					f += 2;
					continue;
				}
				limit = t + 2;
			}
		}
		while (*f && (*t++ = *f++) != '/') {}
	}

	if (t > name+anchored && t[-1] == '/' && !(flags & CFN_KEEP_TRAILING_SLASH))
		t--;
	if (t == name)
		*t++ = '.';
	*t = '\0';

#undef DOT_IS_DOT_DOT_DIR

	return t - name;
}

/* Make path appear as if a chroot had occurred.  This handles a leading
 * "/" (either removing it or expanding it) and any leading or embedded
 * ".." components that attempt to escape past the module's top dir.
 *
 * If dest is NULL, a buffer is allocated to hold the result.  It is legal
 * to call with the dest and the path (p) pointing to the same buffer, but
 * rootdir will be ignored to avoid expansion of the string.
 *
 * The rootdir string contains a value to use in place of a leading slash.
 * Specify NULL to get the default of "module_dir".
 *
 * The depth var is a count of how many '..'s to allow at the start of the
 * path.
 *
 * We also clean the path in a manner similar to clean_fname() but with a
 * few differences:
 *
 * Turns multiple adjacent slashes into a single slash, gets rid of "." dir
 * elements (INCLUDING a trailing dot dir), PRESERVES a trailing slash, and
 * ALWAYS collapses ".." elements (except for those at the start of the
 * string up to "depth" deep).  If the resulting name would be empty,
 * change it into a ".". */
char *sanitize_path(char *dest, const char *p, const char *rootdir, int depth, int flags)
{
	char *start, *sanp;
	int rlen = 0, drop_dot_dirs = !relative_paths || !(flags & SP_KEEP_DOT_DIRS);

	if (dest != p) {
		int plen = strlen(p); /* the path len INCLUDING any separating slash */
		if (*p == '/') {
			if (!rootdir)
				rootdir = module_dir;
			rlen = strlen(rootdir);
			depth = 0;
			p++;
		}
		if (!dest)
			dest = new_array(char, MAX(rlen + plen + 1, 2));
		else if (rlen + plen + 1 >= MAXPATHLEN)
			return NULL;
		if (rlen) { /* only true if p previously started with a slash */
			memcpy(dest, rootdir, rlen);
			if (rlen > 1) /* a rootdir of len 1 is "/", so this avoids a 2nd slash */
				dest[rlen++] = '/';
		}
	}

	if (drop_dot_dirs) {
		while (*p == '.' && p[1] == '/')
			p += 2;
	}

	start = sanp = dest + rlen;
	/* This loop iterates once per filename component in p, pointing at
	 * the start of the name (past any prior slash) for each iteration. */
	while (*p) {
		/* discard leading or extra slashes */
		if (*p == '/') {
			p++;
			continue;
		}
		if (drop_dot_dirs) {
			if (*p == '.' && (p[1] == '/' || p[1] == '\0')) {
				/* skip "." component */
				p++;
				continue;
			}
		}
		if (*p == '.' && p[1] == '.' && (p[2] == '/' || p[2] == '\0')) {
			/* ".." component followed by slash or end */
			if (depth <= 0 || sanp != start) {
				p += 2;
				if (sanp != start) {
					/* back up sanp one level */
					--sanp; /* now pointing at slash */
					while (sanp > start && sanp[-1] != '/')
						sanp--;
				}
				continue;
			}
			/* allow depth levels of .. at the beginning */
			depth--;
			/* move the virtual beginning to leave the .. alone */
			start = sanp + 3;
		}
		/* copy one component through next slash */
		while (*p && (*sanp++ = *p++) != '/') {}
	}
	if (sanp == dest) {
		/* ended up with nothing, so put in "." component */
		*sanp++ = '.';
	}
	*sanp = '\0';

	return dest;
}

/* Like chdir(), but it keeps track of the current directory (in the
 * global "curr_dir"), and ensures that the path size doesn't overflow.
 * Also cleans the path using the clean_fname() function. */
int change_dir(const char *dir, int set_path_only)
{
	static int initialised, skipped_chdir;
	unsigned int len;

	if (!initialised) {
		initialised = 1;
		if (getcwd(curr_dir, sizeof curr_dir - 1) == NULL) {
			rsyserr(FERROR, errno, "getcwd()");
			exit_cleanup(RERR_FILESELECT);
		}
		curr_dir_len = strlen(curr_dir);
	}

	if (!dir)	/* this call was probably just to initialize */
		return 0;

	len = strlen(dir);
	if (len == 1 && *dir == '.' && (!skipped_chdir || set_path_only))
		return 1;

	if (*dir == '/') {
		if (len >= sizeof curr_dir) {
			errno = ENAMETOOLONG;
			return 0;
		}
		if (!set_path_only && chdir(dir))
			return 0;
		skipped_chdir = set_path_only;
		memcpy(curr_dir, dir, len + 1);
	} else {
		unsigned int save_dir_len = curr_dir_len;
		if (curr_dir_len + 1 + len >= sizeof curr_dir) {
			errno = ENAMETOOLONG;
			return 0;
		}
		if (!(curr_dir_len && curr_dir[curr_dir_len-1] == '/'))
			curr_dir[curr_dir_len++] = '/';
		memcpy(curr_dir + curr_dir_len, dir, len + 1);

		if (!set_path_only && chdir(curr_dir)) {
			curr_dir_len = save_dir_len;
			curr_dir[curr_dir_len] = '\0';
			return 0;
		}
		skipped_chdir = set_path_only;
	}

	curr_dir_len = clean_fname(curr_dir, CFN_COLLAPSE_DOT_DOT_DIRS | CFN_DROP_TRAILING_DOT_DIR);
	if (sanitize_paths) {
		if (module_dirlen > curr_dir_len)
			module_dirlen = curr_dir_len;
		curr_dir_depth = count_dir_elements(curr_dir + module_dirlen);
	}

	if (DEBUG_GTE(CHDIR, 1) && !set_path_only)
		rprintf(FINFO, "[%s] change_dir(%s)\n", who_am_i(), curr_dir);

	return 1;
}

/* This will make a relative path absolute and clean it up via clean_fname().
 * Returns the string, which might be newly allocated, or NULL on error. */
char *normalize_path(char *path, BOOL force_newbuf, unsigned int *len_ptr)
{
	unsigned int len;

	if (*path != '/') { /* Make path absolute. */
		int len = strlen(path);
		if (curr_dir_len + 1 + len >= sizeof curr_dir)
			return NULL;
		curr_dir[curr_dir_len] = '/';
		memcpy(curr_dir + curr_dir_len + 1, path, len + 1);
		path = strdup(curr_dir);
		curr_dir[curr_dir_len] = '\0';
	} else if (force_newbuf)
		path = strdup(path);

	len = clean_fname(path, CFN_COLLAPSE_DOT_DOT_DIRS | CFN_DROP_TRAILING_DOT_DIR);

	if (len_ptr)
		*len_ptr = len;

	return path;
}

/**
 * Return a quoted string with the full pathname of the indicated filename.
 * The string " (in MODNAME)" may also be appended.  The returned pointer
 * remains valid until the next time full_fname() is called.
 **/
char *full_fname(const char *fn)
{
	static char *result = NULL;
	char *m1, *m2, *m3;
	char *p1, *p2;

	if (result)
		free(result);

	if (*fn == '/')
		p1 = p2 = "";
	else {
		p1 = curr_dir + module_dirlen;
		for (p2 = p1; *p2 == '/'; p2++) {}
		if (*p2)
			p2 = "/";
	}
	if (module_id >= 0) {
		m1 = " (in ";
		m2 = lp_name(module_id);
		m3 = ")";
	} else
		m1 = m2 = m3 = "";

	if (asprintf(&result, "\"%s%s%s\"%s%s%s", p1, p2, fn, m1, m2, m3) < 0)
		out_of_memory("full_fname");

	return result;
}

static char partial_fname[MAXPATHLEN];

char *partial_dir_fname(const char *fname)
{
	char *t = partial_fname;
	int sz = sizeof partial_fname;
	const char *fn;

	if ((fn = strrchr(fname, '/')) != NULL) {
		fn++;
		if (*partial_dir != '/') {
			int len = fn - fname;
			strncpy(t, fname, len); /* safe */
			t += len;
			sz -= len;
		}
	} else
		fn = fname;
	if ((int)pathjoin(t, sz, partial_dir, fn) >= sz)
		return NULL;
	if (daemon_filter_list.head) {
		t = strrchr(partial_fname, '/');
		*t = '\0';
		if (check_filter(&daemon_filter_list, FLOG, partial_fname, 1) < 0)
			return NULL;
		*t = '/';
		if (check_filter(&daemon_filter_list, FLOG, partial_fname, 0) < 0)
			return NULL;
	}

	return partial_fname;
}

/* If no --partial-dir option was specified, we don't need to do anything
 * (the partial-dir is essentially '.'), so just return success. */
int handle_partial_dir(const char *fname, int create)
{
	char *fn, *dir;

	if (fname != partial_fname)
		return 1;
	if (!create && *partial_dir == '/')
		return 1;
	if (!(fn = strrchr(partial_fname, '/')))
		return 1;

	*fn = '\0';
	dir = partial_fname;
	if (create) {
		STRUCT_STAT st;
		int statret = do_lstat(dir, &st);
		if (statret == 0 && !S_ISDIR(st.st_mode)) {
			if (do_unlink(dir) < 0) {
				*fn = '/';
				return 0;
			}
			statret = -1;
		}
		if (statret < 0 && do_mkdir(dir, 0700) < 0) {
			*fn = '/';
			return 0;
		}
	} else
		do_rmdir(dir);
	*fn = '/';

	return 1;
}

/* Determine if a symlink points outside the current directory tree.
 * This is considered "unsafe" because e.g. when mirroring somebody
 * else's machine it might allow them to establish a symlink to
 * /etc/passwd, and then read it through a web server.
 *
 * Returns 1 if unsafe, 0 if safe.
 *
 * Null symlinks and absolute symlinks are always unsafe.
 *
 * Basically here we are concerned with symlinks whose target contains
 * "..", because this might cause us to walk back up out of the
 * transferred directory.  We are not allowed to go back up and
 * reenter.
 *
 * "dest" is the target of the symlink in question.
 *
 * "src" is the top source directory currently applicable at the level
 * of the referenced symlink.  This is usually the symlink's full path
 * (including its name), as referenced from the root of the transfer. */
int unsafe_symlink(const char *dest, const char *src)
{
	const char *name, *slash;
	int depth = 0;

	/* all absolute and null symlinks are unsafe */
	if (!dest || !*dest || *dest == '/')
		return 1;

	/* find out what our safety margin is */
	for (name = src; (slash = strchr(name, '/')) != 0; name = slash+1) {
		/* ".." segment starts the count over.  "." segment is ignored. */
		if (*name == '.' && (name[1] == '/' || (name[1] == '.' && name[2] == '/'))) {
			if (name[1] == '.')
				depth = 0;
		} else
			depth++;
		while (slash[1] == '/') slash++; /* just in case src isn't clean */
	}
	if (*name == '.' && name[1] == '.' && name[2] == '\0')
		depth = 0;

	for (name = dest; (slash = strchr(name, '/')) != 0; name = slash+1) {
		if (*name == '.' && (name[1] == '/' || (name[1] == '.' && name[2] == '/'))) {
			if (name[1] == '.') {
				/* if at any point we go outside the current directory
				   then stop - it is unsafe */
				if (--depth < 0)
					return 1;
			}
		} else
			depth++;
		while (slash[1] == '/') slash++;
	}
	if (*name == '.' && name[1] == '.' && name[2] == '\0')
		depth--;

	return depth < 0;
}

/* Return the date and time as a string.  Some callers tweak returned buf. */
char *timestring(time_t t)
{
	static int ndx = 0;
	static char buffers[4][20]; /* We support 4 simultaneous timestring results. */
	char *TimeBuf = buffers[ndx = (ndx + 1) % 4];
	struct tm *tm = localtime(&t);
	int len = snprintf(TimeBuf, sizeof buffers[0], "%4d/%02d/%02d %02d:%02d:%02d",
		 (int)tm->tm_year + 1900, (int)tm->tm_mon + 1, (int)tm->tm_mday,
		 (int)tm->tm_hour, (int)tm->tm_min, (int)tm->tm_sec);
	assert(len > 0); /* Silence gcc warning */

	return TimeBuf;
}

/* Determine if two time_t values are equivalent (either exact, or in
 * the modification timestamp window established by --modify-window).
 * Returns 1 if the times the "same", or 0 if they are different. */
int same_time(time_t f1_sec, unsigned long f1_nsec, time_t f2_sec, unsigned long f2_nsec)
{
	if (modify_window == 0)
		return f1_sec == f2_sec;
	if (modify_window < 0)
		return f1_sec == f2_sec && f1_nsec == f2_nsec;
	/* The nano seconds doesn't figure into these checks -- time windows don't care about that. */
	if (f2_sec > f1_sec)
		return f2_sec - f1_sec <= modify_window;
	return f1_sec - f2_sec <= modify_window;
}

#ifdef __INSURE__XX
#include <dlfcn.h>

/**
   This routine is a trick to immediately catch errors when debugging
   with insure. A xterm with a gdb is popped up when insure catches
   a error. It is Linux specific.
**/
int _Insure_trap_error(int a1, int a2, int a3, int a4, int a5, int a6)
{
	static int (*fn)();
	int ret, pid_int = getpid();
	char *cmd;

	if (asprintf(&cmd,
	    "/usr/X11R6/bin/xterm -display :0 -T Panic -n Panic -e /bin/sh -c 'cat /tmp/ierrs.*.%d ; "
	    "gdb /proc/%d/exe %d'", pid_int, pid_int, pid_int) < 0)
		return -1;

	if (!fn) {
		static void *h;
		h = dlopen("/usr/local/parasoft/insure++lite/lib.linux2/libinsure.so", RTLD_LAZY);
		fn = dlsym(h, "_Insure_trap_error");
	}

	ret = fn(a1, a2, a3, a4, a5, a6);

	system(cmd);

	free(cmd);

	return ret;
}
#endif

/* Take a filename and filename length and return the most significant
 * filename suffix we can find.  This ignores suffixes such as "~",
 * ".bak", ".orig", ".~1~", etc. */
const char *find_filename_suffix(const char *fn, int fn_len, int *len_ptr)
{
	const char *suf, *s;
	BOOL had_tilde;
	int s_len;

	/* One or more dots at the start aren't a suffix. */
	while (fn_len && *fn == '.') fn++, fn_len--;

	/* Ignore the ~ in a "foo~" filename. */
	if (fn_len > 1 && fn[fn_len-1] == '~')
		fn_len--, had_tilde = True;
	else
		had_tilde = False;

	/* Assume we don't find an suffix. */
	suf = "";
	*len_ptr = 0;

	/* Find the last significant suffix. */
	for (s = fn + fn_len; fn_len > 1; ) {
		while (*--s != '.' && s != fn) {}
		if (s == fn)
			break;
		s_len = fn_len - (s - fn);
		fn_len = s - fn;
		if (s_len == 4) {
			if (strcmp(s+1, "bak") == 0
			 || strcmp(s+1, "old") == 0)
				continue;
		} else if (s_len == 5) {
			if (strcmp(s+1, "orig") == 0)
				continue;
		} else if (s_len > 2 && had_tilde && s[1] == '~' && isDigit(s + 2))
			continue;
		*len_ptr = s_len;
		suf = s;
		if (s_len == 1)
			break;
		/* Determine if the suffix is all digits. */
		for (s++, s_len--; s_len > 0; s++, s_len--) {
			if (!isDigit(s))
				return suf;
		}
		/* An all-digit suffix may not be that significant. */
		s = suf;
	}

	return suf;
}

/* This is an implementation of the Levenshtein distance algorithm.  It
 * was implemented to avoid needing a two-dimensional matrix (to save
 * memory).  It was also tweaked to try to factor in the ASCII distance
 * between changed characters as a minor distance quantity.  The normal
 * Levenshtein units of distance (each signifying a single change between
 * the two strings) are defined as a "UNIT". */

#define UNIT (1 << 16)

uint32 fuzzy_distance(const char *s1, unsigned len1, const char *s2, unsigned len2)
{
	uint32 a[MAXPATHLEN], diag, above, left, diag_inc, above_inc, left_inc;
	int32 cost;
	unsigned i1, i2;

	if (!len1 || !len2) {
		if (!len1) {
			s1 = s2;
			len1 = len2;
		}
		for (i1 = 0, cost = 0; i1 < len1; i1++)
			cost += s1[i1];
		return (int32)len1 * UNIT + cost;
	}

	for (i2 = 0; i2 < len2; i2++)
		a[i2] = (i2+1) * UNIT;

	for (i1 = 0; i1 < len1; i1++) {
		diag = i1 * UNIT;
		above = (i1+1) * UNIT;
		for (i2 = 0; i2 < len2; i2++) {
			left = a[i2];
			if ((cost = *((uchar*)s1+i1) - *((uchar*)s2+i2)) != 0) {
				if (cost < 0)
					cost = UNIT - cost;
				else
					cost = UNIT + cost;
			}
			diag_inc = diag + cost;
			left_inc = left + UNIT + *((uchar*)s1+i1);
			above_inc = above + UNIT + *((uchar*)s2+i2);
			a[i2] = above = left < above
			      ? (left_inc < diag_inc ? left_inc : diag_inc)
			      : (above_inc < diag_inc ? above_inc : diag_inc);
			diag = left;
		}
	}

	return a[len2-1];
}

#define BB_SLOT_SIZE     (16*1024)          /* Desired size in bytes */
#define BB_PER_SLOT_BITS (BB_SLOT_SIZE * 8) /* Number of bits per slot */
#define BB_PER_SLOT_INTS (BB_SLOT_SIZE / 4) /* Number of int32s per slot */

struct bitbag {
	uint32 **bits;
	int slot_cnt;
};

struct bitbag *bitbag_create(int max_ndx)
{
	struct bitbag *bb = new(struct bitbag);
	bb->slot_cnt = (max_ndx + BB_PER_SLOT_BITS - 1) / BB_PER_SLOT_BITS;

	bb->bits = new_array0(uint32*, bb->slot_cnt);

	return bb;
}

void bitbag_set_bit(struct bitbag *bb, int ndx)
{
	int slot = ndx / BB_PER_SLOT_BITS;
	ndx %= BB_PER_SLOT_BITS;

	if (!bb->bits[slot])
		bb->bits[slot] = new_array0(uint32, BB_PER_SLOT_INTS);

	bb->bits[slot][ndx/32] |= 1u << (ndx % 32);
}

#if 0 /* not needed yet */
void bitbag_clear_bit(struct bitbag *bb, int ndx)
{
	int slot = ndx / BB_PER_SLOT_BITS;
	ndx %= BB_PER_SLOT_BITS;

	if (!bb->bits[slot])
		return;

	bb->bits[slot][ndx/32] &= ~(1u << (ndx % 32));
}

int bitbag_check_bit(struct bitbag *bb, int ndx)
{
	int slot = ndx / BB_PER_SLOT_BITS;
	ndx %= BB_PER_SLOT_BITS;

	if (!bb->bits[slot])
		return 0;

	return bb->bits[slot][ndx/32] & (1u << (ndx % 32)) ? 1 : 0;
}
#endif

/* Call this with -1 to start checking from 0.  Returns -1 at the end. */
int bitbag_next_bit(struct bitbag *bb, int after)
{
	uint32 bits, mask;
	int i, ndx = after + 1;
	int slot = ndx / BB_PER_SLOT_BITS;
	ndx %= BB_PER_SLOT_BITS;

	mask = (1u << (ndx % 32)) - 1;
	for (i = ndx / 32; slot < bb->slot_cnt; slot++, i = mask = 0) {
		if (!bb->bits[slot])
			continue;
		for ( ; i < BB_PER_SLOT_INTS; i++, mask = 0) {
			if (!(bits = bb->bits[slot][i] & ~mask))
				continue;
			/* The xor magic figures out the lowest enabled bit in
			 * bits, and the switch quickly computes log2(bit). */
			switch (bits ^ (bits & (bits-1))) {
#define LOG2(n) case 1u << n: return slot*BB_PER_SLOT_BITS + i*32 + n
			    LOG2(0);  LOG2(1);  LOG2(2);  LOG2(3);
			    LOG2(4);  LOG2(5);  LOG2(6);  LOG2(7);
			    LOG2(8);  LOG2(9);  LOG2(10); LOG2(11);
			    LOG2(12); LOG2(13); LOG2(14); LOG2(15);
			    LOG2(16); LOG2(17); LOG2(18); LOG2(19);
			    LOG2(20); LOG2(21); LOG2(22); LOG2(23);
			    LOG2(24); LOG2(25); LOG2(26); LOG2(27);
			    LOG2(28); LOG2(29); LOG2(30); LOG2(31);
			}
			return -1; /* impossible... */
		}
	}

	return -1;
}

void flist_ndx_push(flist_ndx_list *lp, int ndx)
{
	struct flist_ndx_item *item;

	item = new(struct flist_ndx_item);
	item->next = NULL;
	item->ndx = ndx;
	if (lp->tail)
		lp->tail->next = item;
	else
		lp->head = item;
	lp->tail = item;
}

int flist_ndx_pop(flist_ndx_list *lp)
{
	struct flist_ndx_item *next;
	int ndx;

	if (!lp->head)
		return -1;

	ndx = lp->head->ndx;
	next = lp->head->next;
	free(lp->head);
	lp->head = next;
	if (!next)
		lp->tail = NULL;

	return ndx;
}

/* Make sure there is room for one more item in the item list.  If there
 * is not, expand the list as indicated by the value of "incr":
 *  - if incr < 0 then increase the malloced size by -1 * incr
 *  - if incr >= 0 then either make the malloced size equal to "incr"
 *    or (if that's not large enough) double the malloced size
 * After the size check, the list's count is incremented by 1 and a pointer
 * to the "new" list item is returned.
 */
void *expand_item_list(item_list *lp, size_t item_size, const char *desc, int incr)
{
	/* First time through, 0 <= 0, so list is expanded. */
	if (lp->malloced <= lp->count) {
		void *new_ptr;
		size_t expand_size;
		if (incr < 0)
			expand_size = -incr; /* increase slowly */
		else if (lp->malloced < (size_t)incr)
			expand_size = incr - lp->malloced;
		else if (lp->malloced)
			expand_size = lp->malloced; /* double in size */
		else
			expand_size = 1;
		if (SIZE_MAX/item_size - expand_size < lp->malloced)
			overflow_exit("expand_item_list");
		expand_size += lp->malloced;
		new_ptr = realloc_buf(lp->items, expand_size * item_size);
		if (DEBUG_GTE(FLIST, 3)) {
			rprintf(FINFO, "[%s] expand %s to %s bytes, did%s move\n",
				who_am_i(), desc, big_num(expand_size * item_size),
				new_ptr == lp->items ? " not" : "");
		}

		lp->items = new_ptr;
		lp->malloced = expand_size;
	}
	return (char*)lp->items + (lp->count++ * item_size);
}

/* This zeroing of memory won't be optimized away by the compiler. */
void force_memzero(void *buf, size_t len)
{
	volatile uchar *z = buf;
	while (len-- > 0)
		*z++ = '\0';
}
