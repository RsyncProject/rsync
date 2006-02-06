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
#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
#include <iconv.h>
#endif
#if defined HAVE_LIBCHARSET_H && defined HAVE_LOCALE_CHARSET
#include <libcharset.h>
#elif defined HAVE_LANGINFO_H && defined HAVE_NL_LANGINFO
#include <langinfo.h>
#endif

extern int verbose;
extern int dry_run;
extern int daemon_log_format_has_i;
extern int preserve_perms;
extern int preserve_executability;
extern int preserve_times;
extern int omit_dir_times;
extern int orig_umask;
extern int am_root;
extern int am_server;
extern int am_sender;
extern int am_generator;
extern int am_starting_up;
extern int preserve_uid;
extern int preserve_gid;
extern int inplace;
extern int keep_dirlinks;
extern int make_backups;
extern struct stats stats;

#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
iconv_t ic_chck = (iconv_t)-1;

static const char *default_charset(void)
{
#if defined HAVE_LIBCHARSET_H && defined HAVE_LOCALE_CHARSET
	return locale_charset();
#elif defined HAVE_LANGINFO_H && defined HAVE_NL_LANGINFO
	const char *def = nl_langinfo(CODESET);
	if (strcmp(def, "646") == 0) /* Solaris brain-damage */
		return "ascii";
	return def;
#else
	return ""; /* Works with (at the very least) gnu iconv... */
#endif
}

void setup_iconv()
{
	const char *defset = default_charset();

	if (!am_server
	 && (ic_chck = iconv_open(defset, defset)) == (iconv_t)-1) {
		rprintf(FERROR, "iconv_open(\"%s\", \"%s\") failed\n",
			defset, defset);
		exit_cleanup(RERR_UNSUPPORTED);
	}
}
#endif

/*
  free a sums struct
  */
void free_sums(struct sum_struct *s)
{
	if (s->sums) free(s->sums);
	free(s);
}

/* This is only called when we aren't preserving permissions.  Figure out what
 * the permissions should be and return them merged back into the mode. */
mode_t dest_mode(mode_t flist_mode, mode_t dest_mode, int exists)
{
	/* If the file already exists, we'll return the local permissions,
	 * possibly tweaked by the --executability option. */
	if (exists) {
		if (preserve_executability && S_ISREG(flist_mode)) {
			/* If the source file is executable, grant execute
			 * rights to everyone who can read, but ONLY if the
			 * file isn't already executable. */
			if (!(flist_mode & 0111))
				dest_mode &= ~0111;
			else if (!(dest_mode & 0111))
				dest_mode |= (dest_mode & 0444) >> 2;
		}
	} else
		dest_mode = flist_mode & ACCESSPERMS & ~orig_umask;
	return (flist_mode & ~CHMOD_BITS) | (dest_mode & CHMOD_BITS);
}

int set_file_attrs(char *fname, struct file_struct *file, STRUCT_STAT *st,
		   int flags)
{
	int updated = 0;
	STRUCT_STAT st2;
	int change_uid, change_gid;

	if (!st) {
		if (dry_run)
			return 1;
		if (link_stat(fname, &st2, 0) < 0) {
			rsyserr(FERROR, errno, "stat %s failed",
				full_fname(fname));
			return 0;
		}
		st = &st2;
		if (!preserve_perms && S_ISDIR(file->mode)
		 && st->st_mode & S_ISGID) {
			/* We just created this directory and its setgid
			 * bit is on, so make sure it stays on. */
			file->mode |= S_ISGID;
		}
	}

	if (!preserve_times || (S_ISDIR(st->st_mode) && omit_dir_times))
		flags |= ATTRS_SKIP_MTIME;
	if (!(flags & ATTRS_SKIP_MTIME)
	    && cmp_modtime(st->st_mtime, file->modtime) != 0) {
		int ret = set_modtime(fname, file->modtime, st->st_mode);
		if (ret < 0) {
			rsyserr(FERROR, errno, "failed to set times on %s",
				full_fname(fname));
			return 0;
		}
		if (ret == 0) /* ret == 1 if symlink could not be set */
			updated = 1;
	}

	change_uid = am_root && preserve_uid && st->st_uid != file->uid;
	change_gid = preserve_gid && file->gid != GID_NONE
		&& st->st_gid != file->gid;
#if !defined HAVE_LCHOWN && !defined CHOWN_MODIFIES_SYMLINK
	if (S_ISLNK(st->st_mode))
		;
	else
#endif
	if (change_uid || change_gid) {
		if (verbose > 2) {
			if (change_uid) {
				rprintf(FINFO,
					"set uid of %s from %ld to %ld\n",
					fname,
					(long)st->st_uid, (long)file->uid);
			}
			if (change_gid) {
				rprintf(FINFO,
					"set gid of %s from %ld to %ld\n",
					fname,
					(long)st->st_gid, (long)file->gid);
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
	if ((st->st_mode & CHMOD_BITS) != (file->mode & CHMOD_BITS)) {
		int ret = do_chmod(fname, file->mode);
		if (ret < 0) {
			rsyserr(FERROR, errno,
				"failed to set permissions on %s",
				full_fname(fname));
			return 0;
		}
		if (ret == 0) /* ret == 1 if symlink could not be set */
			updated = 1;
	}
#endif

	if (verbose > 1 && flags & ATTRS_REPORT) {
		enum logcode code = daemon_log_format_has_i || dry_run
				  ? FCLIENT : FINFO;
		if (updated)
			rprintf(code, "%s\n", fname);
		else
			rprintf(code, "%s is uptodate\n", fname);
	}
	return updated;
}

RETSIGTYPE sig_int(UNUSED(int val))
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

/* Finish off a file transfer: renaming the file and setting the file's
 * attributes (e.g. permissions, ownership, etc.).  If partialptr is not
 * NULL and the robust_rename() call is forced to copy the temp file, we
 * stage the file into the partial-dir and then rename it into place. */
void finish_transfer(char *fname, char *fnametmp, char *partialptr,
		     struct file_struct *file, int ok_to_set_time,
		     int overwriting_basis)
{
	int ret;

	if (inplace) {
		if (verbose > 2)
			rprintf(FINFO, "finishing %s\n", fname);
		fnametmp = fname;
		goto do_set_file_attrs;
	}

	if (make_backups && overwriting_basis && !make_backup(fname))
		return;

	/* Change permissions before putting the file into place. */
	set_file_attrs(fnametmp, file, NULL,
		       ok_to_set_time ? 0 : ATTRS_SKIP_MTIME);

	/* move tmp file over real file */
	if (verbose > 2)
		rprintf(FINFO, "renaming %s to %s\n", fnametmp, fname);
	ret = robust_rename(fnametmp, fname, partialptr,
			    file->mode & INITACCESSPERMS);
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
	/* The file was copied, so tweak the perms of the copied file.  If it
	 * was copied to partialptr, move it into its final destination. */
	fnametmp = partialptr ? partialptr : fname;

  do_set_file_attrs:
	set_file_attrs(fnametmp, file, NULL,
		       ok_to_set_time ? 0 : ATTRS_SKIP_MTIME);

	if (partialptr) {
		if (do_rename(fnametmp, fname) < 0) {
			rsyserr(FERROR, errno, "rename %s -> \"%s\"",
				full_fname(fnametmp), fname);
		} else
			handle_partial_dir(partialptr, PDIR_DELETE);
	}
}

const char *who_am_i(void)
{
	if (am_starting_up)
		return am_server ? "server" : "client";
	return am_sender ? "sender" : am_generator ? "generator" : "receiver";
}
