/*
 * Utility routines used in rsync.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003, 2004, 2005, 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
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

#include "rsync.h"

extern int verbose;
extern int dry_run;
extern int module_id;
extern int modify_window;
extern int relative_paths;
extern int human_readable;
extern unsigned int module_dirlen;
extern mode_t orig_umask;
extern char *partial_dir;
extern struct filter_list_struct server_filter_list;

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

void print_child_argv(char **cmd)
{
	rprintf(FCLIENT, "opening connection using ");
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
	}
	rprintf(FCLIENT, "\n");
}

NORETURN void out_of_memory(char *str)
{
	rprintf(FERROR, "ERROR: out of memory in %s [%s]\n", str, who_am_i());
	exit_cleanup(RERR_MALLOC);
}

NORETURN void overflow_exit(char *str)
{
	rprintf(FERROR, "ERROR: buffer overflow in %s [%s]\n", str, who_am_i());
	exit_cleanup(RERR_MALLOC);
}

int set_modtime(char *fname, time_t modtime, mode_t mode)
{
#if !defined HAVE_LUTIMES || !defined HAVE_UTIMES
	if (S_ISLNK(mode))
		return 1;
#endif

	if (verbose > 2) {
		rprintf(FINFO, "set modtime of %s to (%ld) %s",
			fname, (long)modtime,
			asctime(localtime(&modtime)));
	}

	if (dry_run)
		return 0;

	{
#ifdef HAVE_UTIMES
		struct timeval t[2];
		t[0].tv_sec = time(NULL);
		t[0].tv_usec = 0;
		t[1].tv_sec = modtime;
		t[1].tv_usec = 0;
# ifdef HAVE_LUTIMES
		if (S_ISLNK(mode))
			return lutimes(fname, t);
# endif
		return utimes(fname, t);
#elif defined HAVE_UTIMBUF
		struct utimbuf tbuf;
		tbuf.actime = time(NULL);
		tbuf.modtime = modtime;
		return utime(fname,&tbuf);
#elif defined HAVE_UTIME
		time_t t[2];
		t[0] = time(NULL);
		t[1] = modtime;
		return utime(fname,t);
#else
#error No file-time-modification routine found!
#endif
	}
}

/* This creates a new directory with default permissions.  Since there
 * might be some directory-default permissions affecting this, we can't
 * force the permissions directly using the original umask and mkdir(). */
int mkdir_defmode(char *fname)
{
	int ret;

	umask(orig_umask);
	ret = do_mkdir(fname, ACCESSPERMS);
	umask(0);

	return ret;
}

/* Create any necessary directories in fname.  Any missing directories are
 * created with default permissions. */
int create_directory_path(char *fname)
{
	char *p;
	int ret = 0;

	while (*fname == '/')
		fname++;
	while (strncmp(fname, "./", 2) == 0)
		fname += 2;

	umask(orig_umask);
	p = fname;
	while ((p = strchr(p,'/')) != NULL) {
		*p = '\0';
		if (do_mkdir(fname, ACCESSPERMS) < 0 && errno != EEXIST)
		    ret = -1;
		*p++ = '/';
	}
	umask(0);

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
int full_write(int desc, char *ptr, size_t len)
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

/** Copy a file.
 *
 * This is used in conjunction with the --temp-dir, --backup, and
 * --copy-dest options. */
int copy_file(const char *source, const char *dest, mode_t mode)
{
	int ifd;
	int ofd;
	char buf[1024 * 8];
	int len;   /* Number of bytes read into `buf'. */

	ifd = do_open(source, O_RDONLY, 0);
	if (ifd == -1) {
		rsyserr(FERROR, errno, "open %s", full_fname(source));
		return -1;
	}

	if (robust_unlink(dest) && errno != ENOENT) {
		rsyserr(FERROR, errno, "unlink %s", full_fname(dest));
		return -1;
	}

	ofd = do_open(dest, O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, mode);
	if (ofd == -1) {
		rsyserr(FERROR, errno, "open %s", full_fname(dest));
		close(ifd);
		return -1;
	}

	while ((len = safe_read(ifd, buf, sizeof buf)) > 0) {
		if (full_write(ofd, buf, len) < 0) {
			rsyserr(FERROR, errno, "write %s", full_fname(dest));
			close(ifd);
			close(ofd);
			return -1;
		}
	}

	if (len < 0) {
		rsyserr(FERROR, errno, "read %s", full_fname(source));
		close(ifd);
		close(ofd);
		return -1;
	}

	if (close(ifd) < 0) {
		rsyserr(FINFO, errno, "close failed on %s",
			full_fname(source));
	}

	if (close(ofd) < 0) {
		rsyserr(FERROR, errno, "close failed on %s",
			full_fname(dest));
		return -1;
	}

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

	if (verbose > 0) {
		rprintf(FINFO,"renaming %s to %s because of text busy\n",
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
int robust_rename(char *from, char *to, char *partialptr,
		  int mode)
{
	int tries = 4;

	while (tries--) {
		if (do_rename(from, to) == 0)
			return 0;

		switch (errno) {
#ifdef ETXTBSY
		case ETXTBSY:
			if (robust_unlink(to) != 0)
				return -1;
			break;
#endif
		case EXDEV:
			if (partialptr) {
				if (!handle_partial_dir(partialptr,PDIR_CREATE))
					return -1;
				to = partialptr;
			}
			if (copy_file(from, to, mode) != 0)
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

/** Turn a user name into a uid */
int name_to_uid(char *name, uid_t *uid)
{
	struct passwd *pass;
	if (!name || !*name)
		return 0;
	pass = getpwnam(name);
	if (pass) {
		*uid = pass->pw_uid;
		return 1;
	}
	return 0;
}

/** Turn a group name into a gid */
int name_to_gid(char *name, gid_t *gid)
{
	struct group *grp;
	if (!name || !*name)
		return 0;
	grp = getgrnam(name);
	if (grp) {
		*gid = grp->gr_gid;
		return 1;
	}
	return 0;
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

static int filter_server_path(char *arg)
{
	char *s;

	if (server_filter_list.head) {
		for (s = arg; (s = strchr(s, '/')) != NULL; ) {
			*s = '\0';
			if (check_filter(&server_filter_list, arg, 1) < 0) {
				/* We must leave arg truncated! */
				return 1;
			}
			*s++ = '/';
		}
	}
	return 0;
}

static void glob_expand_one(char *s, char ***argv_ptr, int *argc_ptr,
			    int *maxargs_ptr)
{
	char **argv = *argv_ptr;
	int argc = *argc_ptr;
	int maxargs = *maxargs_ptr;
#if !defined HAVE_GLOB || !defined HAVE_GLOB_H
	if (argc == maxargs) {
		maxargs += MAX_ARGS;
		if (!(argv = realloc_array(argv, char *, maxargs)))
			out_of_memory("glob_expand_one");
		*argv_ptr = argv;
		*maxargs_ptr = maxargs;
	}
	if (!*s)
		s = ".";
	s = argv[argc++] = strdup(s);
	filter_server_path(s);
#else
	glob_t globbuf;

	if (maxargs <= argc)
		return;
	if (!*s)
		s = ".";

	if (sanitize_paths)
		s = sanitize_path(NULL, s, "", 0, NULL);
	else
		s = strdup(s);

	memset(&globbuf, 0, sizeof globbuf);
	if (!filter_server_path(s))
		glob(s, 0, NULL, &globbuf);
	if (MAX((int)globbuf.gl_pathc, 1) > maxargs - argc) {
		maxargs += globbuf.gl_pathc + MAX_ARGS;
		if (!(argv = realloc_array(argv, char *, maxargs)))
			out_of_memory("glob_expand_one");
		*argv_ptr = argv;
		*maxargs_ptr = maxargs;
	}
	if (globbuf.gl_pathc == 0)
		argv[argc++] = s;
	else {
		int i;
		free(s);
		for (i = 0; i < (int)globbuf.gl_pathc; i++) {
			if (!(argv[argc++] = strdup(globbuf.gl_pathv[i])))
				out_of_memory("glob_expand_one");
		}
	}
	globfree(&globbuf);
#endif
	*argc_ptr = argc;
}

/* This routine is only used in daemon mode. */
void glob_expand(char *base1, char ***argv_ptr, int *argc_ptr, int *maxargs_ptr)
{
	char *s = (*argv_ptr)[*argc_ptr];
	char *p, *q;
	char *base = base1;
	int base_len = strlen(base);

	if (!s || !*s)
		return;

	if (strncmp(s, base, base_len) == 0)
		s += base_len;

	if (!(s = strdup(s)))
		out_of_memory("glob_expand");

	if (asprintf(&base," %s/", base1) <= 0)
		out_of_memory("glob_expand");
	base_len++;

	for (q = s; *q; q = p + base_len) {
		if ((p = strstr(q, base)) != NULL)
			*p = '\0'; /* split it at this point */
		glob_expand_one(q, argv_ptr, argc_ptr, maxargs_ptr);
		if (!p)
			break;
	}

	free(s);
	free(base);
}

/**
 * Convert a string to lower case
 **/
void strlower(char *s)
{
	while (*s) {
		if (isupper(*(unsigned char *)s))
			*s = tolower(*(unsigned char *)s);
		s++;
	}
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

/* Turns multiple adjacent slashes into a single slash, gets rid of "./"
 * elements (but not a trailing dot dir), removes a trailing slash, and
 * optionally collapses ".." elements (except for those at the start of the
 * string).  If the resulting name would be empty, change it into a ".". */
unsigned int clean_fname(char *name, BOOL collapse_dot_dot)
{
	char *limit = name - 1, *t = name, *f = name;
	int anchored;

	if (!name)
		return 0;

	if ((anchored = *f == '/') != 0)
		*t++ = *f++;
	while (*f) {
		/* discard extra slashes */
		if (*f == '/') {
			f++;
			continue;
		}
		if (*f == '.') {
			/* discard "." dirs (but NOT a trailing '.'!) */
			if (f[1] == '/') {
				f += 2;
				continue;
			}
			/* collapse ".." dirs */
			if (collapse_dot_dot
			    && f[1] == '.' && (f[2] == '/' || !f[2])) {
				char *s = t - 1;
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

	if (t > name+anchored && t[-1] == '/')
		t--;
	if (t == name)
		*t++ = '.';
	*t = '\0';

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
 * Specify NULL to get the default of lp_path(module_id).
 *
 * The depth var is a count of how many '..'s to allow at the start of the
 * path.  If symlink is set, combine its value with the "p" value to get
 * the target path, and **return NULL if any '..'s try to escape**.
 *
 * We also clean the path in a manner similar to clean_fname() but with a
 * few differences:
 *
 * Turns multiple adjacent slashes into a single slash, gets rid of "." dir
 * elements (INCLUDING a trailing dot dir), PRESERVES a trailing slash, and
 * ALWAYS collapses ".." elements (except for those at the start of the
 * string up to "depth" deep).  If the resulting name would be empty,
 * change it into a ".". */
char *sanitize_path(char *dest, const char *p, const char *rootdir, int depth,
		    const char *symlink)
{
	char *start, *sanp, *save_dest = dest;
	int rlen = 0, leave_one_dotdir = relative_paths;

	if (symlink && *symlink == '/') {
		p = symlink;
		symlink = "";
	}

	if (dest != p) {
		int plen = strlen(p);
		if (*p == '/') {
			if (!rootdir)
				rootdir = lp_path(module_id);
			rlen = strlen(rootdir);
			depth = 0;
			p++;
		}
		if (dest) {
			if (rlen + plen + 1 >= MAXPATHLEN)
				return NULL;
		} else if (!(dest = new_array(char, rlen + plen + 1)))
			out_of_memory("sanitize_path");
		if (rlen) {
			memcpy(dest, rootdir, rlen);
			if (rlen > 1)
				dest[rlen++] = '/';
		}
	}

	start = sanp = dest + rlen;
	while (1) {
		if (*p == '\0') {
			if (!symlink || !*symlink)
				break;
			while (sanp != start && sanp[-1] != '/') {
				/* strip last element */
				sanp--;
			}
			/* Append a relative symlink */
			p = symlink;
			symlink = "";
		}
		/* discard leading or extra slashes */
		if (*p == '/') {
			p++;
			continue;
		}
		/* this loop iterates once per filename component in p.
		 * both p (and sanp if the original had a slash) should
		 * always be left pointing after a slash
		 */
		if (*p == '.' && (p[1] == '/' || p[1] == '\0')) {
			if (leave_one_dotdir && p[1])
				leave_one_dotdir = 0;
			else {
				/* skip "." component */
				p++;
				continue;
			}
		}
		if (*p == '.' && p[1] == '.' && (p[2] == '/' || p[2] == '\0')) {
			/* ".." component followed by slash or end */
			if (depth <= 0 || sanp != start) {
				if (symlink && sanp == start) {
					if (!save_dest)
						free(dest);
					return NULL;
				}
				p += 2;
				if (sanp != start) {
					/* back up sanp one level */
					--sanp; /* now pointing at slash */
					while (sanp > start && sanp[-1] != '/') {
						/* skip back up to slash */
						sanp--;
					}
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
int push_dir(char *dir, int set_path_only)
{
	static int initialised;
	unsigned int len;

	if (!initialised) {
		initialised = 1;
		getcwd(curr_dir, sizeof curr_dir - 1);
		curr_dir_len = strlen(curr_dir);
	}

	if (!dir)	/* this call was probably just to initialize */
		return 0;

	len = strlen(dir);
	if (len == 1 && *dir == '.')
		return 1;

	if ((*dir == '/' ? len : curr_dir_len + 1 + len) >= sizeof curr_dir)
		return 0;

	if (!set_path_only && chdir(dir))
		return 0;

	if (*dir == '/') {
		memcpy(curr_dir, dir, len + 1);
		curr_dir_len = len;
	} else {
		curr_dir[curr_dir_len++] = '/';
		memcpy(curr_dir + curr_dir_len, dir, len + 1);
		curr_dir_len += len;
	}

	curr_dir_len = clean_fname(curr_dir, 1);
	if (sanitize_paths) {
		if (module_dirlen > curr_dir_len)
			module_dirlen = curr_dir_len;
		curr_dir_depth = count_dir_elements(curr_dir + module_dirlen);
	}

	return 1;
}

/**
 * Reverse a push_dir() call.  You must pass in an absolute path
 * that was copied from a prior value of "curr_dir".
 **/
int pop_dir(char *dir)
{
	if (chdir(dir))
		return 0;

	curr_dir_len = strlcpy(curr_dir, dir, sizeof curr_dir);
	if (curr_dir_len >= sizeof curr_dir)
		curr_dir_len = sizeof curr_dir - 1;
	if (sanitize_paths)
		curr_dir_depth = count_dir_elements(curr_dir + module_dirlen);

	return 1;
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

	if (asprintf(&result, "\"%s%s%s\"%s%s%s", p1, p2, fn, m1, m2, m3) <= 0)
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
	if (server_filter_list.head) {
		t = strrchr(partial_fname, '/');
		*t = '\0';
		if (check_filter(&server_filter_list, partial_fname, 1) < 0)
			return NULL;
		*t = '/';
		if (check_filter(&server_filter_list, partial_fname, 0) < 0)
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
			if (do_unlink(dir) < 0)
				return 0;
			statret = -1;
		}
		if (statret < 0 && do_mkdir(dir, 0700) < 0)
			return 0;
	} else
		do_rmdir(dir);
	*fn = '/';

	return 1;
}

/**
 * Determine if a symlink points outside the current directory tree.
 * This is considered "unsafe" because e.g. when mirroring somebody
 * else's machine it might allow them to establish a symlink to
 * /etc/passwd, and then read it through a web server.
 *
 * Null symlinks and absolute symlinks are always unsafe.
 *
 * Basically here we are concerned with symlinks whose target contains
 * "..", because this might cause us to walk back up out of the
 * transferred directory.  We are not allowed to go back up and
 * reenter.
 *
 * @param dest Target of the symlink in question.
 *
 * @param src Top source directory currently applicable.  Basically this
 * is the first parameter to rsync in a simple invocation, but it's
 * modified by flist.c in slightly complex ways.
 *
 * @retval True if unsafe
 * @retval False is unsafe
 *
 * @sa t_unsafe.c
 **/
int unsafe_symlink(const char *dest, const char *src)
{
	const char *name, *slash;
	int depth = 0;

	/* all absolute and null symlinks are unsafe */
	if (!dest || !*dest || *dest == '/')
		return 1;

	/* find out what our safety margin is */
	for (name = src; (slash = strchr(name, '/')) != 0; name = slash+1) {
		if (strncmp(name, "../", 3) == 0) {
			depth = 0;
		} else if (strncmp(name, "./", 2) == 0) {
			/* nothing */
		} else {
			depth++;
		}
	}
	if (strcmp(name, "..") == 0)
		depth = 0;

	for (name = dest; (slash = strchr(name, '/')) != 0; name = slash+1) {
		if (strncmp(name, "../", 3) == 0) {
			/* if at any point we go outside the current directory
			   then stop - it is unsafe */
			if (--depth < 0)
				return 1;
		} else if (strncmp(name, "./", 2) == 0) {
			/* nothing */
		} else {
			depth++;
		}
	}
	if (strcmp(name, "..") == 0)
		depth--;

	return (depth < 0);
}

/* Return the int64 number as a string.  If the --human-readable option was
 * specified, we may output the number in K, M, or G units.  We can return
 * up to 4 buffers at a time. */
char *human_num(int64 num)
{
	static char bufs[4][128]; /* more than enough room */
	static unsigned int n;
	char *s;

	n = (n + 1) % (sizeof bufs / sizeof bufs[0]);

	if (human_readable) {
		char units = '\0';
		int mult = human_readable == 1 ? 1000 : 1024;
		double dnum = 0;
		if (num > mult*mult*mult) {
			dnum = (double)num / (mult*mult*mult);
			units = 'G';
		} else if (num > mult*mult) {
			dnum = (double)num / (mult*mult);
			units = 'M';
		} else if (num > mult) {
			dnum = (double)num / mult;
			units = 'K';
		}
		if (units) {
			snprintf(bufs[n], sizeof bufs[0], "%.2f%c", dnum, units);
			return bufs[n];
		}
	}

	s = bufs[n] + sizeof bufs[0] - 1;
	*s = '\0';

	if (!num)
		*--s = '0';
	while (num) {
		*--s = (num % 10) + '0';
		num /= 10;
	}
	return s;
}

/* Return the double number as a string.  If the --human-readable option was
 * specified, we may output the number in K, M, or G units.  We use a buffer
 * from human_num() to return our result. */
char *human_dnum(double dnum, int decimal_digits)
{
	char *buf = human_num(dnum);
	int len = strlen(buf);
	if (isdigit(*(uchar*)(buf+len-1))) {
		/* There's extra room in buf prior to the start of the num. */
		buf -= decimal_digits + 1;
		snprintf(buf, len + decimal_digits + 2, "%.*f", decimal_digits, dnum);
	}
	return buf;
}

/**
 * Return the date and time as a string
 **/
char *timestring(time_t t)
{
	static char TimeBuf[200];
	struct tm *tm = localtime(&t);
	char *p;

#ifdef HAVE_STRFTIME
	strftime(TimeBuf, sizeof TimeBuf - 1, "%Y/%m/%d %H:%M:%S", tm);
#else
	strlcpy(TimeBuf, asctime(tm), sizeof TimeBuf);
#endif

	if ((p = strchr(TimeBuf, '\n')) != NULL)
		*p = '\0';

	return TimeBuf;
}

/**
 * Sleep for a specified number of milliseconds.
 *
 * Always returns TRUE.  (In the future it might return FALSE if
 * interrupted.)
 **/
int msleep(int t)
{
	int tdiff = 0;
	struct timeval tval, t1, t2;

	gettimeofday(&t1, NULL);

	while (tdiff < t) {
		tval.tv_sec = (t-tdiff)/1000;
		tval.tv_usec = 1000*((t-tdiff)%1000);

		errno = 0;
		select(0,NULL,NULL, NULL, &tval);

		gettimeofday(&t2, NULL);
		tdiff = (t2.tv_sec - t1.tv_sec)*1000 +
			(t2.tv_usec - t1.tv_usec)/1000;
	}

	return True;
}

/* Determine if two time_t values are equivalent (either exact, or in
 * the modification timestamp window established by --modify-window).
 *
 * @retval 0 if the times should be treated as the same
 *
 * @retval +1 if the first is later
 *
 * @retval -1 if the 2nd is later
 **/
int cmp_time(time_t file1, time_t file2)
{
	if (file2 > file1) {
		if (file2 - file1 <= modify_window)
			return 0;
		return -1;
	}
	if (file1 - file2 <= modify_window)
		return 0;
	return 1;
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
	int ret;
	char *cmd;

	asprintf(&cmd, "/usr/X11R6/bin/xterm -display :0 -T Panic -n Panic -e /bin/sh -c 'cat /tmp/ierrs.*.%d ; gdb /proc/%d/exe %d'",
		getpid(), getpid(), getpid());

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

#define MALLOC_MAX 0x40000000

void *_new_array(unsigned int size, unsigned long num)
{
	if (num >= MALLOC_MAX/size)
		return NULL;
	return malloc(size * num);
}

void *_realloc_array(void *ptr, unsigned int size, unsigned long num)
{
	if (num >= MALLOC_MAX/size)
		return NULL;
	/* No realloc should need this, but just in case... */
	if (!ptr)
		return malloc(size * num);
	return realloc(ptr, size * num);
}

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
		} else if (s_len > 2 && had_tilde
		    && s[1] == '~' && isdigit(*(uchar*)(s+2)))
			continue;
		*len_ptr = s_len;
		suf = s;
		if (s_len == 1)
			break;
		/* Determine if the suffix is all digits. */
		for (s++, s_len--; s_len > 0; s++, s_len--) {
			if (!isdigit(*(uchar*)s))
				return suf;
		}
		/* An all-digit suffix may not be that signficant. */
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

uint32 fuzzy_distance(const char *s1, int len1, const char *s2, int len2)
{
	uint32 a[MAXPATHLEN], diag, above, left, diag_inc, above_inc, left_inc;
	int32 cost;
	int i1, i2;

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

	if (!(bb->bits = (uint32**)calloc(bb->slot_cnt, sizeof (uint32*))))
		out_of_memory("bitbag_create");

	return bb;
}

void bitbag_set_bit(struct bitbag *bb, int ndx)
{
	int slot = ndx / BB_PER_SLOT_BITS;
	ndx %= BB_PER_SLOT_BITS;

	if (!bb->bits[slot]) {
		if (!(bb->bits[slot] = (uint32*)calloc(BB_PER_SLOT_INTS, 4)))
			out_of_memory("bitbag_set_bit");
	}

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
