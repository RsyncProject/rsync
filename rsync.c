/*
 * Routines common to more than one of the rsync processes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003-2007 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
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
#if defined HAVE_LIBCHARSET_H && defined HAVE_LOCALE_CHARSET
#include <libcharset.h>
#elif defined HAVE_LANGINFO_H && defined HAVE_NL_LANGINFO
#include <langinfo.h>
#endif

extern int verbose;
extern int dry_run;
extern int preserve_acls;
extern int preserve_xattrs;
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
extern int inc_recurse;
extern int inplace;
extern int flist_eof;
extern int keep_dirlinks;
extern int make_backups;
extern struct file_list *cur_flist, *first_flist, *dir_flist;
extern struct chmod_mode_struct *daemon_chmod_modes;
#ifdef ICONV_OPTION
extern char *iconv_opt;
#endif

#ifdef ICONV_CONST
iconv_t ic_chck = (iconv_t)-1;
#ifdef ICONV_OPTION
iconv_t ic_send = (iconv_t)-1, ic_recv = (iconv_t)-1;
int ic_ndx;
#endif

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
	const char *defset = default_charset();
# ifdef ICONV_OPTION
	const char *charset;
	char *cp;
#endif

	if (!am_server && !allow_8bit_chars) {

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

# ifdef ICONV_OPTION
	if (!iconv_opt)
		return;

	if ((cp = strchr(iconv_opt, ',')) != NULL) {
		if (am_server) /* A local transfer needs this. */
			iconv_opt = cp + 1;
		else
			*cp = '\0';
	}

	if (!*iconv_opt || (*iconv_opt == '.' && iconv_opt[1] == '\0'))
		charset = defset;
	else
		charset = iconv_opt;

	if ((ic_send = iconv_open(UTF8_CHARSET, charset)) == (iconv_t)-1) {
		rprintf(FERROR, "iconv_open(\"%s\", \"%s\") failed\n",
			UTF8_CHARSET, charset);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	if ((ic_recv = iconv_open(charset, UTF8_CHARSET)) == (iconv_t)-1) {
		rprintf(FERROR, "iconv_open(\"%s\", \"%s\") failed\n",
			charset, UTF8_CHARSET);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	if (!am_sender || inc_recurse)
		ic_ndx = ++file_extra_cnt;

	if (verbose > 1) {
		rprintf(FINFO, "%s charset: %s\n",
			am_server ? "server" : "client",
			*charset ? charset : "[LOCALE]");
	}
# endif
}
#endif

int read_ndx_and_attrs(int f_in, int *iflag_ptr, uchar *type_ptr,
		       char *buf, int *len_ptr)
{
	int len, iflags = 0;
	struct file_list *flist;
	uchar fnamecmp_type = FNAMECMP_FNAME;
	int ndx;

  read_loop:
	while (1) {
		ndx = read_ndx(f_in);

		if (ndx >= 0)
			break;
		if (ndx == NDX_DONE)
			return ndx;
		if (!inc_recurse || am_sender)
			goto invalid_ndx;
		if (ndx == NDX_FLIST_EOF) {
			flist_eof = 1;
			send_msg(MSG_FLIST_EOF, "", 0, 0);
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

		/* Send everything read from f_in to msg_fd_out. */
		send_msg_int(MSG_FLIST, ndx);
		start_flist_forward(f_in);
		flist = recv_file_list(f_in);
		flist->parent_ndx = ndx;
		stop_flist_forward();
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
mode_t dest_mode(mode_t flist_mode, mode_t stat_mode, int dflt_perms,
		 int exists)
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
		/* Apply destination default permissions and turn
		 * off special permissions. */
		new_mode = flist_mode & (~CHMOD_BITS | dflt_perms);
	}
	return new_mode;
}

int set_file_attrs(const char *fname, struct file_struct *file, statx *sxp,
		   const char *fnamecmp, int flags)
{
	int updated = 0;
	statx sx2;
	int change_uid, change_gid;
	mode_t new_mode = file->mode;

	if (!sxp) {
		if (dry_run)
			return 1;
		if (link_stat(fname, &sx2.st, 0) < 0) {
			rsyserr(FERROR, errno, "stat %s failed",
				full_fname(fname));
			return 0;
		}
#ifdef SUPPORT_ACLS
		sx2.acc_acl = sx2.def_acl = NULL;
#endif
#ifdef SUPPORT_XATTRS
		sx2.xattr = NULL;
#endif
		if (!preserve_perms && S_ISDIR(new_mode)
		 && sx2.st.st_mode & S_ISGID) {
			/* We just created this directory and its setgid
			 * bit is on, so make sure it stays on. */
			new_mode |= S_ISGID;
		}
		sxp = &sx2;
	}

#ifdef SUPPORT_ACLS
	if (preserve_acls && !S_ISLNK(file->mode) && !ACL_READY(*sxp))
		get_acl(fname, sxp);
#endif

#ifdef SUPPORT_XATTRS
	if (preserve_xattrs && fnamecmp)
		set_xattr(fname, file, fnamecmp, sxp);
	if (am_root < 0)
		set_stat_xattr(fname, file);
#endif

	if (!preserve_times || (S_ISDIR(sxp->st.st_mode) && omit_dir_times))
		flags |= ATTRS_SKIP_MTIME;
	if (!(flags & ATTRS_SKIP_MTIME)
	    && cmp_time(sxp->st.st_mtime, file->modtime) != 0) {
		int ret = set_modtime(fname, file->modtime, sxp->st.st_mode);
		if (ret < 0) {
			rsyserr(FERROR, errno, "failed to set times on %s",
				full_fname(fname));
			goto cleanup;
		}
		if (ret == 0) /* ret == 1 if symlink could not be set */
			updated = 1;
	}

	change_uid = am_root && preserve_uid && sxp->st.st_uid != F_UID(file);
	change_gid = preserve_gid && F_GID(file) != GID_NONE
		&& sxp->st.st_gid != F_GID(file);
#if !defined HAVE_LCHOWN && !defined CHOWN_MODIFIES_SYMLINK
	if (S_ISLNK(sxp->st.st_mode))
		;
	else
#endif
	if (change_uid || change_gid) {
		if (verbose > 2) {
			if (change_uid) {
				rprintf(FINFO,
					"set uid of %s from %ld to %ld\n",
					fname,
					(long)sxp->st.st_uid, (long)F_UID(file));
			}
			if (change_gid) {
				rprintf(FINFO,
					"set gid of %s from %ld to %ld\n",
					fname,
					(long)sxp->st.st_gid, (long)F_GID(file));
			}
		}
		if (am_root < 0) {
			;
		} else if (do_lchown(fname,
		    change_uid ? F_UID(file) : sxp->st.st_uid,
		    change_gid ? F_GID(file) : sxp->st.st_gid) != 0) {
			/* shouldn't have attempted to change uid or gid
			 * unless have the privilege */
			rsyserr(FERROR, errno, "%s %s failed",
			    change_uid ? "chown" : "chgrp",
			    full_fname(fname));
			goto cleanup;
		} else
		/* a lchown had been done - we have to re-stat if the
		 * destination had the setuid or setgid bits set due
		 * to the side effect of the chown call */
		if (sxp->st.st_mode & (S_ISUID | S_ISGID)) {
			link_stat(fname, &sxp->st,
				  keep_dirlinks && S_ISDIR(sxp->st.st_mode));
		}
		updated = 1;
	}

	if (daemon_chmod_modes && !S_ISLNK(new_mode))
		new_mode = tweak_mode(new_mode, daemon_chmod_modes);

#ifdef SUPPORT_ACLS
	/* It's OK to call set_acl() now, even for a dir, as the generator
	 * will enable owner-writability using chmod, if necessary.
	 * 
	 * If set_acl() changes permission bits in the process of setting
	 * an access ACL, it changes sxp->st.st_mode so we know whether we
	 * need to chmod(). */
	if (preserve_acls && !S_ISLNK(new_mode) && set_acl(fname, file, sxp) == 0)
		updated = 1;
#endif

#ifdef HAVE_CHMOD
	if (!BITS_EQUAL(sxp->st.st_mode, new_mode, CHMOD_BITS)) {
		int ret = am_root < 0 ? 0 : do_chmod(fname, new_mode);
		if (ret < 0) {
			rsyserr(FERROR, errno,
				"failed to set permissions on %s",
				full_fname(fname));
			goto cleanup;
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
  cleanup:
	if (sxp == &sx2) {
#ifdef SUPPORT_ACLS
		if (preserve_acls)
			free_acl(&sx2);
#endif
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs)
			free_xattr(&sx2);
#endif
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
void finish_transfer(const char *fname, const char *fnametmp,
		     const char *fnamecmp, const char *partialptr,
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
	set_file_attrs(fnametmp, file, NULL, fnamecmp,
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
	set_file_attrs(fnametmp, file, NULL, fnamecmp,
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

	if (!flist && !(flist = first_flist))
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
