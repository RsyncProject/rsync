/*
 * Routines only used by the sending process.
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
#include "inums.h"

extern int do_xfers;
extern int open_noatime;
extern int am_server;
extern int am_daemon;
extern int local_server;
extern int inc_recurse;
extern int log_before_transfer;
extern int stdout_format_has_i;
extern int logfile_format_has_i;
extern int want_xattr_optim;
extern int xfer_sum_len;
extern int csum_length;
extern int append_mode;
extern int copy_links;
extern int copy_unsafe_links;
extern int copy_dirlinks;
extern int insecure_links;
extern int io_error;
extern int flist_eof;
extern int whole_file;
extern int allowed_lull;
extern int copy_devices;
extern int preserve_xattrs;
extern int protocol_version;
extern int remove_source_files;
extern int updating_basis_file;
extern int make_backups;
extern int inplace;
extern int inplace_partial;
extern int batch_fd;
extern char *module_dir;
extern int module_dirfd;
extern int write_batch;
extern int file_old_total;
extern BOOL want_progress_now;
extern struct stats stats;
extern struct file_list *cur_flist, *first_flist, *dir_flist;
extern char num_dev_ino_buf[4 + 8 + 8];

BOOL extra_flist_sending_enabled;

/**
 * @file
 *
 * The sender gets checksums from the generator, calculates deltas,
 * and transmits them to the receiver.  The sender process runs on the
 * machine holding the source files.
 **/

static int secure_sender_parent_fd(struct file_struct *file, const char *fname, const char **bname_p)
{
#ifdef AT_FDCWD
	const char *path, *slash, *relp, *bslash, *fslash;
	char secure_path[MAXPATHLEN];
	int dfd, fl, slen;

	if (!fname || !*fname) {
		errno = 0;
		return -1;
	}

	/* "insecure links = yes" / --insecure-links: restore the 3.2.7 plain re-stat
	 * by declining the confined parent (errno=0 makes the caller use do_lstat). */
	if (symlink_optout_allowed()) {
		errno = 0;
		return -1;
	}

	if (!am_daemon || !module_dir || module_dir[0] != '/') {
		/* Local (non-daemon) sender: there is no module root to anchor at, but
		 * still confine the parent via the shared held ancestor-dirfd stack
		 * (anchor = cwd, the transfer root set by change_pathname) so the
		 * --remove-source-files re-stat below won't follow an attacker-planted
		 * parent symlink.  Best-effort: an uncacheable (very deep) path declines
		 * to -1 and the caller falls back to the path-based stat. */
		const char *fslash = strrchr(fname, '/');
		*bname_p = fslash ? fslash + 1 : fname;
		if (fslash) {
			char dir[MAXPATHLEN];
			size_t dlen = (size_t)(fslash - fname);
			if (dlen >= sizeof dir) {
				errno = ENAMETOOLONG;
				return -1;
			}
			memcpy(dir, fname, dlen);
			dir[dlen] = '\0';
			/* An absolute --relative name is still rooted at / after
			 * change_pathname().  Resolving its parent through the cwd-backed
			 * dirfd cache would re-anchor cleanup at the sender's working
			 * directory and can remove a same-named, unrelated entry there. */
			if (*fname == '/') {
				const char *rel = dir;
#ifdef __CYGWIN__
				/* clean_fname() keeps exactly two leading slashes here,
				 * because //server/share is a separate UNC namespace.
				 * Stripping them and anchoring at "/" would resolve a
				 * different object entirely, so decline (errno 0) and let
				 * the caller fall back to the path-based cleanup. */
				if (fname[1] == '/' && fname[2] != '/') {
					errno = 0;
					return -1;
				}
#endif
				while (*rel == '/')
					rel++;
				return secure_relative_open("/", rel,
					O_RDONLY | O_DIRECTORY, 0);
			}
			/* held_dir_path_fd returns a cache-OWNED fd; the caller closes
			 * what we return, so hand back an owned dup and leave the cache's
			 * dirfd intact.  An uncacheable (very deep) dir declines with
			 * errno 0 -- fall back to the full confined walk (an owned fd,
			 * matching the sender's content open) so deep paths stay confined
			 * too; a real error propagates. */
			dfd = held_dir_path_fd(NULL, dir);
			if (dfd >= 0)
				return dup(dfd);
			if (errno != 0)
				return -1;
			return secure_relative_open(NULL, dir, O_RDONLY | O_DIRECTORY, 0);
		}
		errno = 0;	/* top-level file: no parent component to confine */
		return -1;
	}

	/* Resolve the file's parent anchored at the absolute module root, never the
	 * process CWD: on cygwin the CWD is path-based, so a removal that trusted
	 * openat(".") could be raced (a parent-symlink flip) into unlinking outside
	 * the module.  Reconstruct the module-relative path from F_PATHNAME + f_name
	 * (as send_files does) and walk it confined beneath module_dir -- the
	 * per-component O_NOFOLLOW walk refuses the flipped symlink. */
	path = F_PATHNAME(file);
	if (!path)
		path = "";
	slash = *path ? "/" : "";
	slen = snprintf(secure_path, sizeof secure_path, "%s%s%s", path, slash, fname);
	if (slen < 0 || slen >= (int)sizeof secure_path) {
		errno = ENAMETOOLONG;
		return -1;
	}
	relp = secure_path;
	while (*relp == '/')
		relp++;

	bslash = strrchr(relp, '/');
	if (bslash) {
		char dir[MAXPATHLEN];
		size_t dlen = (size_t)(bslash - relp);
		if (dlen >= sizeof dir) {
			errno = ENAMETOOLONG;
			return -1;
		}
		memcpy(dir, relp, dlen);
		dir[dlen] = '\0';
		dfd = secure_relative_open(module_dir, dir, O_RDONLY | O_DIRECTORY, 0);
	} else
		dfd = secure_relative_open(module_dir, "", O_RDONLY | O_DIRECTORY, 0);

	/* The leaf is the same last component either way; take it from the caller's
	 * persistent fname buffer, not the local secure_path. */
	fslash = strrchr(fname, '/');
	*bname_p = fslash ? fslash + 1 : fname;

	if (dfd >= 0 && (fl = fcntl(dfd, F_GETFD)) >= 0)
		fcntl(dfd, F_SETFD, fl | FD_CLOEXEC);
	return dfd;
#else
	(void)file; (void)fname; (void)bname_p;
	errno = 0;
	return -1;
#endif
}

/* Go through the do_*() wrapper rather than a raw unlinkat(): it carries the
 * dry_run no-op and the read-only/list-only refusal that do_unlink() applies
 * on the non-fd path, plus the missing-AT_FDCWD fallback. */
static int secure_remove_source_file(int dfd, const char *bname)
{
	return do_unlink_atfd(dfd, bname, 0);
}

/* Open `relpath` (relative to `anchor`: NULL=cwd, else an absolute trusted root)
 * with `flags`, opening the leaf via the shared held ancestor-dirfd stack
 * (held_dir_path_fd) so a directory is walked once, not once per file.  The leaf
 * semantics are identical to secure_relative_open() -- it always O_NOFOLLOWs a
 * file leaf and folds in O_NOATIME, both preserved here.  An uncacheable path
 * (held_dir_path_fd returns -1) falls back to the full confined walk. */
static int sender_open_confined(const char *anchor, const char *relpath, int flags)
{
#ifdef AT_FDCWD
	const char *slash = strrchr(relpath, '/');
	const char *bname;
	char dirbuf[MAXPATHLEN];
	const char *dir;
	int dfd;

	if (slash) {
		size_t dlen = slash - relpath;
		if (dlen >= sizeof dirbuf) {
			errno = ENAMETOOLONG;
			return -1;
		}
		memcpy(dirbuf, relpath, dlen);
		dirbuf[dlen] = '\0';
		dir = dirbuf;
		bname = slash + 1;
	} else {
		dir = "";		/* file directly in the anchor dir */
		bname = relpath;
	}

#ifdef O_NOATIME
	if (open_noatime)
		flags |= O_NOATIME;
#endif
	dfd = held_dir_path_fd(anchor, dir);
	if (dfd < 0)
		return secure_relative_open(anchor, relpath, flags | O_NOFOLLOW, 0);
	return openat(dfd, bname, flags | O_NOFOLLOW, 0);
#else
	/* No *at() support: secure_relative_open is a plain open() here (no walk,
	 * so nothing to amortise); use it directly to keep the anchor semantics. */
	return secure_relative_open(anchor, relpath, flags | O_NOFOLLOW, 0);
#endif
}

/* Open the content of `relpath` for a symlink-following transfer mode (-L /
 * --copy-unsafe-links / -k) while staying confined beneath `anchor`.  The leaf
 * O_NOFOLLOW that sender_open_confined() applies refuses an in-tree symlink the
 * operator explicitly asked to follow, so resolve the link ourselves: read it,
 * refuse an absolute or "../"-escaping target (a module escape), and re-resolve
 * the relative target through secure_relative_open() -- which follows in-tree
 * links and rejects an escape above the anchor -- looping for a symlink chain.
 * The final open is still O_NOFOLLOW, so a raced flip at the resolved leaf is
 * refused.  This keeps the module boundary while honouring --copy-links. */
static int sender_open_copylinks_confined(const char *anchor, const char *relpath)
{
#if defined AT_FDCWD && defined O_NOFOLLOW
	char cur[MAXPATHLEN];
	int hops = 32;
	int extra = 0;
#ifdef O_NOATIME
	if (open_noatime)
		extra |= O_NOATIME;
#endif
	if (strlcpy(cur, relpath, sizeof cur) >= sizeof cur) {
		errno = ENAMETOOLONG;
		return -1;
	}
	while (hops-- > 0) {
		const char *slash = strrchr(cur, '/');
		const char *bname;
		char dir[MAXPATHLEN], tgt[MAXPATHLEN];
		int pdfd, fd, e;
		ssize_t n;
		if (slash) {
			size_t dlen = slash - cur;
			if (dlen >= sizeof dir) { errno = ENAMETOOLONG; return -1; }
			memcpy(dir, cur, dlen);
			dir[dlen] = '\0';
			bname = slash + 1;
		} else {
			dir[0] = '\0';
			bname = cur;
		}
		/* anchor is checked explicitly: the resolver treats a NULL anchor as
		 * "relative to cwd", so it is a legal argument for the else branch --
		 * only this branch would hand it to strcmp(). */
		if (am_daemon && module_dirfd >= 0 && module_dir && anchor
		 && strcmp(anchor, module_dir) == 0)
			pdfd = secure_relative_open_at_beneath(module_dirfd, dir,
					O_RDONLY | O_DIRECTORY, 0);
		else
			pdfd = secure_relative_open(anchor, dir,
					O_RDONLY | O_DIRECTORY, 0);
		if (pdfd < 0)
			return -1;
		n = do_readlink_atfd(pdfd, bname, tgt, sizeof tgt - 1);
		e = errno;
		if (n < 0) {
			/* EINVAL: not a symlink -> the resolved target file.  Open it
			 * O_NOFOLLOW (a raced symlink flip is still refused). */
			fd = e == EINVAL
			   ? openat(pdfd, bname, O_RDONLY | O_NOFOLLOW | O_BINARY | extra, 0)
			   : -1;
			close(pdfd);
			if (n < 0 && e != EINVAL)
				errno = e;
			return fd;
		}
		close(pdfd);
		tgt[n] = '\0';
		if (tgt[0] == '/') {		/* absolute target escapes the module */
			errno = ELOOP;
			return -1;
		}
		if ((size_t)snprintf(cur, sizeof cur, "%s%s%s",
				     dir, *dir ? "/" : "", tgt) >= sizeof cur) {
			errno = ENAMETOOLONG;
			return -1;
		}
	}
	errno = ELOOP;
	return -1;
#else
	return secure_relative_open(anchor, relpath, O_RDONLY | O_NOFOLLOW, 0);
#endif
}

/**
 * Receive the checksums for a buffer
 **/
static struct sum_struct *receive_sums(int f)
{
	struct sum_struct *s = new(struct sum_struct);
	int lull_mod = protocol_version >= 31 ? 0 : allowed_lull * 5;
	OFF_T offset = 0;
	int32 i;

	read_sum_head(f, s);

	s->sums = NULL;

	if (DEBUG_GTE(DELTASUM, 3)) {
		rprintf(FINFO, "count=%s n=%ld rem=%ld\n",
			big_num(s->count), (long)s->blength, (long)s->remainder);
	}

	if (append_mode > 0) {
		s->flength = (OFF_T)s->count * s->blength;
		if (s->remainder)
			s->flength -= s->blength - s->remainder;
		return s;
	}

	if (s->count == 0)
		return(s);

	s->sums = new_array(struct sum_buf, s->count);
	s->sum2_array = new_array(char, (size_t)s->count * xfer_sum_len);

	for (i = 0; i < s->count; i++) {
		s->sums[i].sum1 = read_int(f);
		read_buf(f, sum2_at(s, i), s->s2length);

		s->sums[i].offset = offset;
		s->sums[i].flags = 0;

		if (i == s->count-1 && s->remainder != 0)
			s->sums[i].len = s->remainder;
		else
			s->sums[i].len = s->blength;
		offset += s->sums[i].len;

		if (lull_mod && !(i % lull_mod))
			maybe_send_keepalive(time(NULL), True);

		if (DEBUG_GTE(DELTASUM, 3)) {
			rprintf(FINFO,
				"chunk[%d] len=%d offset=%s sum1=%08x\n",
				i, s->sums[i].len, big_num(s->sums[i].offset),
				s->sums[i].sum1);
		}
	}

	s->flength = offset;

	return s;
}

void successful_send(int ndx)
{
	char fname[MAXPATHLEN];
	char *failed_op;
	const char *bname = NULL;
	struct file_struct *file;
	struct file_list *flist;
	STRUCT_STAT st;
	int dfd = -1, secure_errno = 0;

	if (!remove_source_files)
		return;

	flist = flist_for_ndx(ndx, "successful_send");
	if (ndx < flist->ndx_start)
		exit_cleanup(RERR_PROTOCOL);
	file = flist->files[ndx - flist->ndx_start];
	if (!change_pathname(file, NULL, 0))
		return;
	f_name(file, fname);

	dfd = secure_sender_parent_fd(file, fname, &bname);
	if (dfd < 0)
		secure_errno = errno;

	if (dfd < 0 && secure_errno) {
		errno = secure_errno;
		failed_op = "secure-open-parent";
		goto failed;
	}

	if (dfd >= 0
	 ? (copy_links ? do_stat_atfd(dfd, bname, &st) : do_lstat_atfd(dfd, bname, &st)) < 0
	 : (copy_links ? do_stat(fname, &st) : do_lstat(fname, &st)) < 0) {
		failed_op = "re-lstat";
		goto failed;
	}

	if (local_server
	 && (int64)st.st_dev == IVAL64(num_dev_ino_buf, 4)
	 && (int64)st.st_ino == IVAL64(num_dev_ino_buf, 4 + 8)) {
		rprintf(FERROR_XFER, "ERROR: Skipping sender remove of destination file: %s\n", fname);
		if (dfd >= 0)
			close(dfd);
		return;
	}

	if (st.st_size != F_LENGTH(file) || st.st_mtime != file->modtime
#ifdef ST_MTIME_NSEC
	 || (NSEC_BUMP(file) && (uint32)st.ST_MTIME_NSEC != F_MOD_NSEC(file))
#endif
	) {
		rprintf(FERROR_XFER, "ERROR: Skipping sender remove for changed file: %s\n", fname);
		if (dfd >= 0)
			close(dfd);
		return;
	}

	if (dfd >= 0 ? secure_remove_source_file(dfd, bname) < 0 : do_unlink(fname) < 0) {
		failed_op = "remove";
	  failed:
		if (errno == ENOENT)
			rprintf(FINFO, "sender file already removed: %s\n", fname);
		else
			rsyserr(FERROR_XFER, errno, "sender failed to %s %s", failed_op, fname);
	} else {
		if (INFO_GTE(REMOVE, 1))
			rprintf(FINFO, "sender removed %s\n", fname);
	}
	if (dfd >= 0)
		close(dfd);
}

static void write_ndx_and_attrs(int f_out, int ndx, int iflags,
				const char *fname, struct file_struct *file,
				uchar fnamecmp_type, char *buf, int len)
{
	write_ndx(f_out, ndx);
	if (protocol_version < 29)
		return;
	write_shortint(f_out, iflags);
	if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
		write_byte(f_out, fnamecmp_type);
	if (iflags & ITEM_XNAME_FOLLOWS)
		write_vstring(f_out, buf, len);
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs && iflags & ITEM_REPORT_XATTR && do_xfers
	 && !(want_xattr_optim && BITS_SET(iflags, ITEM_XNAME_FOLLOWS|ITEM_LOCAL_CHANGE)))
		send_xattr_request(fname, file, f_out);
#endif
}

void send_files(int f_in, int f_out)
{
	int fd = -1;
	struct sum_struct *s;
	struct map_struct *mbuf = NULL;
	STRUCT_STAT st;
	char fname[MAXPATHLEN], xname[MAXPATHLEN];
	const char *path, *slash;
	uchar fnamecmp_type;
	int iflags, xlen;
	struct file_struct *file;
	int phase = 0, max_phase = protocol_version >= 29 ? 2 : 1;
	int itemizing = am_server ? logfile_format_has_i : stdout_format_has_i;
	enum logcode log_code = log_before_transfer ? FLOG : FINFO;
	int f_xfer = write_batch < 0 ? batch_fd : f_out;
	int save_io_error = io_error;
	int ndx, j;

	if (DEBUG_GTE(SEND, 1))
		rprintf(FINFO, "send_files starting\n");

	if (whole_file < 0)
		whole_file = 0;

	progress_init();

	while (1) {
		if (inc_recurse) {
			send_extra_file_list(f_out, MIN_FILECNT_LOOKAHEAD);
			extra_flist_sending_enabled = !flist_eof;
		}

		/* This call also sets cur_flist. */
		ndx = read_ndx_and_attrs(f_in, f_out, &iflags, &fnamecmp_type,
					 xname, &xlen);
		extra_flist_sending_enabled = False;

		if (ndx == NDX_DONE) {
			if (!am_server && cur_flist) {
				set_current_file_index(NULL, 0);
				if (INFO_GTE(PROGRESS, 2))
					end_progress(0);
			}
			if (inc_recurse && first_flist) {
				file_old_total -= first_flist->used;
				flist_free(first_flist);
				if (first_flist) {
					if (first_flist == cur_flist)
						file_old_total = cur_flist->used;
					write_ndx(f_out, NDX_DONE);
					continue;
				}
			}
			if (++phase > max_phase)
				break;
			if (DEBUG_GTE(SEND, 1))
				rprintf(FINFO, "send_files phase=%d\n", phase);
			write_ndx(f_out, NDX_DONE);
			continue;
		}

		if (inc_recurse)
			send_extra_file_list(f_out, MIN_FILECNT_LOOKAHEAD);

		if (ndx - cur_flist->ndx_start >= 0)
			file = cur_flist->files[ndx - cur_flist->ndx_start];
		else if (cur_flist->parent_ndx < 0
		      || cur_flist->parent_ndx >= dir_flist->used)
			exit_cleanup(RERR_PROTOCOL);
		else
			file = dir_flist->files[cur_flist->parent_ndx];
		if (!F_IS_ACTIVE(file)) {
			rprintf(FERROR,
				"rsync: refusing transfer of cleared file index %d\n",
				ndx);
			exit_cleanup(RERR_PROTOCOL);
		}
		if (F_PATHNAME(file)) {
			path = F_PATHNAME(file);
			slash = "/";
		} else {
			path = slash = "";
		}
		if (!change_pathname(file, NULL, 0))
			continue;
		f_name(file, fname);

		if (DEBUG_GTE(SEND, 1))
			rprintf(FINFO, "send_files(%d, %s%s%s)\n", ndx, path,slash,fname);

#ifdef SUPPORT_XATTRS
		if (preserve_xattrs && iflags & ITEM_REPORT_XATTR && do_xfers
		 && !(want_xattr_optim && BITS_SET(iflags, ITEM_XNAME_FOLLOWS|ITEM_LOCAL_CHANGE)))
			recv_xattr_request(file, f_in);
#endif

		if (!(iflags & ITEM_TRANSFER)) {
			maybe_log_item(file, iflags, itemizing, xname);
			write_ndx_and_attrs(f_out, ndx, iflags, fname, file, fnamecmp_type, xname, xlen);
			if (iflags & ITEM_IS_NEW) {
				stats.created_files++;
				if (S_ISREG(file->mode)) {
					/* Nothing further to count. */
				} else if (S_ISDIR(file->mode))
					stats.created_dirs++;
#ifdef SUPPORT_LINKS
				else if (S_ISLNK(file->mode))
					stats.created_symlinks++;
#endif
				else if (IS_DEVICE(file->mode))
					stats.created_devices++;
				else
					stats.created_specials++;
			}
			continue;
		}
		if (phase == 2) {
			rprintf(FERROR,
				"got transfer request in phase 2 [%s]\n",
				who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}

		if (file->flags & FLAG_FILE_SENT) {
			if (csum_length == SHORT_SUM_LENGTH) {
				/* For inplace: redo phase turns off the backup
				 * flag so that we do a regular inplace send. */
				make_backups = -make_backups;
				append_mode = -append_mode;
				csum_length = SUM_LENGTH;
			}
		} else {
			if (csum_length != SHORT_SUM_LENGTH) {
				make_backups = -make_backups;
				append_mode = -append_mode;
				csum_length = SHORT_SUM_LENGTH;
			}
			if (iflags & ITEM_IS_NEW)
				stats.created_files++;
		}

		updating_basis_file = (inplace_partial && fnamecmp_type == FNAMECMP_PARTIAL_DIR)
		    || (inplace && (protocol_version >= 29 ? fnamecmp_type == FNAMECMP_FNAME : make_backups <= 0));

		if (!am_server)
			set_current_file_index(file, ndx);
		stats.xferred_files++;
		stats.total_transferred_size += F_LENGTH(file);

		remember_initial_stats();

		if (!do_xfers) { /* log the transfer */
			log_item(FCLIENT, file, iflags, NULL);
			write_ndx_and_attrs(f_out, ndx, iflags, fname, file, fnamecmp_type, xname, xlen);
			continue;
		}

		if (!(s = receive_sums(f_in))) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR_XFER, "receive_sums failed\n");
			exit_cleanup(RERR_PROTOCOL);
		}

		if (symlink_optout_allowed()) {
			/* Module opted out of symlink confinement ("insecure links =
			 * yes", admin-only) -- or a non-daemon --insecure-links: legacy
			 * unconfined open, restoring the pre-hardening content read
			 * (re-opening the escape for that module; documented). */
			fd = do_open_checklinks(fname);
		} else if (secure_relpath_active()) {
			/* Open from module root to prevent TOCTOU race where
			 * change_pathname's chdir follows a directory symlink.
			 * Reconstruct the full path relative to module_dir
			 * from F_PATHNAME (path) and f_name (fname). */
			char secure_path[MAXPATHLEN];
			const char *relp;
			int slen = snprintf(secure_path, sizeof secure_path, "%s%s%s", path, slash, fname);
			if (slen >= (int)sizeof secure_path) {
				io_error |= IOERR_GENERAL;
				rprintf(FERROR_XFER, "path too long: %s%s%s\n", path, slash, fname);
				free_sums(s);
				if (protocol_version >= 30)
					send_msg_int(MSG_NO_SEND, ndx);
				continue;
			}
			/* A module with `path = /` makes F_PATHNAME absolute, so the
			 * joined path starts with '/'; strip leading slashes to a
			 * module-relative path that secure_relative_open accepts (#897). */
			relp = secure_path;
			while (*relp == '/')
				relp++;
			/* A symlink-following mode must follow an in-tree symlink leaf the
			 * operator asked for, still confined to the module; the default
			 * keeps the O_NOFOLLOW leaf so a raced leaf symlink is refused. */
			if (copy_links || copy_unsafe_links || copy_dirlinks || insecure_links)
				fd = sender_open_copylinks_confined(module_dir, relp);
			else
				fd = sender_open_confined(module_dir, relp, O_RDONLY);
		} else if (!copy_links && !copy_unsafe_links && !copy_dirlinks && !insecure_links) {
			/* Default symlink handling (no dir-link following): the scan
			 * recorded this as a regular file.  Open it confined beneath the
			 * transfer root: an in-tree symlinked parent (e.g. -R keeps one in
			 * the path) is followed beneath the root, a parent raced into a
			 * symlink pointing out of the tree is refused, and O_NOFOLLOW
			 * governs the leaf so a raced leaf symlink is refused.  A
			 * symlink-following mode (-L/--copy-unsafe-links/-k) or
			 * --insecure-links keeps the legacy open below. */
			if (fname[0] == '/') {
				/* --relative (or a --files-from absolute name) keeps the
				 * full absolute path as fname; the transfer root is then "/",
				 * so anchor the confined open there and strip the leading
				 * slash to the module-relative path the resolver wants -- it
				 * rejects an absolute relpath outright. */
				const char *relp = fname;
				while (*relp == '/')
					relp++;
				fd = sender_open_confined("/", relp, O_RDONLY);
			} else
				fd = sender_open_confined(NULL, fname, O_RDONLY);
		} else {
			fd = do_open_checklinks(fname);
		}
		if (fd == -1) {
			if (errno == ENOENT) {
				enum logcode c = am_daemon && protocol_version < 28 ? FERROR : FWARNING;
				io_error |= IOERR_VANISHED;
				rprintf(c, "file has vanished: %s\n",
					full_fname(fname));
			} else {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR_XFER, errno,
					"send_files failed to open %s",
					full_fname(fname));
			}
			free_sums(s);
			if (protocol_version >= 30)
				send_msg_int(MSG_NO_SEND, ndx);
			continue;
		}

		/* map the local file */
		if (do_fstat(fd, &st) != 0) {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR_XFER, errno, "fstat failed");
			free_sums(s);
			close(fd);
			exit_cleanup(RERR_FILEIO);
		}

		if (IS_DEVICE(st.st_mode)) {
			if (!copy_devices) {
				rprintf(FERROR, "attempt to copy device contents without --copy-devices\n");
				exit_cleanup(RERR_PROTOCOL);
			}
			if (st.st_size == 0)
				st.st_size = get_device_size(fd, fname);
		}

		if (append_mode > 0 && st.st_size < F_LENGTH(file)) {
			rprintf(FWARNING, "skipped diminished file: %s\n",
				full_fname(fname));
			free_sums(s);
			close(fd);
			if (protocol_version >= 30)
				send_msg_int(MSG_NO_SEND, ndx);
			continue;
		}

		if (st.st_size) {
			int32 read_size = MAX(s->blength * 3, MAX_MAP_SIZE);
			mbuf = map_file(fd, st.st_size, read_size, s->blength);
		} else
			mbuf = NULL;

		if (DEBUG_GTE(DELTASUM, 2)) {
			rprintf(FINFO, "send_files mapped %s%s%s of size %s\n",
				path,slash,fname, big_num(st.st_size));
		}

		write_ndx_and_attrs(f_out, ndx, iflags, fname, file, fnamecmp_type, xname, xlen);
		write_sum_head(f_xfer, s);

		if (DEBUG_GTE(DELTASUM, 2))
			rprintf(FINFO, "calling match_sums %s%s%s\n", path,slash,fname);

		if (log_before_transfer)
			log_item(FCLIENT, file, iflags, NULL);
		else if (!am_server && INFO_GTE(NAME, 1) && INFO_EQ(PROGRESS, 1))
			rprintf(FCLIENT, "%s\n", fname);

		set_compression(fname);

		match_sums(f_xfer, s, mbuf, st.st_size);
		if (INFO_GTE(PROGRESS, 1))
			end_progress(st.st_size);
		else if (want_progress_now)
			instant_progress(fname);

		log_item(log_code, file, iflags, NULL);

		if (mbuf) {
			j = unmap_file(mbuf);
			if (j) {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR_XFER, j,
					"read errors mapping %s",
					full_fname(fname));
			}
		}
		close(fd);

		free_sums(s);

		if (DEBUG_GTE(SEND, 1))
			rprintf(FINFO, "sender finished %s%s%s\n", path,slash,fname);

		/* Flag that we actually sent this entry. */
		file->flags |= FLAG_FILE_SENT;
	}
	if (make_backups < 0)
		make_backups = -make_backups;

	if (io_error != save_io_error && protocol_version >= 30)
		send_msg_int(MSG_IO_ERROR, io_error);

	if (DEBUG_GTE(SEND, 1))
		rprintf(FINFO, "send files finished\n");

	match_report();

	write_ndx(f_out, NDX_DONE);
}
