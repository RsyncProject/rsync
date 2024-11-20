/*
 * Routines common to more than one of the rsync processes.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003-2022 Wayne Davison
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
#if defined HAVE_LIBCHARSET_H && defined HAVE_LOCALE_CHARSET
#include <libcharset.h>
#elif defined HAVE_LANGINFO_H && defined HAVE_NL_LANGINFO
#include <langinfo.h>
#endif

extern int dry_run;
extern int preserve_acls;
extern int preserve_xattrs;
extern int preserve_perms;
extern int preserve_executability;
extern int preserve_mtimes;
extern int omit_dir_times;
extern int omit_link_times;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int am_sender;
extern int am_receiver;
extern int am_generator;
extern int am_starting_up;
extern int allow_8bit_chars;
extern int protocol_version;
extern int got_kill_signal;
extern int called_from_signal_handler;
extern int inc_recurse;
extern int inplace;
extern int flist_eof;
extern int file_old_total;
extern int keep_dirlinks;
extern int make_backups;
extern int sanitize_paths;
extern struct file_list *cur_flist, *first_flist, *dir_flist;
extern struct chmod_mode_struct *daemon_chmod_modes;
#ifdef ICONV_OPTION
extern char *iconv_opt;
#endif

#define UPDATED_OWNER (1<<0)
#define UPDATED_GROUP (1<<1)
#define UPDATED_MTIME (1<<2)
#define UPDATED_ATIME (1<<3)
#define UPDATED_ACLS  (1<<4)
#define UPDATED_MODE  (1<<5)
#define UPDATED_CRTIME (1<<6)

#ifdef ICONV_CONST
iconv_t ic_chck = (iconv_t)-1;
# ifdef ICONV_OPTION
iconv_t ic_send = (iconv_t)-1, ic_recv = (iconv_t)-1;
# endif

static const char *default_charset(void)
{
# if defined HAVE_LIBCHARSET_H && defined HAVE_LOCALE_CHARSET
	return locale_charset();
# elif defined HAVE_LANGINFO_H && defined HAVE_NL_LANGINFO
	return nl_langinfo(CODESET);
# else
	return ""; /* Works with (at the very least) gnu iconv... */
# endif
}

void setup_iconv(void)
{
	const char *defset = default_charset();
# ifdef ICONV_OPTION
	const char *charset;
	char *cp;
# endif

	if (!am_server && !allow_8bit_chars) {
		/* It's OK if this fails... */
		ic_chck = iconv_open(defset, defset);

		if (DEBUG_GTE(ICONV, 2)) {
			if (ic_chck == (iconv_t)-1) {
				rprintf(FINFO,
					"msg checking via isprint()"
					" (iconv_open(\"%s\", \"%s\") errno: %d)\n",
					defset, defset, errno);
			} else {
				rprintf(FINFO,
					"msg checking charset: %s\n",
					defset);
			}
		}
	} else
		ic_chck = (iconv_t)-1;

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

	if (DEBUG_GTE(ICONV, 1)) {
		rprintf(FINFO, "[%s] charset: %s\n",
			who_am_i(), *charset ? charset : "[LOCALE]");
	}
# endif
}

/* This function converts the chars in the "in" xbuf into characters in the
 * "out" xbuf.  The ".len" chars of the "in" xbuf is used starting from its
 * ".pos".  The ".size" of the "out" xbuf restricts how many characters can
 * be stored, starting at its ".pos+.len" position.  Note that the last byte
 * of the "out" xbuf is not used, which reserves space for a trailing '\0'
 * (though it is up to the caller to store a trailing '\0', as needed).
 *
 * We return a 0 on success or a -1 on error.  An error also sets errno to
 * E2BIG, EILSEQ, or EINVAL (see below); otherwise errno will be set to 0.
 * The "in" xbuf is altered to update ".pos" and ".len".  The "out" xbuf has
 * data appended, and its ".len" incremented (see below for a ".size" note).
 *
 * If ICB_CIRCULAR_OUT is set in "flags", the chars going into the "out" xbuf
 * can wrap around to the start, and the xbuf may have its ".size" reduced
 * (presumably by 1 byte) if the iconv code doesn't have space to store a
 * multi-byte character at the physical end of the ".buf" (though no reducing
 * happens if ".pos" is <= 1, since there is no room to wrap around).
 *
 * If ICB_EXPAND_OUT is set in "flags", the "out" xbuf will be allocated if
 * empty, and (as long as ICB_CIRCULAR_OUT is not set) expanded if too small.
 * This prevents the return of E2BIG (except for a circular xbuf).
 *
 * If ICB_INCLUDE_BAD is set in "flags", any badly-encoded chars are included
 * verbatim in the "out" xbuf, so EILSEQ will not be returned.
 *
 * If ICB_INCLUDE_INCOMPLETE is set in "flags", any incomplete multi-byte
 * chars are included, which ensures that EINVAL is not returned.
 *
 * If ICB_INIT is set, the iconv() conversion state is initialized prior to
 * processing the characters. */
int iconvbufs(iconv_t ic, xbuf *in, xbuf *out, int flags)
{
	ICONV_CONST char *ibuf;
	size_t icnt, ocnt, opos;
	char *obuf;

	if (!out->size && flags & ICB_EXPAND_OUT) {
		size_t siz = ROUND_UP_1024(in->len * 2);
		alloc_xbuf(out, siz);
	} else if (out->len+1 >= out->size) {
		/* There is no room to even start storing data. */
		if (!(flags & ICB_EXPAND_OUT) || flags & ICB_CIRCULAR_OUT) {
			errno = E2BIG;
			return -1;
		}
		realloc_xbuf(out, out->size + ROUND_UP_1024(in->len * 2));
	}

	if (flags & ICB_INIT)
		iconv(ic, NULL, 0, NULL, 0);

	ibuf = in->buf + in->pos;
	icnt = in->len;

	opos = out->pos + out->len;
	if (flags & ICB_CIRCULAR_OUT) {
		if (opos >= out->size) {
			opos -= out->size;
			/* We know that out->pos is not 0 due to the "no room" check
			 * above, so this can't go "negative". */
			ocnt = out->pos - opos - 1;
		} else {
			/* Allow the use of all bytes to the physical end of the buffer
			 * unless pos is 0, in which case we reserve our trailing '\0'. */
			ocnt = out->size - opos - (out->pos ? 0 : 1);
		}
	} else
		ocnt = out->size - opos - 1;
	obuf = out->buf + opos;

	while (icnt) {
		while (iconv(ic, &ibuf, &icnt, &obuf, &ocnt) == (size_t)-1) {
			if (errno == EINTR)
				continue;
			if (errno == EINVAL) {
				if (!(flags & ICB_INCLUDE_INCOMPLETE))
					goto finish;
				if (!ocnt)
					goto e2big;
			} else if (errno == EILSEQ) {
				if (!(flags & ICB_INCLUDE_BAD))
					goto finish;
				if (!ocnt)
					goto e2big;
			} else if (errno == E2BIG) {
				size_t siz;
			  e2big:
				opos = obuf - out->buf;
				if (flags & ICB_CIRCULAR_OUT && out->pos > 1 && opos > out->pos) {
					/* We are in a divided circular buffer at the physical
					 * end with room to wrap to the start.  If iconv() refused
					 * to use one or more trailing bytes in the buffer, we
					 * set the size to ignore the unused bytes. */
					if (opos < out->size)
						reduce_iobuf_size(out, opos);
					obuf = out->buf;
					ocnt = out->pos - 1;
					continue;
				}
				if (!(flags & ICB_EXPAND_OUT) || flags & ICB_CIRCULAR_OUT) {
					errno = E2BIG;
					goto finish;
				}
				siz = ROUND_UP_1024(in->len * 2);
				realloc_xbuf(out, out->size + siz);
				obuf = out->buf + opos;
				ocnt += siz;
				continue;
			} else {
				rsyserr(FERROR, errno, "unexpected error from iconv()");
				exit_cleanup(RERR_UNSUPPORTED);
			}
			*obuf++ = *ibuf++;
			ocnt--, icnt--;
			if (!icnt)
				break;
		}
	}

	errno = 0;

  finish:
	opos = obuf - out->buf;
	if (flags & ICB_CIRCULAR_OUT && opos < out->pos)
		opos += out->size;
	out->len = opos - out->pos;

	in->len = icnt;
	in->pos = ibuf - in->buf;

	return errno ? -1 : 0;
}
#endif

void send_protected_args(int fd, char *args[])
{
	int i;
#ifdef ICONV_OPTION
	int convert = ic_send != (iconv_t)-1;
	xbuf outbuf, inbuf;

	if (convert)
		alloc_xbuf(&outbuf, 1024);
#endif

	for (i = 0; args[i]; i++) {} /* find first NULL */
	args[i] = "rsync"; /* set a new arg0 */
	if (DEBUG_GTE(CMD, 1))
		print_child_argv("protected args:", args + i + 1);
	do {
		if (!args[i][0])
			write_buf(fd, ".", 2);
#ifdef ICONV_OPTION
		else if (convert) {
			INIT_XBUF_STRLEN(inbuf, args[i]);
			iconvbufs(ic_send, &inbuf, &outbuf,
				  ICB_EXPAND_OUT | ICB_INCLUDE_BAD | ICB_INCLUDE_INCOMPLETE | ICB_INIT);
			outbuf.buf[outbuf.len] = '\0';
			write_buf(fd, outbuf.buf, outbuf.len + 1);
			outbuf.len = 0;
		}
#endif
		else
			write_buf(fd, args[i], strlen(args[i]) + 1);
	} while (args[++i]);
	write_byte(fd, 0);

#ifdef ICONV_OPTION
	if (convert)
		free(outbuf.buf);
#endif
}

int read_ndx_and_attrs(int f_in, int f_out, int *iflag_ptr, uchar *type_ptr, char *buf, int *len_ptr)
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
		if (ndx == NDX_DEL_STATS) {
			read_del_stats(f_in);
			if (am_sender && am_server)
				write_del_stats(f_out);
			continue;
		}
		if (!inc_recurse || am_sender) {
			int last;
			if (first_flist)
				last = first_flist->prev->ndx_start + first_flist->prev->used - 1;
			else
				last = -1;
			rprintf(FERROR,
				"Invalid file index: %d (%d - %d) [%s]\n",
				ndx, NDX_DONE, last, who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}
		if (ndx == NDX_FLIST_EOF) {
			flist_eof = 1;
			if (DEBUG_GTE(FLIST, 3))
				rprintf(FINFO, "[%s] flist_eof=1\n", who_am_i());
			write_int(f_out, NDX_FLIST_EOF);
			continue;
		}
		ndx = NDX_FLIST_OFFSET - ndx;
		if (ndx < 0 || ndx >= dir_flist->used) {
			ndx = NDX_FLIST_OFFSET - ndx;
			rprintf(FERROR,
				"Invalid dir index: %d (%d - %d) [%s]\n",
				ndx, NDX_FLIST_OFFSET,
				NDX_FLIST_OFFSET - dir_flist->used + 1,
				who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}

		if (DEBUG_GTE(FLIST, 2)) {
			rprintf(FINFO, "[%s] receiving flist for dir %d\n",
				who_am_i(), ndx);
		}
		/* Send all the data we read for this flist to the generator. */
		start_flist_forward(ndx);
		flist = recv_file_list(f_in, ndx);
		flist->parent_ndx = ndx;
		stop_flist_forward();
	}

	iflags = protocol_version >= 29 ? read_shortint(f_in)
		   : ITEM_TRANSFER | ITEM_MISSING_DATA;

	/* Support the protocol-29 keep-alive style. */
	if (protocol_version < 30 && ndx == cur_flist->used && iflags == ITEM_IS_NEW) {
		if (am_sender)
			maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH);
		goto read_loop;
	}

	flist = flist_for_ndx(ndx, "read_ndx_and_attrs");
	if (flist != cur_flist) {
		cur_flist = flist;
		if (am_sender) {
			file_old_total = cur_flist->used;
			for (flist = first_flist; flist != cur_flist; flist = flist->next)
				file_old_total += flist->used;
		}
	}

	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		fnamecmp_type = read_byte(f_in);
	*type_ptr = fnamecmp_type;

	if (iflags & ITEM_XNAME_FOLLOWS) {
		if ((len = read_vstring(f_in, buf, MAXPATHLEN)) < 0)
			exit_cleanup(RERR_PROTOCOL);

		if (sanitize_paths) {
			sanitize_path(buf, buf, "", 0, SP_DEFAULT);
			len = strlen(buf);
		}
	} else {
		*buf = '\0';
		len = -1;
	}
	*len_ptr = len;

	if (iflags & ITEM_TRANSFER) {
		int i = ndx - cur_flist->ndx_start;
		if (i < 0 || !S_ISREG(cur_flist->files[i]->mode)) {
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
	if (s->sums) {
		free(s->sums);
		free(s->sum2_array);
	}
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

static int same_mtime(struct file_struct *file, STRUCT_STAT *st, int extra_accuracy)
{
#ifdef ST_MTIME_NSEC
	uint32 f1_nsec = F_MOD_NSEC_or_0(file);
	uint32 f2_nsec = (uint32)st->ST_MTIME_NSEC;
#else
	uint32 f1_nsec = 0, f2_nsec = 0;
#endif

	if (extra_accuracy) /* ignore modify_window when setting the time after a transfer or checksum check */
		return file->modtime == st->st_mtime && f1_nsec == f2_nsec;

	return same_time(file->modtime, f1_nsec, st->st_mtime , f2_nsec);
}

int set_file_attrs(const char *fname, struct file_struct *file, stat_x *sxp,
		   const char *fnamecmp, int flags)
{
	int updated = 0;
	stat_x sx2;
	int change_uid, change_gid;
	mode_t new_mode = file->mode;
	int inherit;

	if (!sxp) {
		if (dry_run)
			return 1;
		if (link_stat(fname, &sx2.st, 0) < 0) {
			rsyserr(FERROR_XFER, errno, "stat %s failed",
				full_fname(fname));
			return 0;
		}
		init_stat_x(&sx2);
		sxp = &sx2;
		inherit = !preserve_perms;
	} else
		inherit = !preserve_perms && file->flags & FLAG_DIR_CREATED;

	if (inherit && S_ISDIR(new_mode) && sxp->st.st_mode & S_ISGID) {
		/* We just created this directory and its setgid
		 * bit is on, so make sure it stays on. */
		new_mode |= S_ISGID;
	}

	if (daemon_chmod_modes && !S_ISLNK(new_mode))
		new_mode = tweak_mode(new_mode, daemon_chmod_modes);

#ifdef SUPPORT_ACLS
	if (preserve_acls && !S_ISLNK(file->mode) && !ACL_READY(*sxp))
		get_acl(fname, sxp);
#endif

	change_uid = am_root && uid_ndx && sxp->st.st_uid != (uid_t)F_OWNER(file);
	change_gid = gid_ndx && !(file->flags & FLAG_SKIP_GROUP)
		  && sxp->st.st_gid != (gid_t)F_GROUP(file);
#ifndef CAN_CHOWN_SYMLINK
	if (S_ISLNK(sxp->st.st_mode)) {
		;
	} else
#endif
	if (change_uid || change_gid) {
		if (DEBUG_GTE(OWN, 1)) {
			if (change_uid) {
				rprintf(FINFO,
					"set uid of %s from %u to %u\n",
					fname, (unsigned)sxp->st.st_uid, F_OWNER(file));
			}
			if (change_gid) {
				rprintf(FINFO,
					"set gid of %s from %u to %u\n",
					fname, (unsigned)sxp->st.st_gid, F_GROUP(file));
			}
		}
		if (am_root >= 0) {
			uid_t uid = change_uid ? (uid_t)F_OWNER(file) : sxp->st.st_uid;
			gid_t gid = change_gid ? (gid_t)F_GROUP(file) : sxp->st.st_gid;
			if (do_lchown(fname, uid, gid) != 0) {
				/* We shouldn't have attempted to change uid
				 * or gid unless have the privilege. */
				rsyserr(FERROR_XFER, errno, "%s %s failed",
					change_uid ? "chown" : "chgrp",
					full_fname(fname));
				goto cleanup;
			}
			if (uid == (uid_t)-1 && sxp->st.st_uid != (uid_t)-1)
				rprintf(FERROR_XFER, "uid 4294967295 (-1) is impossible to set on %s\n", full_fname(fname));
			if (gid == (gid_t)-1 && sxp->st.st_gid != (gid_t)-1)
				rprintf(FERROR_XFER, "gid 4294967295 (-1) is impossible to set on %s\n", full_fname(fname));
			/* A lchown had been done, so we need to re-stat if
			 * the destination had the setuid or setgid bits set
			 * (due to the side effect of the chown call). */
			if (sxp->st.st_mode & (S_ISUID | S_ISGID)) {
				link_stat(fname, &sxp->st,
					  keep_dirlinks && S_ISDIR(sxp->st.st_mode));
			}
		}
		if (change_uid)
			updated |= UPDATED_OWNER;
		if (change_gid)
			updated |= UPDATED_GROUP;
	}

#ifdef SUPPORT_XATTRS
	if (am_root < 0)
		set_stat_xattr(fname, file, new_mode);
	if (preserve_xattrs && fnamecmp)
		set_xattr(fname, file, fnamecmp, sxp);
#endif

	if ((omit_dir_times && S_ISDIR(sxp->st.st_mode))
	 || (omit_link_times && S_ISLNK(sxp->st.st_mode)))
		flags |= ATTRS_SKIP_MTIME | ATTRS_SKIP_ATIME | ATTRS_SKIP_CRTIME;
	else {
		if (!preserve_mtimes)
			flags |= ATTRS_SKIP_MTIME;
		if (!atimes_ndx || S_ISDIR(sxp->st.st_mode))
			flags |= ATTRS_SKIP_ATIME;
		/* Don't set the creation date on the root folder of an HFS+ volume. */
		if (sxp->st.st_ino == 2 && S_ISDIR(sxp->st.st_mode))
			flags |= ATTRS_SKIP_CRTIME;
	}
	if (sxp != &sx2)
		memcpy(&sx2.st, &sxp->st, sizeof sx2.st);
	if (!(flags & ATTRS_SKIP_MTIME) && !same_mtime(file, &sxp->st, flags & ATTRS_ACCURATE_TIME)) {
		sx2.st.st_mtime = file->modtime;
#ifdef ST_MTIME_NSEC
		sx2.st.ST_MTIME_NSEC = F_MOD_NSEC_or_0(file);
#endif
		updated |= UPDATED_MTIME;
	}
	if (!(flags & ATTRS_SKIP_ATIME)) {
		time_t file_atime = F_ATIME(file);
		if (flags & ATTRS_ACCURATE_TIME || !same_time(sxp->st.st_atime, 0, file_atime, 0)) {
			sx2.st.st_atime = file_atime;
#ifdef ST_ATIME_NSEC
			sx2.st.ST_ATIME_NSEC = 0;
#endif
			updated |= UPDATED_ATIME;
		}
	}
#ifdef SUPPORT_CRTIMES
	if (crtimes_ndx && !(flags & ATTRS_SKIP_CRTIME)) {
		time_t file_crtime = F_CRTIME(file);
		if (sxp->crtime == 0)
			sxp->crtime = get_create_time(fname, &sxp->st);
		if (!same_time(sxp->crtime, 0L, file_crtime, 0L)) {
			if (
#ifdef HAVE_GETATTRLIST
			     do_setattrlist_crtime(fname, file_crtime) == 0
#elif defined __CYGWIN__
			     do_SetFileTime(fname, file_crtime) == 0
#else
#error Unknown crtimes implementation
#endif
			)
				updated |= UPDATED_CRTIME;
		}
	}
#endif
	if (updated & (UPDATED_MTIME|UPDATED_ATIME)) {
		int ret = set_times(fname, &sx2.st);
		if (ret < 0) {
			rsyserr(FERROR_XFER, errno, "failed to set times on %s", full_fname(fname));
			goto cleanup;
		}
		if (ret > 0) { /* ret == 1 if symlink could not be set */
			updated &= ~(UPDATED_MTIME|UPDATED_ATIME);
			file->flags |= FLAG_TIME_FAILED;
		}
	}

#ifdef SUPPORT_ACLS
	/* It's OK to call set_acl() now, even for a dir, as the generator
	 * will enable owner-writability using chmod, if necessary.
	 *
	 * If set_acl() changes permission bits in the process of setting
	 * an access ACL, it changes sxp->st.st_mode so we know whether we
	 * need to chmod(). */
	if (preserve_acls && !S_ISLNK(new_mode)) {
		if (set_acl(fname, file, sxp, new_mode) > 0)
			updated |= UPDATED_ACLS;
	}
#endif

#ifdef HAVE_CHMOD
	if (!BITS_EQUAL(sxp->st.st_mode, new_mode, CHMOD_BITS)) {
		int ret = am_root < 0 ? 0 : do_chmod(fname, new_mode);
		if (ret < 0) {
			rsyserr(FERROR_XFER, errno,
				"failed to set permissions on %s",
				full_fname(fname));
			goto cleanup;
		}
		if (ret == 0) /* ret == 1 if symlink could not be set */
			updated |= UPDATED_MODE;
	}
#endif

	if (INFO_GTE(NAME, 2) && flags & ATTRS_REPORT) {
		if (updated)
			rprintf(FCLIENT, "%s\n", fname);
		else
			rprintf(FCLIENT, "%s is uptodate\n", fname);
	}
  cleanup:
	if (sxp == &sx2)
		free_stat_x(&sx2);
	return updated;
}

/* This is only called for SIGINT, SIGHUP, and SIGTERM. */
void sig_int(int sig_num)
{
	called_from_signal_handler = 1;

	/* KLUGE: if the user hits Ctrl-C while ssh is prompting
	 * for a password, then our cleanup's sending of a SIGUSR1
	 * signal to all our children may kill ssh before it has a
	 * chance to restore the tty settings (i.e. turn echo back
	 * on).  By sleeping for a short time, ssh gets a bigger
	 * chance to do the right thing.  If child processes are
	 * not ssh waiting for a password, then this tiny delay
	 * shouldn't hurt anything. */
	msleep(400);

	/* If we're an rsync daemon listener (not a daemon server),
	 * we'll exit with status 0 if we received SIGTERM. */
	if (am_daemon && !am_server && sig_num == SIGTERM)
		exit_cleanup(0);

	/* If the signal arrived on the server side (or for the receiver
	 * process on the client), we want to try to do a controlled shutdown
	 * that lets the client side (generator process) know what happened.
	 * To do this, we set a flag and let the normal process handle the
	 * shutdown.  We only attempt this if multiplexed IO is in effect and
	 * we didn't already set the flag. */
	if (!got_kill_signal && (am_server || am_receiver)) {
		got_kill_signal = sig_num;
		called_from_signal_handler = 0;
		return;
	}

	exit_cleanup(RERR_SIGNAL);
}

/* Finish off a file transfer: renaming the file and setting the file's
 * attributes (e.g. permissions, ownership, etc.).  If the robust_rename()
 * call is forced to copy the temp file and partialptr is both non-NULL and
 * not an absolute path, we stage the file into the partial-dir and then
 * rename it into place.  This returns 1 on success or 0 on failure. */
int finish_transfer(const char *fname, const char *fnametmp,
		    const char *fnamecmp, const char *partialptr,
		    struct file_struct *file, int ok_to_set_time,
		    int overwriting_basis)
{
	int ret;
	const char *temp_copy_name = partialptr && *partialptr != '/' ? partialptr : NULL;

	if (inplace) {
		if (DEBUG_GTE(RECV, 1))
			rprintf(FINFO, "finishing %s\n", fname);
		fnametmp = fname;
		goto do_set_file_attrs;
	}

	if (make_backups > 0 && overwriting_basis) {
		int ok = make_backup(fname, False);
		if (!ok)
			exit_cleanup(RERR_FILEIO);
		if (ok == 1 && fnamecmp == fname)
			fnamecmp = get_backup_name(fname);
	}

	/* Change permissions before putting the file into place. */
	set_file_attrs(fnametmp, file, NULL, fnamecmp,
		       ok_to_set_time ? ATTRS_ACCURATE_TIME : ATTRS_SKIP_MTIME | ATTRS_SKIP_ATIME | ATTRS_SKIP_CRTIME);

	/* move tmp file over real file */
	if (DEBUG_GTE(RECV, 1))
		rprintf(FINFO, "renaming %s to %s\n", fnametmp, fname);
	ret = robust_rename(fnametmp, fname, temp_copy_name, file->mode);
	if (ret < 0) {
		rsyserr(FERROR_XFER, errno, "%s %s -> \"%s\"",
			ret == -2 ? "copy" : "rename",
			full_fname(fnametmp), fname);
		if (!partialptr || (ret == -2 && temp_copy_name)
		 || robust_rename(fnametmp, partialptr, NULL, file->mode) < 0)
			do_unlink(fnametmp);
		return 0;
	}
	if (ret == 0) {
		/* The file was moved into place (not copied), so it's done. */
		return 1;
	}
	/* The file was copied, so tweak the perms of the copied file.  If it
	 * was copied to partialptr, move it into its final destination. */
	fnametmp = temp_copy_name ? temp_copy_name : fname;

  do_set_file_attrs:
	set_file_attrs(fnametmp, file, NULL, fnamecmp,
		       ok_to_set_time ? ATTRS_ACCURATE_TIME : ATTRS_SKIP_MTIME | ATTRS_SKIP_ATIME | ATTRS_SKIP_CRTIME);

	if (temp_copy_name) {
		if (do_rename(fnametmp, fname) < 0) {
			rsyserr(FERROR_XFER, errno, "rename %s -> \"%s\"",
				full_fname(fnametmp), fname);
			return 0;
		}
		handle_partial_dir(temp_copy_name, PDIR_DELETE);
	}
	return 1;
}

struct file_list *flist_for_ndx(int ndx, const char *fatal_error_loc)
{
	struct file_list *flist = cur_flist;

	if (!flist && !(flist = first_flist))
		goto not_found;

	while (ndx < flist->ndx_start-1) {
		if (flist == first_flist)
			goto not_found;
		flist = flist->prev;
	}
	while (ndx >= flist->ndx_start + flist->used) {
		if (!(flist = flist->next))
			goto not_found;
	}
	return flist;

  not_found:
	if (fatal_error_loc) {
		int first, last;
		if (first_flist) {
			first = first_flist->ndx_start - 1;
			last = first_flist->prev->ndx_start + first_flist->prev->used - 1;
		} else {
			first = 0;
			last = -1;
		}
		rprintf(FERROR,
			"File-list index %d not in %d - %d (%s) [%s]\n",
			ndx, first, last, fatal_error_loc, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
	return NULL;
}

const char *who_am_i(void)
{
	if (am_starting_up)
		return am_server ? "server" : "client";
	return am_sender ? "sender"
	     : am_generator ? "generator"
	     : am_receiver ? "receiver"
	     : "Receiver"; /* pre-forked receiver */
}
