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
extern char *backup_suffix;
extern char *backup_dir;


/* simple backup creates a backup with a suffix in the same directory */
static int make_simple_backup(char *fname)
{
	char fnamebak[MAXPATHLEN];
	if (strlen(fname) + strlen(backup_suffix) > (MAXPATHLEN-1)) {
		rprintf(FERROR,"backup filename too long\n");
		return 0;
	}

	slprintf(fnamebak,sizeof(fnamebak),"%s%s",fname,backup_suffix);
	if (do_rename(fname,fnamebak) != 0) {
		/* cygwin (at least version b19) reports EINVAL */
		if (errno != ENOENT && errno != EINVAL) {
			rprintf(FERROR,"rename %s %s : %s\n",fname,fnamebak,strerror(errno));
			return 0;
		}
	} else if (verbose > 1) {
		rprintf(FINFO,"backed up %s to %s\n",fname,fnamebak);
	}
	return 1;
}


/* recursively make a directory path */
static int make_dir(char *name, int mask)
{
	char newdir [MAXPATHLEN];
	char *p, *d;

	/* copy pathname over, look for last '/' */
	for (p = d = newdir; *name; *d++ = *name++)
		if (*name == '/')
			p = d;
	if (p == newdir)
		return 0;
	*p = 0;

	/* make the new directory, if that fails then make its parent */
	while (do_mkdir (newdir, mask) != 0)
		if ((errno != ENOENT) || !make_dir (newdir, mask))
			return 0;

	return 1;
} /* make_dir */


/* robustly move a file, creating new directory structures if necessary */
static int robust_move(char *src, char *dst)
{
	int keep_trying = 4;
	int keep_path_extfs = 0;
	int failed;

	while (keep_trying) {
		if (keep_path_extfs)
			failed = copy_file (src, dst, 0755);
		else
			failed = robust_rename (src, dst);

		if (failed) {
			if (verbose > 2)
				rprintf (FERROR, "robust_move failed: %s(%d)\n",
					strerror (errno), errno);
			switch (errno) {
				/* external filesystem */
				case EXDEV:
					keep_path_extfs = 1;
					keep_trying--;
					break;
				/* no directory to write to */
				case ENOENT:
					make_dir (dst, 0755);
					keep_trying--;
					break;
				default:
					keep_trying = 0;
			} /* switch */
		} else
			keep_trying = 0;
	} /* while */
	return (!failed);
} /* robust_move */


/* if we have a backup_dir, then we get here from make_backup().
   We will move the file to be deleted into a parallel directory tree */
static int keep_backup(char *fname)
{
	static int initialised;

	char keep_name [MAXPATHLEN];
	STRUCT_STAT st;
	struct file_struct *file;

	if (!initialised) {
		if (backup_dir[strlen(backup_dir) - 1] == '/')
			backup_dir[strlen(backup_dir) - 1] = 0;
		if (verbose > 0)
			rprintf (FINFO, "backup_dir is %s\n", backup_dir);
		initialised = 1;
	}

	/* return if no file to keep */
#if SUPPORT_LINKS
	if (do_lstat (fname, &st)) return 1;
#else
	if (do_stat (fname, &st)) return 1;
#endif

	file = make_file (0, fname);

	/* make a complete pathname for backup file */
	if (strlen(backup_dir) + strlen(fname) > (MAXPATHLEN - 1)) {
		rprintf (FERROR, "keep_backup filename too long\n");
		return 0;
	}
	slprintf(keep_name, sizeof (keep_name), "%s/%s", backup_dir, fname);

	if (!S_ISDIR(file->mode)) {
	/* move to keep tree if a file */
		if (!robust_move (fname, keep_name))
			rprintf(FERROR, "keep_backup failed %s -> %s : %s\n",
				fname, keep_name, strerror(errno));
	} else {
	/* this bit only used to "keep" empty directories */
		/* make the parent directories */
		make_dir (keep_name, 0755);
		/* now make the (empty) directory */
		do_mkdir (keep_name, file->mode);
		if (verbose > 1)
			rprintf (FINFO, "keep_backup: made empty dir: %s\n",
				keep_name);
	}

	set_perms (keep_name, file, NULL, 0);
	free_file (file);
	free (file);
	if (verbose > 1)
		rprintf (FINFO, "keep_backup %s -> %s\n", fname, keep_name);
	return 1;
} /* keep_backup */


/* main backup switch routine */
int make_backup(char *fname)
{
	if (backup_dir)
		return (keep_backup(fname));
	else
		return (make_simple_backup(fname));
}

