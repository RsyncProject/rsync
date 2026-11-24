/*
 * Backup handling code.
 *
 * Copyright (C) 1999 Andrew Tridgell
 * Copyright (C) 2003-2022 Wayne Davison
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

extern int am_root;
extern int dry_run;
extern int preserve_acls;
extern int preserve_xattrs;
extern int preserve_devices;
extern int preserve_specials;
extern int preserve_links;
extern int safe_symlinks;
extern int backup_dir_len;
extern int backup_suffix_len;
extern unsigned int backup_dir_remainder;
extern int operator_path_resolve;
extern char backup_dir_buf[MAXPATHLEN];
extern char *backup_suffix;
extern char *backup_dir;

/* Pin a backup SOURCE leaf with a confined O_NOFOLLOW fd (via the operator
 * owner-walk resolver, like set_file_attrs's op_leaf_fd) so the ACL/xattr the
 * backup caches off it are read through the held fd -- a parent-symlink race
 * can't redirect the read out of the module.  Returns -1 for a non-hardened
 * receiver (caller path-reads) or for a raced/absent leaf on a hardened one
 * (caller skips the cache rather than read through a flippable path; use
 * backup_metadata_hardened() to tell the two -1 cases apart). */
int backup_metadata_hardened(void)
{
	return secure_relpath_active() && !symlink_optout_allowed();
}

int backup_source_fd(const char *path)
{
#if defined AT_FDCWD && defined O_NOFOLLOW
	if (backup_metadata_hardened() && path && *path) {
		int save = operator_path_resolve, fd;
		operator_path_resolve = 1;
		fd = do_open_at(path, O_RDONLY | O_NONBLOCK | O_NOCTTY | O_CLOEXEC, 0);
		operator_path_resolve = save;
		return fd;
	}
#endif
	return -1;
}

struct backup_dir_access {
	int fd;
	mode_t mode;
};

#if defined HAVE_CHMOD && defined AT_FDCWD && defined O_NOFOLLOW && defined O_DIRECTORY
#define BACKUP_DIR_ACCESS 1
/* One entry per path component plus a short-lived write or metadata grant. */
static struct backup_dir_access backup_access[MAXPATHLEN / 2 + 2];
static volatile sig_atomic_t backup_access_count;
static int backup_lock_fd = -1;
static int backup_lock_error;
#endif

void init_backup_access(void)
{
#ifdef BACKUP_DIR_ACCESS
	FILE *lock_file;

	/* The generator and receiver inherit one anonymous file. Record locks
	 * on it are process-owned and disappear if either process exits. */
	if (backup_dir && !dry_run && am_root <= 0) {
		if (!(lock_file = tmpfile()))
			backup_lock_error = errno;
		else {
			backup_lock_fd = dup(fileno(lock_file));
			if (backup_lock_fd >= 0 && fcntl(backup_lock_fd, F_SETFD, FD_CLOEXEC) < 0) {
				backup_lock_error = errno;
				close(backup_lock_fd);
				backup_lock_fd = -1;
			} else if (backup_lock_fd < 0)
				backup_lock_error = errno;
			fclose(lock_file);
		}
	}
#endif
}

static BOOL lock_backup_access(void)
{
#ifdef BACKUP_DIR_ACCESS
	if (backup_lock_fd >= 0) {
		struct flock lock = {.l_type = F_WRLCK, .l_whence = SEEK_SET};
		int ret;

		do {
			ret = fcntl(backup_lock_fd, F_SETLKW, &lock);
		} while (ret < 0 && errno == EINTR);
		if (ret < 0) {
			rsyserr(FERROR_XFER, errno, "cannot lock backup directory access");
			return False;
		}
	}
#endif
	return True;
}

static int backup_access_mark(void)
{
#ifdef BACKUP_DIR_ACCESS
	return backup_access_count;
#else
	return 0;
#endif
}

static BOOL restore_backup_access(int mark)
{
#ifdef BACKUP_DIR_ACCESS
	int saved_errno = errno, error = 0;

	while (backup_access_count > mark) {
		const struct backup_dir_access *access = &backup_access[backup_access_count - 1];
		int ret;

		do {
			ret = fchmod(access->fd, access->mode);
		} while (ret < 0 && errno == EINTR);
		if (ret < 0 && !error)
			error = errno;
		backup_access_count--;
		close(access->fd);
	}
	if (error) {
		rsyserr(FERROR_XFER, error, "cannot restore backup directory mode for %s",
			full_fname(backup_dir_buf));
		errno = error;
		return False;
	}
	errno = saved_errno;
#else
	(void)mark;
#endif
	return True;
}

BOOL end_backup_access(void)
{
	BOOL ok = restore_backup_access(0);
#ifdef BACKUP_DIR_ACCESS
	int saved_errno = errno;

	if (backup_lock_fd >= 0) {
		struct flock lock = {.l_type = F_UNLCK, .l_whence = SEEK_SET};
		if (fcntl(backup_lock_fd, F_SETLK, &lock) < 0) {
			int error = errno;
			rsyserr(FERROR_XFER, error, "cannot unlock backup directory access");
			errno = error;
			return False;
		}
	}
	errno = saved_errno;
#endif
	return ok;
}

/* Change only an owned directory beneath the checked backup root. The fd
 * keeps mode restoration tied to the same directory, not to its name. */
#ifdef BACKUP_DIR_ACCESS
static int allow_backup_dir_access(const STRUCT_STAT *st, mode_t permission)
{
	STRUCT_STAT root_st, pinned;
	char root[MAXPATHLEN];
	int root_fd, fd, save_errno, mark;

	if (dry_run || !operator_path_resolve || !backup_dir || am_root > 0
	 || !backup_dir_buf[backup_dir_len] || !S_ISDIR(st->st_mode)
	 || st->st_uid != MY_UID() || (st->st_mode & permission) == permission)
		return 0;
	/* A chmod may silently clear an ungrantable setgid bit. */
	if (st->st_mode & S_ISGID && !is_in_group(st->st_gid))
		return 0;
	if (backup_lock_fd < 0) {
		errno = backup_lock_error ? backup_lock_error : ENOTSUP;
		goto failed;
	}
	mark = backup_access_count;
	if (mark >= (int)(sizeof backup_access / sizeof backup_access[0])) {
		errno = ENAMETOOLONG;
		goto failed;
	}

	memcpy(root, backup_dir_buf, backup_dir_len);
	root[backup_dir_len] = '\0';
	if ((root_fd = open_no_attacker_symlinks_dirfd(root)) < 0)
		goto failed;
	if (do_fstat(root_fd, &root_st) < 0)
		fd = -1;
	else if (root_st.st_dev == st->st_dev && root_st.st_ino == st->st_ino) {
		close(root_fd);
		return 0;
	} else
		fd = secure_relative_open_at_beneath(root_fd, backup_dir_buf + backup_dir_len,
			O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, 0);
	save_errno = errno;
	close(root_fd);
	errno = save_errno;
	if (fd < 0)
		goto failed;
	if (do_fstat(fd, &pinned) < 0)
		goto close_failed;
	if (pinned.st_dev != st->st_dev || pinned.st_ino != st->st_ino
	 || pinned.st_mode != st->st_mode || pinned.st_uid != st->st_uid || pinned.st_gid != st->st_gid) {
		errno = EINVAL;
		goto close_failed;
	}
	/* Publish before chmod so exit cleanup also sees an interrupted grant. */
	backup_access[mark].fd = fd;
	backup_access[mark].mode = st->st_mode & CHMOD_BITS;
	backup_access_count = mark + 1;
	if (do_chmod_attrs(backup_dir_buf, (st->st_mode & CHMOD_BITS) | permission, fd, -1, NULL, False) < 0) {
		save_errno = errno;
		backup_access_count = mark;
		errno = save_errno;
		goto close_failed;
	}
	return 1;

  close_failed:
	save_errno = errno;
	close(fd);
	errno = save_errno;
  failed:
	save_errno = errno;
	rsyserr(FERROR_XFER, save_errno, "cannot make backup directory %s %s", full_fname(backup_dir_buf),
		permission == S_IXUSR ? "searchable" : "writable");
	errno = save_errno;
	return -1;
}
#endif

static int allow_backup_dir_mode(mode_t permission)
{
#ifdef BACKUP_DIR_ACCESS
	STRUCT_STAT st;

	if (!dry_run && operator_path_resolve && backup_dir && am_root <= 0
	 && backup_dir_buf[backup_dir_len] && do_lstat_at(backup_dir_buf, &st) == 0)
		return allow_backup_dir_access(&st, permission);
#else
	(void)permission;
#endif
	return 0;
}

static int allow_backup_parent_write(void)
{
	char *slash;
	int ret;

	if (!backup_dir || !(slash = strrchr(backup_dir_buf + backup_dir_len, '/')))
		return 0;
	*slash = '\0';
	ret = allow_backup_dir_mode(S_IWUSR);
	*slash = '/';
	return ret;
}

static int mkdir_backup_dir(void)
{
	int mark = backup_access_mark();
	int ret = do_mkdir_at(backup_dir_buf, ACCESSPERMS);
	int error = errno, granted;

	if (ret == 0 || (errno != EACCES && errno != EPERM))
		return ret;
	if ((granted = allow_backup_parent_write()) < 0)
		return -1;
	if (!granted) {
		errno = error;
		return ret;
	}
	ret = do_mkdir_at(backup_dir_buf, ACCESSPERMS);
	return restore_backup_access(mark) ? ret : -1;
}

static void backup_suffix_collision(void)
{
	rprintf(FERROR_XFER, "backup path %s is both a directory and a suffixed backup name\n",
		full_fname(backup_dir_buf));
	errno = EEXIST;
}

/* Returns -1 on error, 0 on missing dir, and 1 on present dir. */
static int validate_backup_dir(void)
{
	STRUCT_STAT st;

	if (do_lstat_at(backup_dir_buf, &st) < 0) {
		if (errno == ENOENT)
			return 0;
		rsyserr(FERROR, errno, "backup lstat %s failed", backup_dir_buf);
		return -1;
	}
	if (!S_ISDIR(st.st_mode)) {
		int flags = get_del_for_flag(st.st_mode) | DEL_FOR_BACKUP | DEL_RECURSE;
		const char *relative = backup_dir_buf + backup_dir_len;
		const char *slash = strrchr(relative, '/');
		const char *name = slash ? slash + 1 : relative;
		size_t len = strlen(name);
		int ret, mark = backup_access_mark();

		if (backup_suffix_len && len > (size_t)backup_suffix_len
		 && strcmp(name + len - backup_suffix_len, backup_suffix) == 0) {
			backup_suffix_collision();
			return -1;
		}
		if (allow_backup_parent_write() < 0)
			return -1;
		ret = delete_item(backup_dir_buf, st.st_mode, flags);
		if (restore_backup_access(mark) && ret == 0)
			return 0;
		return -1;
	}
#ifdef BACKUP_DIR_ACCESS
	if (allow_backup_dir_access(&st, S_IXUSR) < 0)
		return -1;
#endif
	return 1;
}

/* Create a backup path from the given fname, putting the result into
 * backup_dir_buf.  Any new directories (compared to the prior backup
 * path) are ensured to exist as directories, replacing anything else
 * that may be in the way (e.g. a symlink). */
static BOOL copy_valid_path(const char *fname, const char *suffix)
{
	const char *const parts[] = { fname, suffix };
	int val;
	BOOL ret = True;
	STRUCT_STAT parent;
	stat_x sx;
	char *b, *rel = backup_dir_buf + backup_dir_len, *name = rel;

	if (stringjoin(rel, backup_dir_remainder, parts, sizeof parts / sizeof parts[0]) >= backup_dir_remainder) {
		rprintf(FERROR, "backup filename too long\n");
		return False;
	}

	/* A searchable parent proves the whole prefix is usable. Walk from
	 * the root only to prepare missing or inaccessible components. */
	if ((b = strrchr(rel, '/')) == NULL)
		return True;
	*b = '\0';
	val = do_lstat_at(backup_dir_buf, &parent);
#ifdef BACKUP_DIR_ACCESS
	if (val == 0 && S_ISDIR(parent.st_mode)
	 && allow_backup_dir_access(&parent, S_IXUSR) < 0) {
		*b = '/';
		return False;
	}
#endif
	*b = '/';
	if (val == 0 && S_ISDIR(parent.st_mode))
		return True;

	for ( ; ; name = b + 1) {
		if ((b = strchr(name, '/')) == NULL)
			return True;
		*b = '\0';

		val = validate_backup_dir();
		if (val == 0)
			break;
		if (val < 0) {
			*name = '\0';
			return False;
		}

		*b = '/';
	}

	init_stat_x(&sx);

	for ( ; b; name = b + 1, b = strchr(name, '/')) {
		*b = '\0';

		while (mkdir_backup_dir() < 0) {
			if (errno == EEXIST) {
				val = validate_backup_dir();
				if (val > 0)
					goto next;
				if (val == 0)
					continue;
			} else
				rsyserr(FERROR, errno, "backup mkdir %s failed", backup_dir_buf);
			*name = '\0';
			ret = False;
			goto cleanup;
		}

		/* Try to transfer the directory settings of the actual dir
		 * that the files are coming from. */
		if (x_stat(rel, &sx.st, NULL) < 0)
			rsyserr(FERROR, errno, "backup stat %s failed", full_fname(rel));
		else {
			struct file_struct *file;
			if (!(file = make_file(rel, NULL, NULL, 0, NO_FILTERS)))
				goto next;
			if (!am_root && !is_in_group(sx.st.st_gid))
				file->flags |= FLAG_SKIP_GROUP;
#if defined SUPPORT_ACLS || defined SUPPORT_XATTRS
			{	/* read the source dir's ACL/xattr through a confined fd */
			int bfd = backup_source_fd(rel);
			if (!backup_metadata_hardened() || bfd >= 0) {
# ifdef SUPPORT_ACLS
				if (preserve_acls && !S_ISLNK(file->mode)) {
					get_acl_fdat(bfd, -1, NULL, rel, &sx);
					cache_tmp_acl(file, &sx);
					free_acl(&sx);
				}
# endif
# ifdef SUPPORT_XATTRS
				if (preserve_xattrs) {
					get_xattr(rel, bfd, &sx);
					cache_tmp_xattr(file, &sx);
					free_xattr(&sx);
				}
# endif
			}
			if (bfd >= 0)
				close(bfd);
			}
#endif
			set_file_attrs(backup_dir_buf, file, NULL, NULL, 0);
			unmake_file(file);
		}

		if (allow_backup_dir_mode(S_IXUSR) < 0) {
			ret = False;
			goto cleanup;
		}
	  next:
		*b = '/';
	}

  cleanup:

#ifdef SUPPORT_ACLS
	uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
	uncache_tmp_xattrs();
#endif

	return ret;
}

static char *backup_name(const char *fname, const char *suffix)
{
	const char *const parts[] = { fname, suffix };

	if (backup_dir) {
		static int initialized = 0;
		if (!initialized) {
			char dirbuf[MAXPATHLEN];
			int ret;
			if (strlcpy(dirbuf, backup_dir_buf, sizeof dirbuf) >= sizeof dirbuf) {
				errno = ENAMETOOLONG;
				return NULL;
			}
			if (backup_dir_len > 1)
				dirbuf[backup_dir_len-1] = '\0';
			ret = make_path(dirbuf, 0);
			if (ret < 0)
				return NULL;
			initialized = 1;
		}
		/* copy fname into backup_dir_buf while validating the dirs. */
		if (copy_valid_path(fname, suffix))
			return backup_dir_buf;
		/* copy_valid_path() has printed an error message. */
		return NULL;
	}

	if (stringjoin(backup_dir_buf, MAXPATHLEN, parts, sizeof parts / sizeof parts[0]) < MAXPATHLEN)
		return backup_dir_buf;

	rprintf(FERROR, "backup filename too long\n");
	return NULL;
}

/* Produce a name without creating directories for a basis lookup. */
char *get_backup_name(const char *fname)
{
	char *buf = backup_dir ? backup_dir_buf + backup_dir_len : backup_dir_buf;
	size_t size = backup_dir ? backup_dir_remainder : MAXPATHLEN;
	const char *const parts[] = { fname, backup_suffix };

	if (stringjoin(buf, size, parts, sizeof parts / sizeof parts[0]) < size)
		return backup_dir_buf;
	errno = ENAMETOOLONG;
	return NULL;
}

/* Keep search access while the caller opens an existing backup or sets its
 * attributes. A read must not create or replace a backup directory. */
BOOL begin_backup_read(void)
{
	int save = operator_path_resolve;
	char *part, *slash;
	BOOL ok = False;

#ifndef BACKUP_DIR_ACCESS
	return True;
#endif
	if (!backup_dir || am_root > 0 || dry_run)
		return True;
	if (!lock_backup_access())
		return False;

	operator_path_resolve = 1;
	for (part = backup_dir_buf + backup_dir_len; (slash = strchr(part, '/')); part = slash + 1) {
		int ret;

		*slash = '\0';
		ret = allow_backup_dir_mode(S_IXUSR);
		*slash = '/';
		if (ret < 0)
			goto done;
	}
	ok = True;
  done:
	operator_path_resolve = save;
	if (!ok)
		end_backup_access();
	return ok;
}

int open_inplace_backup(const char *fname, mode_t mode)
{
	int save = operator_path_resolve, fd = -1;

	operator_path_resolve = 1;
	if (lock_backup_access() && backup_name(fname, backup_suffix) && allow_backup_parent_write() >= 0)
		fd = open_copy_destination(backup_dir_buf, mode);
	if (!end_backup_access()) {
		if (fd >= 0)
			close(fd);
		fd = -1;
	}
	operator_path_resolve = save;
	return fd;
}

int copy_inplace_backup(const char *fname, mode_t mode)
{
	int ifd = open_copy_source(fname);
	int ofd;

	if (ifd < 0)
		return -1;
	ofd = open_inplace_backup(fname, mode);
	if (ofd >= 0)
		return copy_file_from_fd(fname, ifd, backup_dir_buf, ofd, mode);
	close(ifd);
	return -1;
}

static BOOL copy_directory_backup_attrs(const char *fname, int source_fd,
					const char *backup, STRUCT_STAT *original)
{
	struct file_struct *file;
	stat_x source;
	int ret = -1, backup_fd = -1, save_errno;

	(void)source_fd;
	init_stat_x(&source);
	file = make_file(fname, NULL, original, 0, NO_FILTERS);
	if (!file) {
		errno = EIO;
		goto cleanup;
	}
	source.st = *original;
	if (!am_root && !is_in_group(original->st_gid))
		file->flags |= FLAG_SKIP_GROUP;
#ifdef SUPPORT_ACLS
	if (preserve_acls) {
		if (get_acl_fdat(source_fd, -1, NULL, fname, &source) < 0)
			goto cleanup;
		cache_tmp_acl(file, &source);
	}
#endif
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs) {
		int xattr_ret, mark = backup_access_mark();
		int granted = allow_backup_dir_mode(S_IWUSR);

		if (granted < 0)
			goto cleanup;
#ifdef BACKUP_DIR_ACCESS
		if (granted)
			backup_fd = backup_access[mark].fd;
		else
#endif
			backup_fd = backup_source_fd(backup);
		xattr_ret = backup_fd < 0 && backup_metadata_hardened() ? -1
			: reconcile_backup_xattrs(fname, source_fd, backup, backup_fd);
		if (granted)
			backup_fd = -1;
		if (!restore_backup_access(mark) || xattr_ret < 0)
			goto cleanup;
	}
#endif
	ret = set_file_attrs(backup, file, NULL, NULL,
			     ATTRS_ACCURATE_TIME | ATTRS_REQUIRE_SUCCESS);

  cleanup:
	save_errno = errno;
	if (backup_fd >= 0)
		close(backup_fd);
	free_stat_x(&source);
	if (file)
		unmake_file(file);
#ifdef SUPPORT_ACLS
	uncache_tmp_acls();
#endif
	errno = save_errno;
	return ret >= 0;
}

/* Prepare an empty counterpart before rmdir proves the original is empty.
 * A directory created for a child backup is reused, without touching its contents. */
BOOL make_directory_backup(const char *fname, STRUCT_STAT *original)
{
	STRUCT_STAT current, dest;
	int save_resolve = operator_path_resolve;
	int source_fd = backup_source_fd(fname);
	char *backup;
	BOOL ok = False;

	if ((source_fd < 0 && backup_metadata_hardened())
	 || (source_fd >= 0 ? do_fstat(source_fd, &current) : do_lstat_at(fname, &current)) < 0)
		goto done;
	if (!S_ISDIR(current.st_mode) || current.st_dev != original->st_dev
	 || current.st_ino != original->st_ino) {
		errno = EINVAL;
		goto done;
	}

	operator_path_resolve = 1;
	/* Directory metadata belongs to the same path as its child backups. */
	if (!lock_backup_access() || !(backup = backup_name(fname, "")))
		goto done;
	if (mkdir_backup_dir() < 0 && errno != EEXIST)
		goto done;
	if (do_lstat_at(backup, &dest) < 0)
		goto done;
	if (!S_ISDIR(dest.st_mode) || (dest.st_dev == current.st_dev && dest.st_ino == current.st_ino)) {
		errno = EEXIST;
		goto done;
	}
	if (!copy_directory_backup_attrs(fname, source_fd, backup, original))
		goto done;
	ok = True;
	if (INFO_GTE(BACKUP, 1))
		rprintf(FINFO, "backed up %s to %s\n", fname, backup);

  done:
	if (!end_backup_access())
		ok = False;
	if (!ok)
		rsyserr(FERROR_XFER, errno ? errno : EIO, "backup directory %s failed", full_fname(fname));
	if (source_fd >= 0)
		close(source_fd);
	operator_path_resolve = save_resolve;
	return ok;
}

/* Has same return codes as make_backup(). */
static inline int link_or_rename(const char *from, const char *to,
				 BOOL prefer_rename, STRUCT_STAT *stp)
{
#ifdef SUPPORT_HARD_LINKS
	if (!prefer_rename) {
#ifndef CAN_HARDLINK_SYMLINK
		if (S_ISLNK(stp->st_mode))
			return 0; /* Use copy code. */
#endif
#ifndef CAN_HARDLINK_SPECIAL
		if (IS_SPECIAL(stp->st_mode) || IS_DEVICE(stp->st_mode))
			return 0; /* Use copy code. */
#endif
		if (do_link_at(from, to) == 0) {
			if (DEBUG_GTE(BACKUP, 1))
				rprintf(FINFO, "make_backup: HLINK %s successful.\n", from);
			return 2;
		}
		/* We prefer to rename a regular file rather than copy it. */
		if (!S_ISREG(stp->st_mode) || errno == EEXIST || errno == EISDIR)
			return 0;
	}
#endif
	if (do_rename_at(from, to) == 0) {
		if (stp->st_nlink > 1 && !S_ISDIR(stp->st_mode)) {
			/* If someone has hard-linked the file into the backup
			 * dir, rename() might return success but do nothing! */
			robust_unlink(from); /* Just in case... */
		}
		if (DEBUG_GTE(BACKUP, 1))
			rprintf(FINFO, "make_backup: RENAME %s successful.\n", from);
		return 1;
	}
	return 0;
}

/* Hard-link, rename, or copy an item to the backup name.  Returns 0 for
 * failure, 1 if item was moved, 2 if item was duplicated or hard linked
 * into backup area, or 3 if item doesn't exist or isn't a regular file. */
static int make_backup_inner(const char *fname, BOOL prefer_rename)
{
	stat_x sx;
	struct file_struct *file;
	int save_preserve_xattrs, save_errno, granted;
	char *buf;
	int ret = 0;

	init_stat_x(&sx);
	/* Return success if no file to keep. */
	if (x_lstat(fname, &sx.st, NULL) < 0)
		return 3;

	if (!(buf = backup_name(fname, backup_suffix)))
		return 0;

#ifdef SUPPORT_LINKS
	/* Honor --safe-links BEFORE the hard-link / rename fast path.  When
	 * CAN_HARDLINK_SYMLINK is defined, link_or_rename() would otherwise
	 * hard-link an escaping symlink (e.g. ../../etc/passwd) into the backup
	 * area and "goto success", skipping the safe_symlinks check in the
	 * copy-fallback path below -- silently preserving an unsafe link that
	 * --safe-links was meant to drop.  Match the copy path: don't back up an
	 * unsafe symlink. */
	if (preserve_links && S_ISLNK(sx.st.st_mode) && safe_symlinks) {
		char lnkbuf[MAXPATHLEN];
		int llen = do_readlink(fname, lnkbuf, MAXPATHLEN - 1);
		/* A failed readlink means we can't verify the target, so fail
		 * closed: skip the backup rather than let the hard-link fast path
		 * preserve a possibly-unsafe symlink unchecked. */
		if (llen <= 0) {
			if (INFO_GTE(SYMSAFE, 1))
				rprintf(FINFO, "not backing up symlink with unreadable target \"%s\"\n", fname);
			ret = 2;
			goto success;
		}
		lnkbuf[llen] = '\0';
		if (unsafe_symlink(lnkbuf, fname)) {
			if (INFO_GTE(SYMSAFE, 1)) {
				rprintf(FINFO, "not backing up unsafe symlink \"%s\" -> \"%s\"\n",
					fname, lnkbuf);
			}
			ret = 2;
			goto success;
		}
	}
#endif

	/* Try a hard-link or a rename first.  Using rename is not atomic, but
	 * is more efficient than forcing a copy for larger files when no hard-
	 * linking is possible. */
	if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
		goto success;
	save_errno = errno;
	if ((granted = allow_backup_parent_write()) < 0)
		return 0;
	if (granted) {
		if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
			goto success;
	} else
		errno = save_errno;
	if (errno == EEXIST || errno == EISDIR) {
		STRUCT_STAT bakst;
		if (do_lstat_at(buf, &bakst) == 0) {
			int flags = get_del_for_flag(bakst.st_mode) | DEL_FOR_BACKUP | DEL_RECURSE;
			if (backup_dir && backup_suffix_len && S_ISDIR(bakst.st_mode)) {
				backup_suffix_collision();
				return 0;
			}
			if (delete_item(buf, bakst.st_mode, flags) != 0)
				return 0;
		}
		if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
			goto success;
	}

	/* Fall back to making a copy. */
	if (!(file = make_file(fname, NULL, &sx.st, 0, NO_FILTERS)))
		return 3; /* the file could have disappeared */

#if defined SUPPORT_ACLS || defined SUPPORT_XATTRS
	{	/* read the source file's ACL/xattr through a confined fd */
	int bfd = backup_source_fd(fname);
	if (!backup_metadata_hardened() || bfd >= 0) {
# ifdef SUPPORT_ACLS
		if (preserve_acls && !S_ISLNK(file->mode)) {
			get_acl_fdat(bfd, -1, NULL, fname, &sx);
			cache_tmp_acl(file, &sx);
			free_acl(&sx);
		}
# endif
# ifdef SUPPORT_XATTRS
		if (preserve_xattrs) {
			get_xattr(fname, bfd, &sx);
			cache_tmp_xattr(file, &sx);
			free_xattr(&sx);
		}
# endif
	}
	if (bfd >= 0)
		close(bfd);
	}
#endif

	/* Check to see if this is a device file, or link */
	if ((am_root && preserve_devices && IS_DEVICE(file->mode))
	 || (preserve_specials && IS_SPECIAL(file->mode))) {
		if (do_mknod_at(buf, file->mode, sx.st.st_rdev) < 0)
			rsyserr(FERROR, errno, "mknod %s failed", full_fname(buf));
		else if (DEBUG_GTE(BACKUP, 1))
			rprintf(FINFO, "make_backup: DEVICE %s successful.\n", fname);
		ret = 2;
	}

#ifdef SUPPORT_LINKS
	if (!ret && preserve_links && S_ISLNK(file->mode)) {
		const char *sl = F_SYMLINK(file);
		if (safe_symlinks && unsafe_symlink(sl, fname)) {
			if (INFO_GTE(SYMSAFE, 1)) {
				rprintf(FINFO, "not backing up unsafe symlink \"%s\" -> \"%s\"\n",
					fname, sl);
			}
			ret = 2;
		} else {
			if (do_symlink_at(sl, buf) < 0)
				rsyserr(FERROR, errno, "link %s -> \"%s\"", full_fname(buf), sl);
			else if (DEBUG_GTE(BACKUP, 1))
				rprintf(FINFO, "make_backup: SYMLINK %s successful.\n", fname);
			ret = 2;
		}
	}
#endif

	if (!ret && !S_ISREG(file->mode)) {
		if (INFO_GTE(NONREG, 1))
			rprintf(FINFO, "make_bak: skipping non-regular file %s\n", fname);
		unmake_file(file);
#ifdef SUPPORT_ACLS
		uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
		uncache_tmp_xattrs();
#endif
		return 3;
	}

	/* Copy to backup tree if a file. */
	if (!ret) {
		if (copy_file(fname, buf, -1, file->mode) < 0) {
			rsyserr(FERROR, errno, "keep_backup failed: %s -> \"%s\"",
				full_fname(fname), buf);
			unmake_file(file);
#ifdef SUPPORT_ACLS
			uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
			uncache_tmp_xattrs();
#endif
			return 0;
		}
		if (DEBUG_GTE(BACKUP, 1))
			rprintf(FINFO, "make_backup: COPY %s successful.\n", fname);
		ret = 2;
	}

	save_preserve_xattrs = preserve_xattrs;
	preserve_xattrs = 0;
	set_file_attrs(buf, file, NULL, fname, ATTRS_ACCURATE_TIME);
	preserve_xattrs = save_preserve_xattrs;

	unmake_file(file);
#ifdef SUPPORT_ACLS
	uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
	uncache_tmp_xattrs();
#endif

  success:
	if (INFO_GTE(BACKUP, 1))
		rprintf(FINFO, "backed up %s to %s\n", fname, buf);
	return ret;
}

int make_backup(const char *fname, BOOL prefer_rename)
{
	int save = operator_path_resolve, ret;
	/* The --backup-dir is an operator-supplied path: resolve it (and the
	 * tail/rename beneath it) with the ownership walk so a foreign-owned
	 * symlink component is refused while the operator's own is followed --
	 * absolute and relative alike.  --insecure-links / "insecure links ="
	 * restores legacy following. */
	operator_path_resolve = 1;
	ret = lock_backup_access() ? make_backup_inner(fname, prefer_rename) : 0;
	if (!end_backup_access())
		ret = 0;
	operator_path_resolve = save;
	return ret;
}
