/*
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996

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

/* this file contains code used by more than one part of the rsync
   process */

#include "rsync.h"

extern int verbose;
extern int dry_run;
extern int preserve_times;
extern int am_root;
extern int am_sender;
extern int am_generator;
extern int preserve_uid;
extern int preserve_gid;
extern int force_delete;
extern int recurse;
extern int keep_dirlinks;
extern int make_backups;
extern char *backup_dir;
extern int inplace;


/*
  free a sums struct
  */
void free_sums(struct sum_struct *s)
{
	if (s->sums) free(s->sums);
	free(s);
}


/*
 * delete a file or directory. If force_delete is set then delete
 * recursively
 */
int delete_file(char *fname)
{
	DIR *d;
	struct dirent *di;
	char buf[MAXPATHLEN];
	STRUCT_STAT st;
	int ret;

#if SUPPORT_LINKS
	ret = do_lstat(fname, &st);
#else
	ret = do_stat(fname, &st);
#endif
	if (ret)
		return -1;

	if (!S_ISDIR(st.st_mode)) {
		if (robust_unlink(fname) == 0 || errno == ENOENT)
			return 0;
		rsyserr(FERROR, errno, "delete_file: unlink %s failed",
			full_fname(fname));
		return -1;
	}

	if (do_rmdir(fname) == 0 || errno == ENOENT)
		return 0;
	if (!force_delete || !recurse
	    || (errno != ENOTEMPTY && errno != EEXIST)) {
		rsyserr(FERROR, errno, "delete_file: rmdir %s failed",
			full_fname(fname));
		return -1;
	}

	/* now we do a recsursive delete on the directory ... */
	if (!(d = opendir(fname))) {
		rsyserr(FERROR, errno, "delete_file: opendir %s failed",
			full_fname(fname));
		return -1;
	}

	for (errno = 0, di = readdir(d); di; errno = 0, di = readdir(d)) {
		char *dname = d_name(di);
		if (dname[0] == '.' && (dname[1] == '\0'
		    || (dname[1] == '.' && dname[2] == '\0')))
			continue;
		pathjoin(buf, sizeof buf, fname, dname);
		if (verbose > 0)
			rprintf(FINFO, "deleting %s\n", safe_fname(buf));
		if (delete_file(buf) != 0) {
			closedir(d);
			return -1;
		}
	}
	if (errno) {
		rsyserr(FERROR, errno, "delete_file: readdir %s failed",
			full_fname(fname));
		closedir(d);
		return -1;
	}

	closedir(d);

	if (do_rmdir(fname) != 0) {
		rsyserr(FERROR, errno, "delete_file: rmdir %s failed",
			full_fname(fname));
		return -1;
	}

	return 0;
}

int set_perms(char *fname,struct file_struct *file,STRUCT_STAT *st,
	      int flags)
{
	int updated = 0;
	STRUCT_STAT st2;
	int change_uid, change_gid;

	if (dry_run)
		return 0;

	if (!st) {
		if (link_stat(fname, &st2, 0) < 0) {
			rsyserr(FERROR, errno, "stat %s failed",
				full_fname(fname));
			return 0;
		}
		st = &st2;
	}

	if (!preserve_times || S_ISLNK(st->st_mode)
	    || (make_backups && !backup_dir && S_ISDIR(st->st_mode)))
		flags |= PERMS_SKIP_MTIME;
	if (!(flags & PERMS_SKIP_MTIME)
	    && cmp_modtime(st->st_mtime, file->modtime) != 0) {
		/* don't complain about not setting times on directories
		 * because some filesystems can't do it */
		if (set_modtime(fname,file->modtime) != 0 &&
		    !S_ISDIR(st->st_mode)) {
			rsyserr(FERROR, errno, "failed to set times on %s",
				full_fname(fname));
			return 0;
		}
		updated = 1;
	}

	change_uid = am_root && preserve_uid && st->st_uid != file->uid;
	change_gid = preserve_gid && file->gid != GID_NONE
		&& st->st_gid != file->gid;
	if (change_uid || change_gid) {
		if (verbose > 2) {
			if (change_uid) {
				rprintf(FINFO,
				    "set uid of %s from %ld to %ld\n",
				    fname, (long)st->st_uid, (long)file->uid);
			}
			if (change_gid) {
				rprintf(FINFO,
				    "set gid of %s from %ld to %ld\n",
				    fname, (long)st->st_gid, (long)file->gid);
			}
		}
		if (do_lchown(fname,
		    change_uid ? file->uid : st->st_uid,
		    change_gid ? file->gid : st->st_gid) != 0) {
			/* shouldn't have attempted to change uid or gid
			 * unless have the privilege */
			rsyserr(FERROR, errno, "%s %s failed",
			    change_uid ? "chown" : "chgrp",
			    full_fname(fname));
			return 0;
		}
		/* a lchown had been done - we have to re-stat if the
                 * destination had the setuid or setgid bits set due
                 * to the side effect of the chown call */
		if (st->st_mode & (S_ISUID | S_ISGID)) {
			link_stat(fname, st,
				  keep_dirlinks && S_ISDIR(st->st_mode));
		}
		updated = 1;
	}

#ifdef HAVE_CHMOD
	if (!S_ISLNK(st->st_mode)) {
		if ((st->st_mode & CHMOD_BITS) != (file->mode & CHMOD_BITS)) {
			updated = 1;
			if (do_chmod(fname,(file->mode & CHMOD_BITS)) != 0) {
				rsyserr(FERROR, errno, "failed to set permissions on %s",
					full_fname(fname));
				return 0;
			}
		}
	}
#endif

	if (verbose > 1 && flags & PERMS_REPORT) {
		if (updated)
			rprintf(FINFO,"%s\n",fname);
		else
			rprintf(FINFO,"%s is uptodate\n",fname);
	}
	return updated;
}


void sig_int(void)
{
	/* KLUGE: if the user hits Ctrl-C while ssh is prompting
	 * for a password, then our cleanup's sending of a SIGUSR1
	 * signal to all our children may kill ssh before it has a
	 * chance to restore the tty settings (i.e. turn echo back
	 * on).  By sleeping for a short time, ssh gets a bigger
	 * chance to do the right thing.  If child processes are
	 * not ssh waiting for a password, then this tiny delay
	 * shouldn't hurt anything. */
	msleep(400);
	exit_cleanup(RERR_SIGNAL);
}


/* finish off a file transfer, renaming the file and setting the permissions
   and ownership */
void finish_transfer(char *fname, char *fnametmp, struct file_struct *file,
		     int ok_to_set_time)
{
	int ret;

	if (inplace) {
		if (verbose > 2)
			rprintf(FINFO, "finishing %s\n", fname);
		goto do_set_perms;
	}

	if (make_backups && !make_backup(fname))
		return;

	/* Change permissions before putting the file into place. */
	set_perms(fnametmp, file, NULL, ok_to_set_time ? 0 : PERMS_SKIP_MTIME);

	/* move tmp file over real file */
	if (verbose > 2)
		rprintf(FINFO, "renaming %s to %s\n", fnametmp, fname);
	ret = robust_rename(fnametmp, fname, file->mode & INITACCESSPERMS);
	if (ret < 0) {
		rsyserr(FERROR, errno, "%s %s -> \"%s\"",
		    ret == -2 ? "copy" : "rename",
		    full_fname(fnametmp), fname);
		do_unlink(fnametmp);
		return;
	}
	if (ret == 0) {
		/* The file was moved into place (not copied), so it's done. */
		return;
	}
    do_set_perms:
	set_perms(fname, file, NULL, ok_to_set_time ? 0 : PERMS_SKIP_MTIME);
}

const char *who_am_i(void)
{
	return am_sender ? "sender" : am_generator ? "generator" : "receiver";
}
