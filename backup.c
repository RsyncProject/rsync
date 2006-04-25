/*
 * Backup handling code.
 *
 * Copyright (C) 1999 Andrew Tridgell
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
extern int backup_dir_len;
extern unsigned int backup_dir_remainder;
extern char backup_dir_buf[MAXPATHLEN];
extern char *backup_suffix;
extern char *backup_dir;

extern int am_root;
extern int preserve_devices;
extern int preserve_specials;
extern int preserve_links;
extern int safe_symlinks;

/* make a complete pathname for backup file */
char *get_backup_name(char *fname)
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
static int make_simple_backup(char *fname)
{
	int rename_errno;
	char *fnamebak = get_backup_name(fname);

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
static int make_bak_dir(char *fullpath)
{
	STRUCT_STAT st;
	char *rel = fullpath + backup_dir_len;
	char *end = rel + strlen(rel);
	char *p = end;

	while (strncmp(fullpath, "./", 2) == 0)
		fullpath += 2;

	/* Try to find an existing dir, starting from the deepest dir. */
	while (1) {
		if (--p == fullpath) {
			p += strlen(p);
			goto failure;
		}
		if (*p == '/') {
			*p = '\0';
			if (mkdir_defmode(fullpath) == 0)
				break;
			if (errno != ENOENT) {
				rsyserr(FERROR, errno,
					"make_bak_dir mkdir %s failed",
					full_fname(fullpath));
				goto failure;
			}
		}
	}

	/* Make all the dirs that we didn't find on the way here. */
	while (1) {
		if (p >= rel) {
			/* Try to transfer the directory settings of the
			 * actual dir that the files are coming from. */
			if (do_stat(rel, &st) < 0) {
				rsyserr(FERROR, errno,
					"make_bak_dir stat %s failed",
					full_fname(rel));
			} else {
				do_lchown(fullpath, st.st_uid, st.st_gid);
				do_chmod(fullpath, st.st_mode);
			}
		}
		*p = '/';
		p += strlen(p);
		if (p == end)
			break;
		if (mkdir_defmode(fullpath) < 0) {
			rsyserr(FERROR, errno, "make_bak_dir mkdir %s failed",
				full_fname(fullpath));
			goto failure;
		}
	}
	return 0;

  failure:
	while (p != end) {
		*p = '/';
		p += strlen(p);
	}
	return -1;
}

/* robustly move a file, creating new directory structures if necessary */
static int robust_move(char *src, char *dst)
{
	if (robust_rename(src, dst, NULL, 0755) < 0
	 && (errno != ENOENT || make_bak_dir(dst) < 0
	  || robust_rename(src, dst, NULL, 0755) < 0))
		return -1;
	return 0;
}


/* If we have a --backup-dir, then we get here from make_backup().
 * We will move the file to be deleted into a parallel directory tree. */
static int keep_backup(char *fname)
{
	STRUCT_STAT st;
	struct file_struct *file;
	char *buf;
	int kept = 0;
	int ret_code;

	/* return if no file to keep */
	if (do_lstat(fname, &st) < 0)
		return 1;

	if (!(file = make_file(fname, NULL, NULL, 0, NO_FILTERS)))
		return 1; /* the file could have disappeared */

	if (!(buf = get_backup_name(fname)))
		return 0;

	/* Check to see if this is a device file, or link */
	if ((am_root && preserve_devices && IS_DEVICE(file->mode))
	 || (preserve_specials && IS_SPECIAL(file->mode))) {
		do_unlink(buf);
		if (do_mknod(buf, file->mode, file->u.rdev) < 0
		    && (errno != ENOENT || make_bak_dir(buf) < 0
		     || do_mknod(buf, file->mode, file->u.rdev) < 0)) {
			rsyserr(FERROR, errno, "mknod %s failed",
				full_fname(buf));
		} else if (verbose > 2) {
			rprintf(FINFO, "make_backup: DEVICE %s successful.\n",
				fname);
		}
		kept = 1;
		do_unlink(fname);
	}

	if (!kept && S_ISDIR(file->mode)) {
		/* make an empty directory */
		if (do_mkdir(buf, file->mode) < 0
		    && (errno != ENOENT || make_bak_dir(buf) < 0
		     || do_mkdir(buf, file->mode) < 0)) {
			rsyserr(FINFO, errno, "mkdir %s failed",
				full_fname(buf));
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
		if (safe_symlinks && unsafe_symlink(file->u.link, buf)) {
			if (verbose) {
				rprintf(FINFO, "ignoring unsafe symlink %s -> %s\n",
					full_fname(buf), file->u.link);
			}
			kept = 1;
		} else {
			do_unlink(buf);
			if (do_symlink(file->u.link, buf) < 0
			    && (errno != ENOENT || make_bak_dir(buf) < 0
			     || do_symlink(file->u.link, buf) < 0)) {
				rsyserr(FERROR, errno, "link %s -> \"%s\"",
					full_fname(buf),
					file->u.link);
			}
			do_unlink(fname);
			kept = 1;
		}
	}
#endif

	if (!kept && !S_ISREG(file->mode)) {
		rprintf(FINFO, "make_bak: skipping non-regular file %s\n",
			fname);
		return 1;
	}

	/* move to keep tree if a file */
	if (!kept) {
		if (robust_move(fname, buf) != 0) {
			rsyserr(FERROR, errno, "keep_backup failed: %s -> \"%s\"",
				full_fname(fname), buf);
		} else if (st.st_nlink > 1) {
			/* If someone has hard-linked the file into the backup
			 * dir, rename() might return success but do nothing! */
			robust_unlink(fname); /* Just in case... */
		}
	}
	set_file_attrs(buf, file, NULL, 0);
	free(file);

	if (verbose > 1) {
		rprintf(FINFO, "backed up %s to %s\n",
			fname, buf);
	}
	return 1;
}


/* main backup switch routine */
int make_backup(char *fname)
{
	if (backup_dir)
		return keep_backup(fname);
	return make_simple_backup(fname);
}
