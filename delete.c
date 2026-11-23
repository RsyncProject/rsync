/*
 * Deletion routines used in rsync.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2024 Wayne Davison
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

extern int am_root;
extern int dry_run;
extern int make_backups;
extern int max_delete;
extern char *backup_dir;
extern char *backup_suffix;
extern int backup_suffix_len;
extern struct stats stats;

int ignore_perishable = 0;
int non_perishable_cnt = 0;
int skipped_deletes = 0;

/* Held fd of the directory whose contents delete_dir_contents() is currently
 * removing, so delete_item()'s per-entry rmdir/unlink/chmod go through it
 * instead of re-resolving the full path for every entry.  Set (with save/
 * restore across the recursion) around the delete loop; -1 outside a recursive
 * delete or when the secure resolver is gated off (chroot / non-receiver) or
 * the path doesn't live directly in that dir. */
static int del_dirfd = -1;
static const char *del_dir_prefix;
static int del_dir_prefix_len;

/* If `path` is a single component directly inside the dir being deleted,
 * point *leaf at its basename and return the held dir fd; else return -1. */
static int del_held_dfd(const char *path, const char **leaf)
{
	if (del_dirfd >= 0
	 && strncmp(path, del_dir_prefix, del_dir_prefix_len) == 0
	 && path[del_dir_prefix_len] == '/'
	 && strchr(path + del_dir_prefix_len + 1, '/') == NULL) {
		*leaf = path + del_dir_prefix_len + 1;
		return del_dirfd;
	}
	return -1;
}

static int del_chmod(const char *fbuf, mode_t mode)
{
	const char *leaf;
	int dfd = del_held_dfd(fbuf, &leaf);
	if (dfd >= 0)
		return do_chmod_atfd(dfd, leaf, mode);
	return do_chmod_at(fbuf, mode);
}

static int del_unlink(const char *fbuf)
{
	const char *leaf;
	int dfd = del_held_dfd(fbuf, &leaf);
	if (dfd >= 0 && do_unlink_atfd(dfd, leaf, 0) == 0)
		return 0;
	return robust_unlink(fbuf);	/* fall back (ETXTBSY retry, or not held) */
}

static inline int is_backup_file(char *fn)
{
	int k = strlen(fn) - backup_suffix_len;
	return k > 0 && strcmp(fn+k, backup_suffix) == 0;
}

static enum delret delete_item_with_stat(char *fbuf, uint16 mode, uint16 flags,
					STRUCT_STAT *backup_stat);

/* Record the directory before removing children changes its timestamps. */
static int directory_backup_stat(const char *path, uint16 mode, uint16 flags,
				 STRUCT_STAT *st)
{
	int error;

	if (!S_ISDIR(mode) || make_backups <= 0 || !backup_dir
	 || dry_run || (flags & (DEL_FOR_BACKUP | DEL_MAKE_ROOM)))
		return 0;

	if ((am_root < 0 ? x_lstat(path, st, NULL) : do_lstat_at(path, st)) == 0) {
		if (S_ISDIR(st->st_mode))
			return 1;
		errno = ENOTDIR;
	}
	error = errno;
	if (error != ENOENT)
		rsyserr(FERROR_XFER, error, "backup stat %s failed", full_fname(path));
	errno = error;
	return -1;
}

static void restore_directory_backup_mode(const char *path, const STRUCT_STAT *original)
{
	STRUCT_STAT current;
	const char *leaf;
	int dfd = del_held_dfd(path, &leaf);
	int ret = dfd >= 0 ? do_lstat_atfd(dfd, leaf, &current) : do_lstat_at(path, &current);

	if (ret == 0 && current.st_dev == original->st_dev && current.st_ino == original->st_ino
	 && del_chmod(path, original->st_mode) < 0)
		rsyserr(FERROR_XFER, errno, "restore permissions on %s failed", full_fname(path));
}

/* The directory is about to be deleted: if DEL_RECURSE is given, delete all
 * its contents, otherwise just checks for content.  Returns DR_SUCCESS or
 * DR_NOT_EMPTY.  Note that fname must point to a MAXPATHLEN buffer!  (The
 * buffer is used for recursion, but returned unchanged.)
 */
static enum delret delete_dir_contents(char *fname, uint16 flags, BOOL back_up_dirs)
{
	struct file_list *dirlist;
	enum delret ret;
	unsigned remainder;
	void *save_filters;
	int j, dlen;
	char *p;

	if (DEBUG_GTE(DEL, 3)) {
		rprintf(FINFO, "delete_dir_contents(%s) flags=%d\n",
			fname, flags);
	}

	dlen = strlen(fname);
	save_filters = push_local_filters(fname, dlen);

	non_perishable_cnt = 0;
	dirlist = get_dirlist(fname, dlen, 0);
	ret = non_perishable_cnt ? DR_NOT_EMPTY : DR_SUCCESS;

	if (!dirlist->used)
		goto done;

	if (!(flags & DEL_RECURSE)) {
		ret = DR_NOT_EMPTY;
		goto done;
	}

	p = fname + dlen;
	if (dlen != 1 || *fname != '/')
		*p++ = '/';
	remainder = MAXPATHLEN - (p - fname);

	/* We do our own recursion, so make delete_item() non-recursive. */
	flags = (flags & ~(DEL_RECURSE|DEL_MAKE_ROOM|DEL_NO_UID_WRITE))
	      | DEL_DIR_IS_EMPTY;

	/* Hold this dir open so the per-entry chmod/rmdir/unlink below (and in
	 * delete_item) become *at() calls against it rather than re-resolving the
	 * full path for every entry.  Save/restore around the recursion. */
	int save_del_dirfd = del_dirfd;
	const char *save_del_prefix = del_dir_prefix;
	int save_del_prefix_len = del_dir_prefix_len;
	fname[dlen] = '\0';
	del_dirfd = open_dir_secure(fname);
	fname[dlen] = '/';
	del_dir_prefix = fname;
	del_dir_prefix_len = dlen;

	for (j = dirlist->used; j--; ) {
		struct file_struct *fp = dirlist->files[j];
		STRUCT_STAT st;
		int back_up, restore_mode = 0;

		if (fp->flags & FLAG_MOUNT_DIR && S_ISDIR(fp->mode)) {
			if (DEBUG_GTE(DEL, 1)) {
				rprintf(FINFO,
					"mount point, %s, pins parent directory\n",
					f_name(fp, NULL));
			}
			ret = DR_NOT_EMPTY;
			continue;
		}

		strlcpy(p, fp->basename, remainder);
		back_up = back_up_dirs ? directory_backup_stat(fname, fp->mode, flags, &st) : 0;
		if (back_up < 0) {
			if (errno != ENOENT)
				ret = DR_NOT_EMPTY;
			continue;
		}
		if (!(fp->mode & S_IWUSR) && !am_root && fp->flags & FLAG_OWNED_BY_US)
			restore_mode = del_chmod(fname, fp->mode | S_IWUSR) == 0 && back_up;
		/* Save stack by recursing to ourself directly. */
		if (S_ISDIR(fp->mode)
		 && delete_dir_contents(fname, flags | DEL_RECURSE, back_up_dirs) != DR_SUCCESS) {
			ret = DR_NOT_EMPTY;
			if (back_up) {
				if (restore_mode)
					restore_directory_backup_mode(fname, &st);
				continue;
			}
		}
		if (delete_item_with_stat(fname, fp->mode, flags, back_up ? &st : NULL) != DR_SUCCESS) {
			if (restore_mode)
				restore_directory_backup_mode(fname, &st);
			ret = DR_NOT_EMPTY;
		}
	}

	if (del_dirfd >= 0)
		close(del_dirfd);
	del_dirfd = save_del_dirfd;
	del_dir_prefix = save_del_prefix;
	del_dir_prefix_len = save_del_prefix_len;

	fname[dlen] = '\0';

  done:
	flist_free(dirlist);
	pop_local_filters(save_filters);

	if (ret == DR_NOT_EMPTY) {
		rprintf(FINFO, "cannot delete non-empty directory: %s\n",
			fname);
	}
	return ret;
}

/* Delete a file or directory.  If DEL_RECURSE is set in the flags, this will
 * delete recursively.
 *
 * Note that fbuf must point to a MAXPATHLEN buffer if the mode indicates it's
 * a directory! (The buffer is used for recursion, but returned unchanged.)
 */
static enum delret delete_item_with_stat(char *fbuf, uint16 mode, uint16 flags,
					STRUCT_STAT *backup_stat)
{
	enum delret ret;
	char *what;
	int ok;
	int restore_mode = 0;

	if (DEBUG_GTE(DEL, 2)) {
		rprintf(FINFO, "delete_item(%s) mode=%o flags=%d\n",
			fbuf, (int)mode, (int)flags);
	}

	if (flags & DEL_NO_UID_WRITE)
		restore_mode = del_chmod(fbuf, mode | S_IWUSR) == 0 && backup_stat;

	if (S_ISDIR(mode) && !(flags & DEL_DIR_IS_EMPTY)) {
		/* This only happens on the first call to delete_item() since
		 * delete_dir_contents() always calls us w/DEL_DIR_IS_EMPTY. */
		ignore_perishable = 1;
		/* If DEL_RECURSE is not set, this just reports emptiness. */
		/* Replacing a directory must not start backing up its subdirectories. */
		ret = delete_dir_contents(fbuf, flags, backup_stat != NULL);
		ignore_perishable = 0;
		if (ret == DR_NOT_EMPTY || ret == DR_AT_LIMIT)
			goto check_ret;
		/* OK: try to delete the directory. */
	}

	if (!(flags & DEL_MAKE_ROOM) && max_delete >= 0 && stats.deleted_files >= max_delete) {
		skipped_deletes++;
		ret = DR_AT_LIMIT;
		goto check_ret;
	}

	if (S_ISDIR(mode)) {
		const char *leaf;
		int dfd = del_held_dfd(fbuf, &leaf);
		if (backup_stat && !make_directory_backup(fbuf, backup_stat)) {
			ret = DR_FAILURE;
			goto check_ret;
		}
		what = "rmdir";
		ok = (dfd >= 0 ? do_unlink_atfd(dfd, leaf, AT_REMOVEDIR) : do_rmdir_at(fbuf)) == 0;
	} else {
		if (make_backups > 0 && !(flags & DEL_FOR_BACKUP) && (backup_dir || !is_backup_file(fbuf))) {
			what = "make_backup";
			ok = make_backup(fbuf, True);
			if (ok == 2) {
				what = "unlink";
				ok = del_unlink(fbuf) == 0;
			}
		} else {
			what = "unlink";
			ok = del_unlink(fbuf) == 0;
		}
	}

	if (ok) {
		if (!(flags & DEL_MAKE_ROOM)) {
			log_delete(fbuf, mode);
			stats.deleted_files++;
			if (S_ISREG(mode)) {
				/* Nothing more to count */
			} else if (S_ISDIR(mode))
				stats.deleted_dirs++;
#ifdef SUPPORT_LINKS
			else if (S_ISLNK(mode))
				stats.deleted_symlinks++;
#endif
			else if (IS_DEVICE(mode))
				stats.deleted_devices++;
			else
				stats.deleted_specials++;
		}
		ret = DR_SUCCESS;
	} else {
		if (S_ISDIR(mode) && (errno == ENOTEMPTY || errno == EEXIST)) {
			rprintf(FINFO, "cannot delete non-empty directory: %s\n",
				fbuf);
			ret = DR_NOT_EMPTY;
		} else if (errno != ENOENT) {
			rsyserr(FERROR_XFER, errno, "delete_file: %s(%s) failed",
				what, fbuf);
			ret = DR_FAILURE;
		} else
			ret = DR_SUCCESS;
	}

  check_ret:
	if (ret != DR_SUCCESS && restore_mode)
		restore_directory_backup_mode(fbuf, backup_stat);
	if (ret != DR_SUCCESS && flags & DEL_MAKE_ROOM) {
		const char *desc;
		switch (flags & DEL_MAKE_ROOM) {
		case DEL_FOR_FILE: desc = "regular file"; break;
		case DEL_FOR_DIR: desc = "directory"; break;
		case DEL_FOR_SYMLINK: desc = "symlink"; break;
		case DEL_FOR_DEVICE: desc = "device file"; break;
		case DEL_FOR_SPECIAL: desc = "special file"; break;
		default: exit_cleanup(RERR_UNSUPPORTED); /* IMPOSSIBLE */
		}
		rprintf(FERROR_XFER, "could not make way for %s %s: %s\n",
			flags & DEL_FOR_BACKUP ? "backup" : "new",
			desc, fbuf);
	}
	return ret;
}

enum delret delete_item(char *fbuf, uint16 mode, uint16 flags)
{
	STRUCT_STAT st;
	int back_up = directory_backup_stat(fbuf, mode, flags, &st);

	if (back_up < 0)
		return errno == ENOENT ? DR_SUCCESS : DR_FAILURE;
	return delete_item_with_stat(fbuf, mode, flags, back_up ? &st : NULL);
}

uint16 get_del_for_flag(uint16 mode)
{
	if (S_ISREG(mode))
		return DEL_FOR_FILE;
	if (S_ISDIR(mode))
		return DEL_FOR_DIR;
	if (S_ISLNK(mode))
		return DEL_FOR_SYMLINK;
	if (IS_DEVICE(mode))
		return DEL_FOR_DEVICE;
	if (IS_SPECIAL(mode))
		return DEL_FOR_SPECIAL;
	exit_cleanup(RERR_UNSUPPORTED); /* IMPOSSIBLE */
}
