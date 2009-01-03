/*
 * Backup handling code.
 *
 * Copyright (C) 1999 Andrew Tridgell
 * Copyright (C) 2003-2009 Wayne Davison
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

/* make a complete pathname for backup file */
char *get_backup_name(const char *fname)
{
	if (backup_dir) {
		if (stringjoin(backup_dir_buf + backup_dir_len, backup_dir_remainder,
			       fname, backup_suffix, NULL) < backup_dir_remainder)
			return backup_dir_buf;
	} else {
		if (stringjoin(backup_dir_buf, MAXPATHLEN,
			       fname, backup_suffix, NULL) < MAXPATHLEN)
			return backup_dir_buf;
	}

	rprintf(FERROR, "backup filename too long\n");
	return NULL;
}

/****************************************************************************
Create a directory given an absolute path, perms based upon another directory
path
****************************************************************************/
int make_bak_dir(const char *fullpath)
{
	char fbuf[MAXPATHLEN], *rel, *end, *p;
	struct file_struct *file;
	int len = backup_dir_len;
	stat_x sx;

	while (*fullpath == '.' && fullpath[1] == '/') {
		fullpath += 2;
		len -= 2;
	}

	if (strlcpy(fbuf, fullpath, sizeof fbuf) >= sizeof fbuf)
		return -1;

	rel = fbuf + len;
	end = p = rel + strlen(rel);

	/* Try to find an existing dir, starting from the deepest dir. */
	while (1) {
		if (--p == fbuf)
			return -1;
		if (*p == '/') {
			*p = '\0';
			if (mkdir_defmode(fbuf) == 0)
				break;
			if (errno != ENOENT) {
				rsyserr(FERROR, errno,
					"make_bak_dir mkdir %s failed",
					full_fname(fbuf));
				return -1;
			}
		}
	}

	/* Make all the dirs that we didn't find on the way here. */
	while (1) {
		if (p >= rel) {
			/* Try to transfer the directory settings of the
			 * actual dir that the files are coming from. */
			init_stat_x(&sx);
			if (x_stat(rel, &sx.st, NULL) < 0) {
				rsyserr(FERROR, errno,
					"make_bak_dir stat %s failed",
					full_fname(rel));
			} else {
				if (!(file = make_file(rel, NULL, NULL, 0, NO_FILTERS)))
					continue;
#ifdef SUPPORT_ACLS
				if (preserve_acls && !S_ISLNK(file->mode)) {
					get_acl(rel, &sx);
					cache_acl(file, &sx);
					free_acl(&sx);
				}
#endif
#ifdef SUPPORT_XATTRS
				if (preserve_xattrs) {
					get_xattr(rel, &sx);
					cache_xattr(file, &sx);
					free_xattr(&sx);
				}
#endif
				set_file_attrs(fbuf, file, NULL, NULL, 0);
				unmake_file(file);
			}
		}
		*p = '/';
		p += strlen(p);
		if (p == end)
			break;
		if (mkdir_defmode(fbuf) < 0) {
			rsyserr(FERROR, errno, "make_bak_dir mkdir %s failed",
				full_fname(fbuf));
			return -1;
		}
	}

	return 0;
}

/* Has same return codes as make_backup(). */
static inline int link_or_rename(const char *from, const char *to,
				 BOOL prefer_rename, STRUCT_STAT *stp)
{
	if (S_ISLNK(stp->st_mode)) {
		if (prefer_rename)
			goto do_rename;
#ifndef CAN_HARDLINK_SYMLINK
		return 0; /* Use copy code. */
#endif
	}
	if (IS_SPECIAL(stp->st_mode) || IS_DEVICE(stp->st_mode)) {
		if (prefer_rename)
			goto do_rename;
#ifndef CAN_HARDLINK_SPECIAL
		return 0; /* Use copy code. */
#endif
	}
#ifdef SUPPORT_HARD_LINKS
	if (!S_ISDIR(stp->st_mode)) {
		if (do_link(from, to) == 0)
			return 2;
		return 0;
	}
#endif
  do_rename:
	if (do_rename(from, to) == 0) {
		if (stp->st_nlink > 1 && !S_ISDIR(stp->st_mode)) {
			/* If someone has hard-linked the file into the backup
			 * dir, rename() might return success but do nothing! */
			robust_unlink(to); /* Just in case... */
		}
		return 1;
	}
	return 0;
}

/* Hard-link, rename, or copy an item to the backup name.  Returns 2 if item
 * was duplicated into backup area, 1 if item was moved, or 0 for failure.*/
int make_backup(const char *fname, BOOL prefer_rename)
{
	stat_x sx;
	struct file_struct *file;
	int save_preserve_xattrs;
	char *buf = get_backup_name(fname);
	int ret = 0;

	if (!buf)
		return 0;

	init_stat_x(&sx);
	/* Return success if no file to keep. */
	if (x_lstat(fname, &sx.st, NULL) < 0)
		return 1;

	/* Try a hard-link or a rename first.  Using rename is not atomic, but
	 * is more efficient than forcing a copy for larger files when no hard-
	 * linking is possible. */
	if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
		goto success;
	if (errno == EEXIST) {
		STRUCT_STAT bakst;
		if (do_lstat(buf, &bakst) == 0) {
			int flags = get_del_for_flag(bakst.st_mode) | DEL_FOR_BACKUP | DEL_RECURSE;
			if (delete_item(buf, bakst.st_mode, flags) != 0)
				return 0;
		}
		if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
			goto success;
	} else if (backup_dir && errno == ENOENT) {
		/* If the backup dir is missing, try again after making it. */
		if (make_bak_dir(buf) != 0)
			return 0;
		if ((ret = link_or_rename(fname, buf, prefer_rename, &sx.st)) != 0)
			goto success;
	}

	/* Fall back to making a copy. */
	if (!(file = make_file(fname, NULL, &sx.st, 0, NO_FILTERS)))
		return 1; /* the file could have disappeared */

#ifdef SUPPORT_ACLS
	if (preserve_acls && !S_ISLNK(file->mode)) {
		get_acl(fname, &sx);
		cache_acl(file, &sx);
		free_acl(&sx);
	}
#endif
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs) {
		get_xattr(fname, &sx);
		cache_xattr(file, &sx);
		free_xattr(&sx);
	}
#endif

	/* Check to see if this is a device file, or link */
	if ((am_root && preserve_devices && IS_DEVICE(file->mode))
	 || (preserve_specials && IS_SPECIAL(file->mode))) {
		int save_errno;
		if (do_mknod(buf, file->mode, sx.st.st_rdev) < 0) {
			save_errno = errno ? errno : EINVAL; /* 0 paranoia */
			if (errno == ENOENT && make_bak_dir(buf) == 0) {
				if (do_mknod(buf, file->mode, sx.st.st_rdev) < 0)
					save_errno = errno ? errno : save_errno;
				else
					save_errno = 0;
			}
			if (save_errno) {
				rsyserr(FERROR, save_errno, "mknod %s failed",
					full_fname(buf));
			}
		} else
			save_errno = 0;
		if (DEBUG_GTE(BACKUP, 1) && save_errno == 0) {
			rprintf(FINFO, "make_backup: DEVICE %s successful.\n",
				fname);
		}
		ret = 2;
	}

	if (!ret && S_ISDIR(file->mode)) {
		int ret_code;
		/* make an empty directory */
		if (do_mkdir(buf, file->mode) < 0) {
			int save_errno = errno ? errno : EINVAL; /* 0 paranoia */
			if (errno == ENOENT && make_bak_dir(buf) == 0) {
				if (do_mkdir(buf, file->mode) < 0)
					save_errno = errno ? errno : save_errno;
				else
					save_errno = 0;
			}
			if (save_errno) {
				rsyserr(FINFO, save_errno, "mkdir %s failed",
					full_fname(buf));
			}
		}

		ret_code = do_rmdir(fname);
		if (DEBUG_GTE(BACKUP, 1)) {
			rprintf(FINFO, "make_backup: RMDIR %s returns %i\n",
				full_fname(fname), ret_code);
		}
		ret = 2;
	}

#ifdef SUPPORT_LINKS
	if (!ret && preserve_links && S_ISLNK(file->mode)) {
		const char *sl = F_SYMLINK(file);
		if (safe_symlinks && unsafe_symlink(sl, buf)) {
			if (INFO_GTE(SYMSAFE, 1)) {
				rprintf(FINFO, "ignoring unsafe symlink %s -> %s\n",
					full_fname(buf), sl);
			}
			ret = 2;
		} else {
			if (do_symlink(sl, buf) < 0) {
				int save_errno = errno ? errno : EINVAL; /* 0 paranoia */
				if (errno == ENOENT && make_bak_dir(buf) == 0) {
					if (do_symlink(sl, buf) < 0)
						save_errno = errno ? errno : save_errno;
					else
						save_errno = 0;
				}
				if (save_errno) {
					rsyserr(FERROR, save_errno, "link %s -> \"%s\"",
						full_fname(buf), sl);
				}
			}
			ret = 2;
		}
	}
#endif

	if (!ret && !S_ISREG(file->mode)) {
		rprintf(FINFO, "make_bak: skipping non-regular file %s\n",
			fname);
		unmake_file(file);
		return 2;
	}

	/* Copy to backup tree if a file. */
	if (!ret) {
		if (copy_file(fname, buf, -1, file->mode, 1) < 0) {
			rsyserr(FERROR, errno, "keep_backup failed: %s -> \"%s\"",
				full_fname(fname), buf);
			unmake_file(file);
			return 0;
		}
		ret = 2;
	}

	save_preserve_xattrs = preserve_xattrs;
	preserve_xattrs = 0;
	set_file_attrs(buf, file, NULL, fname, 0);
	preserve_xattrs = save_preserve_xattrs;

	unmake_file(file);

  success:
	if (INFO_GTE(BACKUP, 1)) {
		rprintf(FINFO, "backed up %s to %s\n",
			fname, buf);
	}
	return ret;
}
