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

extern int verbose;
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

/* simple backup creates a backup with a suffix in the same directory */
static int make_simple_backup(const char *fname)
{
	int rename_errno;
	const char *fnamebak = get_backup_name(fname);

	if (!fnamebak)
		return 0;

	while (1) {
		if (do_rename(fname, fnamebak) == 0) {
			if (verbose > 1) {
				rprintf(FINFO, "backed up %s to %s\n",
					fname, fnamebak);
			}
			break;
		}
		/* cygwin (at least version b19) reports EINVAL */
		if (errno == ENOENT || errno == EINVAL)
			break;

		rename_errno = errno;
		if (errno == EISDIR && do_rmdir(fnamebak) == 0)
			continue;
		if (errno == ENOTDIR && do_unlink(fnamebak) == 0)
			continue;

		rsyserr(FERROR, rename_errno, "rename %s to backup %s",
			fname, fnamebak);
		errno = rename_errno;
		return 0;
	}

	return 1;
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
			if (x_stat(rel, &sx.st, NULL) < 0) {
				rsyserr(FERROR, errno,
					"make_bak_dir stat %s failed",
					full_fname(rel));
			} else {
#ifdef SUPPORT_ACLS
				sx.acc_acl = sx.def_acl = NULL;
#endif
#ifdef SUPPORT_XATTRS
				sx.xattr = NULL;
#endif
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
				set_file_attrs(fbuf, file, NULL, NULL, 0);
				unmake_file(file);
#ifdef SUPPORT_ACLS
				uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
				uncache_tmp_xattrs();
#endif
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

/* robustly move a file, creating new directory structures if necessary */
static int robust_move(const char *src, char *dst)
{
	if (robust_rename(src, dst, NULL, 0755) < 0) {
		int save_errno = errno ? errno : EINVAL; /* 0 paranoia */
		if (errno == ENOENT && make_bak_dir(dst) == 0) {
			if (robust_rename(src, dst, NULL, 0755) < 0)
				save_errno = errno ? errno : save_errno;
			else
				save_errno = 0;
		}
		if (save_errno) {
			errno = save_errno;
			return -1;
		}
	}
	return 0;
}


/* If we have a --backup-dir, then we get here from make_backup().
 * We will move the file to be deleted into a parallel directory tree. */
static int keep_backup(const char *fname)
{
	stat_x sx;
	struct file_struct *file;
	char *buf;
	int save_preserve_xattrs = preserve_xattrs;
	int kept = 0;
	int ret_code;

	/* return if no file to keep */
	if (x_lstat(fname, &sx.st, NULL) < 0)
		return 1;
#ifdef SUPPORT_ACLS
	sx.acc_acl = sx.def_acl = NULL;
#endif
#ifdef SUPPORT_XATTRS
	sx.xattr = NULL;
#endif

	if (!(file = make_file(fname, NULL, NULL, 0, NO_FILTERS)))
		return 1; /* the file could have disappeared */

	if (!(buf = get_backup_name(fname))) {
		unmake_file(file);
#ifdef SUPPORT_ACLS
		uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
		uncache_tmp_xattrs();
#endif
		return 0;
	}

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
		int save_errno;
		do_unlink(buf);
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
		if (verbose > 2 && save_errno == 0) {
			rprintf(FINFO, "make_backup: DEVICE %s successful.\n",
				fname);
		}
		kept = 1;
		do_unlink(fname);
	}

	if (!kept && S_ISDIR(file->mode)) {
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
		if (verbose > 2) {
			rprintf(FINFO, "make_backup: RMDIR %s returns %i\n",
				full_fname(fname), ret_code);
		}
		kept = 1;
	}

#ifdef SUPPORT_LINKS
	if (!kept && preserve_links && S_ISLNK(file->mode)) {
		const char *sl = F_SYMLINK(file);
		if (safe_symlinks && unsafe_symlink(sl, fname)) {
			if (verbose) {
				rprintf(FINFO, "not backing up unsafe symlink \"%s\" -> \"%s\"\n",
					fname, sl);
			}
			kept = 1;
		} else {
			do_unlink(buf);
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
			do_unlink(fname);
			kept = 1;
		}
	}
#endif

	if (!kept && !S_ISREG(file->mode)) {
		rprintf(FINFO, "make_bak: skipping non-regular file %s\n",
			fname);
		unmake_file(file);
#ifdef SUPPORT_ACLS
		uncache_tmp_acls();
#endif
#ifdef SUPPORT_XATTRS
		uncache_tmp_xattrs();
#endif
		return 1;
	}

	/* move to keep tree if a file */
	if (!kept) {
		if (robust_move(fname, buf) != 0) {
			rsyserr(FERROR, errno, "keep_backup failed: %s -> \"%s\"",
				full_fname(fname), buf);
		} else if (sx.st.st_nlink > 1) {
			/* If someone has hard-linked the file into the backup
			 * dir, rename() might return success but do nothing! */
			robust_unlink(fname); /* Just in case... */
		}
	}
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

	if (verbose > 1) {
		rprintf(FINFO, "backed up %s to %s\n",
			fname, buf);
	}
	return 1;
}


/* main backup switch routine */
int make_backup(const char *fname)
{
	if (backup_dir)
		return keep_backup(fname);
	return make_simple_backup(fname);
}
