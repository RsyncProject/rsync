/*
   Copyright (C) Andrew Tridgell 1999

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/* backup handling code */

#include "rsync.h"

extern int verbose;
extern int backup_suffix_len;
extern int backup_dir_len;
extern unsigned int backup_dir_remainder;
extern char backup_dir_buf[MAXPATHLEN];
extern char *backup_suffix;
extern char *backup_dir;

extern int am_root;
extern int preserve_devices;
extern int preserve_links;
extern int preserve_hard_links;
extern int orig_umask;

/* simple backup creates a backup with a suffix in the same directory */
static int make_simple_backup(char *fname)
{
	char fnamebak[MAXPATHLEN];

	if (stringjoin(fnamebak, sizeof fnamebak, fname, backup_suffix, NULL)
	    >= sizeof fnamebak) {
		rprintf(FERROR, "backup filename too long\n");
		return 0;
	}

	if (do_rename(fname, fnamebak) != 0) {
		/* cygwin (at least version b19) reports EINVAL */
		if (errno != ENOENT && errno != EINVAL) {
			rsyserr(FERROR, errno, "rename %s to backup %s", fname, fnamebak);
			return 0;
		}
	} else if (verbose > 1) {
		rprintf(FINFO, "backed up %s to %s\n", fname, fnamebak);
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
			if (do_mkdir(fullpath, 0777 & ~orig_umask) == 0)
				break;
			if (errno != ENOENT) {
				rprintf(FERROR,
				    "make_bak_dir mkdir %s failed: %s\n",
				    full_fname(fullpath), strerror(errno));
				goto failure;
			}
		}
	}

	/* Make all the dirs that we didn't find on the way here. */
	while (1) {
		if (p >= rel) {
			/* Try to transfer the directory settings of the
			 * actual dir that the files are coming from. */
			if (do_lstat(rel, &st) != 0) {
				rprintf(FERROR,
				    "make_bak_dir stat %s failed: %s\n",
				    full_fname(rel), strerror(errno));
			} else {
				set_modtime(fullpath, st.st_mtime);
				do_lchown(fullpath, st.st_uid, st.st_gid);
				do_chmod(fullpath, st.st_mode);
			}
		}
		*p = '/';
		p += strlen(p);
		if (p == end)
			break;
		if (do_mkdir(fullpath, 0777 & ~orig_umask) < 0) {
			rprintf(FERROR,
			    "make_bak_dir mkdir %s failed: %s\n",
			    full_fname(fullpath), strerror(errno));
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
	if (robust_rename(src, dst, 0755) < 0 && (errno != ENOENT
	    || make_bak_dir(dst) < 0 || robust_rename(src, dst, 0755) < 0))
		return -1;
	return 0;
}


/* If we have a --backup-dir, then we get here from make_backup().
 * We will move the file to be deleted into a parallel directory tree. */
static int keep_backup(char *fname)
{
	STRUCT_STAT st;
	struct file_struct *file;
	int kept = 0;
	int ret_code;

	/* return if no file to keep */
#if SUPPORT_LINKS
	if (do_lstat(fname, &st)) return 1;
#else
	if (do_stat(fname, &st)) return 1;
#endif

	file = make_file(fname, NULL, NO_EXCLUDES);

	/* the file could have disappeared */
	if (!file) return 1;

	/* make a complete pathname for backup file */
	if (stringjoin(backup_dir_buf + backup_dir_len, backup_dir_remainder,
	    fname, backup_suffix, NULL) >= backup_dir_remainder) {
		rprintf(FERROR, "keep_backup filename too long\n");
		return 0;
	}

#ifdef HAVE_MKNOD
	/* Check to see if this is a device file, or link */
	if (IS_DEVICE(file->mode)) {
		if (am_root && preserve_devices) {
			make_bak_dir(backup_dir_buf);
			if (do_mknod(backup_dir_buf, file->mode, file->u.rdev) != 0) {
				rprintf(FERROR, "mknod %s failed: %s\n",
					full_fname(backup_dir_buf), strerror(errno));
			} else if (verbose > 2) {
				rprintf(FINFO,
					"make_backup: DEVICE %s successful.\n",
					fname);
			}
		}
		kept = 1;
		do_unlink(fname);
	}
#endif

	if (!kept && S_ISDIR(file->mode)) {
		/* make an empty directory */
		make_bak_dir(backup_dir_buf);
		do_mkdir(backup_dir_buf, file->mode);
		ret_code = do_rmdir(fname);

		if (verbose > 2) {
			rprintf(FINFO, "make_backup: RMDIR %s returns %i\n",
				full_fname(fname), ret_code);
		}
		kept = 1;
	}

#if SUPPORT_LINKS
	if (!kept && preserve_links && S_ISLNK(file->mode)) {
		extern int safe_symlinks;
		if (safe_symlinks && unsafe_symlink(file->u.link, backup_dir_buf)) {
			if (verbose) {
				rprintf(FINFO, "ignoring unsafe symlink %s -> %s\n",
					full_fname(backup_dir_buf), file->u.link);
			}
			kept = 1;
		}
		make_bak_dir(backup_dir_buf);
		if (do_symlink(file->u.link, backup_dir_buf) != 0) {
			rprintf(FERROR, "link %s -> %s : %s\n",
				full_fname(backup_dir_buf), file->u.link, strerror(errno));
		}
		do_unlink(fname);
		kept = 1;
	}
#endif

	if (!kept && !S_ISREG(file->mode)) {
		rprintf(FINFO, "make_bak: skipping non-regular file %s\n",
			fname);
	}

	/* move to keep tree if a file */
	if (!kept) {
		if (robust_move(fname, backup_dir_buf) != 0) {
			rprintf(FERROR, "keep_backup failed: %s -> \"%s\": %s\n",
				full_fname(fname), backup_dir_buf, strerror(errno));
		}
	}
	set_perms(backup_dir_buf, file, NULL, 0);
	free(file);

	if (verbose > 1)
		rprintf(FINFO, "keep_backup %s -> %s\n", fname, backup_dir_buf);
	return 1;
}


/* main backup switch routine */
int make_backup(char *fname)
{
	if (backup_dir)
		return keep_backup(fname);
	return make_simple_backup(fname);
}
