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
extern int suffix_specified;
extern char *backup_suffix;
extern char *backup_dir;


extern int am_root;
extern int preserve_devices;
extern int preserve_links;
extern int preserve_hard_links;

/* simple backup creates a backup with a suffix in the same directory */
static int make_simple_backup(char *fname)
{
	char fnamebak[MAXPATHLEN];
	if (strlen(fname) + strlen(backup_suffix) > (MAXPATHLEN-1)) {
		rprintf(FERROR,"backup filename too long\n");
		return 0;
	}

	snprintf(fnamebak,sizeof(fnamebak),"%s%s",fname,backup_suffix);
	if (do_rename(fname,fnamebak) != 0) {
		/* cygwin (at least version b19) reports EINVAL */
		if (errno != ENOENT && errno != EINVAL) {
			rsyserr(FERROR, errno, "rename %s to backup %s", fname, fnamebak);
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


/****************************************************************************
Create a directory given an absolute path, perms based upon another directory
path
****************************************************************************/
static int make_bak_dir(char *fname,char *bak_path)
{
        STRUCT_STAT st;
        STRUCT_STAT *st2;
        char fullpath[MAXPATHLEN];
        extern int orig_umask;
        char *p;
        char *q;

        while(strncmp(bak_path,"./",2)==0) bak_path += 2;

        if(bak_path[strlen(bak_path)-1]!='/') {
                snprintf(fullpath,sizeof(fullpath),"%s/",bak_path);
        } else {
                snprintf(fullpath,sizeof(fullpath),"%s",bak_path);
        }
        p=fullpath;
        q=&fullpath[strlen(fullpath)];  /* End of bak_path string */
        strcat(fullpath,fname);

        /* Make the directories */
        while ((p=strchr(p,'/'))) {
                *p = 0;
                if(do_lstat(fullpath,&st)!=0) {
                        do_mkdir(fullpath,0777 & ~orig_umask);
                        if(p>q) {
                                if(do_lstat(q,&st)!=0) {
                                        rprintf(FERROR,"make_bak_dir stat %s : %s\n",fullpath,strerror(errno));
                                } else {
                                        st2=&st;
                                        set_modtime(fullpath,st2->st_mtime);
                                        if(do_lchown(fullpath,st2->st_uid,st2->st_gid)!=0) {
                                                rprintf(FERROR,"make_bak_dir chown %s : %s\n",fullpath,strerror(errno));
                                        };
                                        if(do_chmod(fullpath,st2->st_mode)!=0) {
                                                rprintf(FERROR,"make_bak_dir failed to set permissions on %s : %s\n",fullpath,strerror(errno));
                                        };
                                };
                        }
                };
                *p = '/';
                p++;
        }
        return 0;
}

/* robustly move a file, creating new directory structures if necessary */
static int robust_move(char *src, char *dst)
{
	int keep_trying = 4;
	int keep_path_extfs = 0;
	int failed;

	while (keep_trying) {
		if (keep_path_extfs) {
			failed = copy_file(src, dst, 0755);
			if (!failed) {
				do_unlink(src);
			}
		} else {
			failed = robust_rename (src, dst);
		}

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

	int kept=0;
	int ret_code;

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

	file = make_file(-1, fname, NULL, 1);

	/* the file could have disappeared */
	if (!file) return 1;

        /* make a complete pathname for backup file */
        if (strlen(backup_dir) + strlen(fname) + 
		(suffix_specified ? strlen(backup_suffix) : 0) > (MAXPATHLEN - 1)) {
                rprintf (FERROR, "keep_backup filename too long\n");
                return 0;
        }

	if (suffix_specified) {
        	snprintf(keep_name, sizeof (keep_name), "%s/%s%s", backup_dir, fname, backup_suffix);
		} else {
        	snprintf(keep_name, sizeof (keep_name), "%s/%s", backup_dir, fname);
		}


#ifdef HAVE_MKNOD
	/* Check to see if this is a device file, or link */
        if(IS_DEVICE(file->mode)) {
                if(am_root && preserve_devices) {
                        make_bak_dir(fname,backup_dir);
                        if(do_mknod(keep_name,file->mode,file->rdev)!=0) {
                                rprintf(FERROR,"mknod %s : %s\n",keep_name,strerror(errno));
                        } else {
                                if(verbose>2)
                                        rprintf(FINFO,"make_backup : DEVICE %s successful.\n",fname);
                        };
                };
		kept=1;
                do_unlink(fname);
        };
#endif

	if(!kept && S_ISDIR(file->mode)) {
		/* make an empty directory */
                make_bak_dir(fname,backup_dir);
                do_mkdir(keep_name,file->mode);
                ret_code=do_rmdir(fname);
                if(verbose>2)
                        rprintf(FINFO,"make_backup : RMDIR %s returns %i\n",fname,ret_code);
		kept=1;
        };

#if SUPPORT_LINKS
        if(!kept && preserve_links && S_ISLNK(file->mode)) {
                extern int safe_symlinks;
                if (safe_symlinks && unsafe_symlink(file->link, keep_name)) {
                        if (verbose) {
                                rprintf(FINFO,"ignoring unsafe symlink %s -> %s\n",
                                        keep_name,file->link);
                        }
			kept=1;
                }
                make_bak_dir(fname,backup_dir);
                if(do_symlink(file->link,keep_name) != 0) {
                        rprintf(FERROR,"link %s -> %s : %s\n",keep_name,file->link,strerror(errno));
                };
                do_unlink(fname);
		kept=1;
        };
#endif
        if(!kept && preserve_hard_links && check_hard_link(file)) {
                if(verbose > 1) rprintf(FINFO,"%s is a hard link\n",f_name(file));
        };

        if(!kept && !S_ISREG(file->mode)) {
                rprintf(FINFO,"make_bak: skipping non-regular file %s\n",fname);
        }

	/* move to keep tree if a file */
	if(!kept) {
		if (!robust_move (fname, keep_name))
			rprintf(FERROR, "keep_backup failed %s -> %s : %s\n",
				fname, keep_name, strerror(errno));
	};
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

