/*
 * Routines common to more than one of the rsync processes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
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
extern int preserve_perms;
extern int preserve_executability;
extern int preserve_times;
extern int omit_dir_times;
extern int am_root;
extern int am_server;
extern int am_sender;
extern int am_generator;
extern int am_starting_up;
extern int allow_8bit_chars;
extern int protocol_version;
extern int preserve_uid;
extern int preserve_gid;
extern int incremental;
extern int inplace;
extern int flist_eof;
extern int keep_dirlinks;
extern int make_backups;
extern mode_t orig_umask;
extern struct stats stats;
extern struct file_list *cur_flist, *first_flist, *dir_flist;
extern struct chmod_mode_struct *daemon_chmod_modes;

#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
iconv_t ic_chck = (iconv_t)-1;

static const char *default_charset(void)
{
#if defined HAVE_LIBCHARSET_H && defined HAVE_LOCALE_CHARSET
	return locale_charset();
#elif defined HAVE_LANGINFO_H && defined HAVE_NL_LANGINFO
	return nl_langinfo(CODESET);
#else
	return ""; /* Works with (at the very least) gnu iconv... */
#endif
}

void setup_iconv()
{
	if (!am_server && !allow_8bit_chars) {
		const char *defset = default_charset();

		/* It's OK if this fails... */
		ic_chck = iconv_open(defset, defset);

		if (verbose > 3) {
			if (ic_chck == (iconv_t)-1) {
				rprintf(FINFO,
					"note: iconv_open(\"%s\", \"%s\") failed (%d)"
					" -- using isprint() instead of iconv().\n",
					defset, defset, errno);
			} else {
				rprintf(FINFO,
					"note: iconv_open(\"%s\", \"%s\") succeeded.\n",
					defset, defset);
			}
		}
	}
}
#endif

/* This is used by sender.c with a valid f_out, and by receive.c with
 * f_out = -1. */
int read_ndx_and_attrs(int f_in, int f_out, int *iflag_ptr,
		       uchar *type_ptr, char *buf, int *len_ptr)
{
	int len, iflags = 0;
	struct file_list *flist;
	uchar fnamecmp_type = FNAMECMP_FNAME;
	int verbose_save, ndx;

  read_loop:
	while (1) {
		ndx = read_ndx(f_in);

		if (ndx >= 0)
			break;
		if (ndx == NDX_DONE)
			return ndx;
		if (!incremental || am_sender)
			goto invalid_ndx;
		if (ndx == NDX_FLIST_EOF) {
			flist_eof = 1;
			send_msg(MSG_FLIST_EOF, "", 0);
			continue;
		}
		ndx = NDX_FLIST_OFFSET - ndx;
		if (ndx < 0 || ndx >= dir_flist->count) {
			ndx = NDX_FLIST_OFFSET - ndx;
			rprintf(FERROR,
				"Invalid dir index: %d (%d - %d)\n",
				ndx, NDX_FLIST_OFFSET,
				NDX_FLIST_OFFSET - dir_flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}
		verbose_save = verbose;
		verbose = 0; /* TODO allow verbose messages? */

		/* Send everything read from f_in to msg_fd_out. */
		send_msg_int(MSG_FLIST, ndx);
		start_flist_forward(f_in);
		flist = recv_file_list(f_in);
		flist->parent_ndx = ndx;
		stop_flist_forward();

		verbose = verbose_save;
	}

	iflags = protocol_version >= 29 ? read_shortint(f_in)
		   : ITEM_TRANSFER | ITEM_MISSING_DATA;

	/* Honor the old-style keep-alive indicator. */
	if (protocol_version < 30
	 && ndx == cur_flist->count && iflags == ITEM_IS_NEW) {
		if (am_sender)
			maybe_send_keepalive();
		goto read_loop;
	}

	if (!(flist = flist_for_ndx(ndx))) {
	  invalid_ndx:
		rprintf(FERROR,
			"Invalid file index: %d (%d - %d) with iflags %x [%s]\n",
			ndx, first_flist->ndx_start + first_flist->ndx_start,
			first_flist->prev->ndx_start + first_flist->ndx_start
			+ first_flist->prev->count - 1, iflags, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	cur_flist = flist;

	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		fnamecmp_type = read_byte(f_in);
	*type_ptr = fnamecmp_type;

	if (iflags & ITEM_XNAME_FOLLOWS) {
		if ((len = read_vstring(f_in, buf, MAXPATHLEN)) < 0)
			exit_cleanup(RERR_PROTOCOL);
	} else {
		*buf = '\0';
		len = -1;
	}
	*len_ptr = len;

	if (iflags & ITEM_TRANSFER) {
		int i = ndx - cur_flist->ndx_start;
		if (!S_ISREG(cur_flist->files[i]->mode)) {
			rprintf(FERROR,
				"received request to transfer non-regular file: %d [%s]\n",
				ndx, who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}
	} else if (f_out >= 0) {
		write_ndx_and_attrs(f_out, ndx, iflags,
				    fnamecmp_type, buf, len);
	}

	*iflag_ptr = iflags;
	return ndx;
}

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
mode_t dest_mode(mode_t flist_mode, mode_t stat_mode, int exists)
{
	int new_mode;
	/* If the file already exists, we'll return the local permissions,
	 * possibly tweaked by the --executability option. */
	if (exists) {
		new_mode = (flist_mode & ~CHMOD_BITS) | (stat_mode & CHMOD_BITS);
		if (preserve_executability && S_ISREG(flist_mode)) {
			/* If the source file is executable, grant execute
			 * rights to everyone who can read, but ONLY if the
			 * file isn't already executable. */
			if (!(flist_mode & 0111))
				new_mode &= ~0111;
			else if (!(stat_mode & 0111))
				new_mode |= (new_mode & 0444) >> 2;
		}
	} else {
		/* Apply the umask and turn off special permissions. */
		new_mode = flist_mode & (~CHMOD_BITS | (ACCESSPERMS & ~orig_umask));
	}
	return new_mode;
}

int set_file_attrs(char *fname, struct file_struct *file, STRUCT_STAT *st,
		   int flags)
{
	int updated = 0;
	STRUCT_STAT st2;
	int change_uid, change_gid;
	mode_t new_mode = file->mode;

	if (!st) {
		if (dry_run)
			return 1;
		if (link_stat(fname, &st2, 0) < 0) {
			rsyserr(FERROR, errno, "stat %s failed",
				full_fname(fname));
			return 0;
		}
		st = &st2;
		if (!preserve_perms && S_ISDIR(new_mode)
		 && st->st_mode & S_ISGID) {
			/* We just created this directory and its setgid
			 * bit is on, so make sure it stays on. */
			new_mode |= S_ISGID;
		}
	}

	if (!preserve_times || (S_ISDIR(st->st_mode) && omit_dir_times))
		flags |= ATTRS_SKIP_MTIME;
	if (!(flags & ATTRS_SKIP_MTIME)
	    && cmp_time(st->st_mtime, file->modtime) != 0) {
		int ret = set_modtime(fname, file->modtime, st->st_mode);
		if (ret < 0) {
			rsyserr(FERROR, errno, "failed to set times on %s",
				full_fname(fname));
			return 0;
		}
		if (ret == 0) /* ret == 1 if symlink could not be set */
			updated = 1;
	}

	change_uid = am_root && preserve_uid && st->st_uid != F_UID(file);
	change_gid = preserve_gid && F_GID(file) != GID_NONE
		&& st->st_gid != F_GID(file);
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
					(long)st->st_uid, (long)F_UID(file));
			}
			if (change_gid) {
				rprintf(FINFO,
					"set gid of %s from %ld to %ld\n",
					fname,
					(long)st->st_gid, (long)F_GID(file));
			}
		}
		if (do_lchown(fname,
		    change_uid ? F_UID(file) : st->st_uid,
		    change_gid ? F_GID(file) : st->st_gid) != 0) {
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

	if (daemon_chmod_modes && !S_ISLNK(new_mode))
		new_mode = tweak_mode(new_mode, daemon_chmod_modes);
#ifdef HAVE_CHMOD
	if (!BITS_EQUAL(st->st_mode, new_mode, CHMOD_BITS)) {
		int ret = do_chmod(fname, new_mode);
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
		if (updated)
			rprintf(FCLIENT, "%s\n", fname);
		else
			rprintf(FCLIENT, "%s is uptodate\n", fname);
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

	if (make_backups > 0 && overwriting_basis && !make_backup(fname))
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

struct file_list *flist_for_ndx(int ndx)
{
	struct file_list *flist = cur_flist;

	if (!flist)
		return NULL;

	while (ndx < flist->ndx_start) {
		if (flist == first_flist)
			return NULL;
		flist = flist->prev;
	}
	while (ndx >= flist->ndx_start + flist->count) {
		if (!(flist = flist->next))
			return NULL;
	}
	return flist;
}

const char *who_am_i(void)
{
	if (am_starting_up)
		return am_server ? "server" : "client";
	return am_sender ? "sender" : am_generator ? "generator" : "receiver";
}
