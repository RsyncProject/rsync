/*
 * Backup handling code.
 *
 * Copyright (C) 1999 Andrew Tridgell
 * Copyright (C) 2003-2015 Wayne Davison
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
extern int preserve_acls;
extern int preserve_xattrs;
extern int preserve_devices;
extern int preserve_specials;
extern int preserve_links;
extern int safe_symlinks;
extern int backup_dir_len;
extern unsigned int backup_dir_remainder;
extern char backup_dir_buf[MAXPATHLEN];
extern char *backup_suffix;
extern char *backup_dir;

/* Returns -1 on error, 0 on missing dir, and 1 on present dir. */
static int validate_backup_dir(void)
{
	STRUCT_STAT st;

	if (do_lstat(backup_dir_buf, &st) < 0) {
		if (errno == ENOENT)
			return 0;
		rsyserr(FERROR, errno, "backup lstat %s failed", backup_dir_buf);
		return -1;
	}
	if (!S_ISDIR(st.st_mode)) {
		int flags = get_del_for_flag(st.st_mode) | DEL_FOR_BACKUP | DEL_RECURSE;
		if (delete_item(backup_dir_buf, st.st_mode, flags) == 0)
			return 0;
		return -1;
	}
	return 1;
}

/* Create a backup path from the given fname, putting the result into
 * backup_dir_buf.  Any new directories (compared to the prior backup
 * path) are ensured to exist as directories, replacing anything else
 * that may be in the way (e.g. a symlink). */
static BOOL copy_valid_path(const char *fname)
{
	const char *f;
	int val;
	BOOL ret = True;
	stat_x sx;
	char *b, *rel = backup_dir_buf + backup_dir_len, *name = rel;

	for (f = fname, b = rel; *f && *f == *b; f++, b++) {
		if (*b == '/')
			name = b + 1;
	}

	if (stringjoin(rel, backup_dir_remainder, fname, backup_suffix, NULL) >= backup_dir_remainder) {
		rprintf(FERROR, "backup filename too long\n");
		*name = '\0';
		return False;
	}

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

		while (do_mkdir(backup_dir_buf, ACCESSPERMS) < 0) {
			if (errno == EEXIST) {
				val = validate_backup_dir();
				if (val > 0)
					break;
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
				continue;
#ifdef SUPPORT_ACLS
			if (preserve_acls && !S_ISLNK(file->mode)) {
				get_acl(rel, &sx);
				cache_tmp_acl(file, &sx);
				free_acl(&sx);
			}
#endif
#ifdef SUPPORT_XATTRS
			if (preserve_xattrs) {
				get_xattr(rel, &sx);
				cache_tmp_xattr(file, &sx);
				free_xattr(&sx);
			}
#endif
			set_file_attrs(backup_dir_buf, file, NULL, NULL, 0);
			unmake_file(file);
		}

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

/* Make a complete pathname for backup file and verify any new path elements. */
char *get_backup_name(const char *fname)
{
	if (backup_dir) {
		static int initialized = 0;
		if (!initialized) {
			int ret;
			if (backup_dir_len > 1)
				backup_dir_buf[backup_dir_len-1] = '\0';
			ret = make_path(backup_dir_buf, 0);
			if (backup_dir_len > 1)
				backup_dir_buf[backup_dir_len-1] = '/';
			if (ret < 0)
				return NULL;
			initialized = 1;
		}
		/* copy fname into backup_dir_buf while validating the dirs. */
		if (copy_valid_path(fname))
			return backup_dir_buf;
		/* copy_valid_path() has printed an error message. */
		return NULL;
	}

	if (stringjoin(backup_dir_buf, MAXPATHLEN, fname, backup_suffix, NULL) < MAXPATHLEN)
		return backup_dir_buf;

	rprintf(FERROR, "backup filename too long\n");
	return NULL;
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
		if (do_link(from, to) == 0) {
			if (DEBUG_GTE(BACKUP, 1))
				rprintf(FINFO, "make_backup: HLINK %s successful.\n", from);
			return 2;
		}
		/* We prefer to rename a regular file rather than copy it. */
		if (!S_ISREG(stp->st_mode) || errno == EEXIST || errno == EISDIR)
			return 0;
	}
#endif
	if (do_rename(from, to) == 0) {
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
int make_backup(const char *fname, BOOL prefer_rename)
{
	stat_x sx;
	struct file_struct *file;
	int save_preserve_xattrs;
	char *buf;
	int ret = 0;

	init_stat_x(&sx);
	/* Return success if no file to keep. */
	if (x_lstat(fname, &sx.st, NULL) < 0)
		return 3;

	if (!(buf = get_backup_name(fname)))
		return 0;

	/* Try a hard-link or a rename first.  Using rename is not atomic, but
	 * is more efficient than forcing a copy for larger files when no hard-
	 * linking is possible. */
	if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
		goto success;
	if (errno == EEXIST || errno == EISDIR) {
		STRUCT_STAT bakst;
		if (do_lstat(buf, &bakst) == 0) {
			int flags = get_del_for_flag(bakst.st_mode) | DEL_FOR_BACKUP | DEL_RECURSE;
			if (delete_item(buf, bakst.st_mode, flags) != 0)
				return 0;
		}
		if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
			goto success;
	}

	/* Fall back to making a copy. */
	if (!(file = make_file(fname, NULL, &sx.st, 0, NO_FILTERS)))
		return 3; /* the file could have disappeared */

#ifdef SUPPORT_ACLS
	if (preserve_acls && !S_ISLNK(file->mode)) {
		get_acl(fname, &sx);
		cache_tmp_acl(file, &sx);
		free_acl(&sx);
	}
#endif
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs) {
		get_xattr(fname, &sx);
		cache_tmp_xattr(file, &sx);
		free_xattr(&sx);
	}
#endif

	/* Check to see if this is a device file, or link */
	if ((am_root && preserve_devices && IS_DEVICE(file->mode))
	 || (preserve_specials && IS_SPECIAL(file->mode))) {
		if (do_mknod(buf, file->mode, sx.st.st_rdev) < 0)
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
			if (do_symlink(sl, buf) < 0)
				rsyserr(FERROR, errno, "link %s -> \"%s\"", full_fname(buf), sl);
			else if (DEBUG_GTE(BACKUP, 1))
				rprintf(FINFO, "make_backup: SYMLINK %s successful.\n", fname);
			ret = 2;
		}
	}
#endif

	if (!ret && !S_ISREG(file->mode)) {
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
	set_file_attrs(buf, file, NULL, fname, 0);
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
