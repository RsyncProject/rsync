/*
 * Routines that are exclusive to the generator process.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2015 Wayne Davison
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
#include "ifuncs.h"

extern int dry_run;
extern int do_xfers;
extern int stdout_format_has_i;
extern int logfile_format_has_i;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int inc_recurse;
extern int relative_paths;
extern int implied_dirs;
extern int keep_dirlinks;
extern int preserve_acls;
extern int preserve_xattrs;
extern int preserve_links;
extern int preserve_devices;
extern int preserve_specials;
extern int preserve_hard_links;
extern int preserve_executability;
extern int preserve_perms;
extern int preserve_times;
extern int delete_mode;
extern int delete_before;
extern int delete_during;
extern int delete_after;
extern int missing_args;
extern int msgdone_cnt;
extern int ignore_errors;
extern int remove_source_files;
extern int delay_updates;
extern int update_only;
extern int human_readable;
extern int ignore_existing;
extern int ignore_non_existing;
extern int want_xattr_optim;
extern int inplace;
extern int append_mode;
extern int make_backups;
extern int csum_length;
extern int ignore_times;
extern int size_only;
extern OFF_T max_size;
extern OFF_T min_size;
extern int io_error;
extern int flist_eof;
extern int allowed_lull;
extern int sock_f_out;
extern int protocol_version;
extern int file_total;
extern int fuzzy_basis;
extern int always_checksum;
extern int checksum_len;
extern char *partial_dir;
extern int compare_dest;
extern int copy_dest;
extern int link_dest;
extern int whole_file;
extern int list_only;
extern int read_batch;
extern int write_batch;
extern int safe_symlinks;
extern long block_size; /* "long" because popt can't set an int32. */
extern int unsort_ndx;
extern int max_delete;
extern int force_delete;
extern int one_file_system;
extern int skipped_deletes;
extern dev_t filesystem_dev;
extern mode_t orig_umask;
extern uid_t our_uid;
extern char *tmpdir;
extern char *basis_dir[MAX_BASIS_DIRS+1];
extern struct file_list *cur_flist, *first_flist, *dir_flist;
extern filter_rule_list filter_list, daemon_filter_list;

int maybe_ATTRS_REPORT = 0;

static dev_t dev_zero;
static int deldelay_size = 0, deldelay_cnt = 0;
static char *deldelay_buf = NULL;
static int deldelay_fd = -1;
static int loopchk_limit;
static int dir_tweaking;
static int symlink_timeset_failed_flags;
static int need_retouch_dir_times;
static int need_retouch_dir_perms;
static const char *solo_file = NULL;

enum nonregtype {
    TYPE_DIR, TYPE_SPECIAL, TYPE_DEVICE, TYPE_SYMLINK
};

/* Forward declarations. */
#ifdef SUPPORT_HARD_LINKS
static void handle_skipped_hlink(struct file_struct *file, int itemizing,
				 enum logcode code, int f_out);
#endif

#define EARLY_DELAY_DONE_MSG() (!delay_updates)
#define EARLY_DELETE_DONE_MSG() (!(delete_during == 2 || delete_after))

static int start_delete_delay_temp(void)
{
	char fnametmp[MAXPATHLEN];
	int save_dry_run = dry_run;

	dry_run = 0;
	if (!get_tmpname(fnametmp, "deldelay", False)
	 || (deldelay_fd = do_mkstemp(fnametmp, 0600)) < 0) {
		rprintf(FINFO, "NOTE: Unable to create delete-delay temp file%s.\n",
			inc_recurse ? "" : " -- switching to --delete-after");
		delete_during = 0;
		delete_after = !inc_recurse;
		dry_run = save_dry_run;
		return 0;
	}
	unlink(fnametmp);
	dry_run = save_dry_run;
	return 1;
}

static int flush_delete_delay(void)
{
	if (deldelay_fd < 0 && !start_delete_delay_temp())
		return 0;
	if (write(deldelay_fd, deldelay_buf, deldelay_cnt) != deldelay_cnt) {
		rsyserr(FERROR, errno, "flush of delete-delay buffer");
		delete_during = 0;
		delete_after = !inc_recurse;
		close(deldelay_fd);
		return 0;
	}
	deldelay_cnt = 0;
	return 1;
}

static int remember_delete(struct file_struct *file, const char *fname, int flags)
{
	int len;

	if (deldelay_cnt == deldelay_size && !flush_delete_delay())
		return 0;

	if (flags & DEL_NO_UID_WRITE)
		deldelay_buf[deldelay_cnt++] = '!';

	while (1) {
		len = snprintf(deldelay_buf + deldelay_cnt,
			       deldelay_size - deldelay_cnt,
			       "%x %s%c",
			       (int)file->mode, fname, '\0');
		if ((deldelay_cnt += len) <= deldelay_size)
			break;
		deldelay_cnt -= len;
		if (!flush_delete_delay())
			return 0;
	}

	return 1;
}

static int read_delay_line(char *buf, int *flags_p)
{
	static int read_pos = 0;
	int j, len, mode;
	char *bp, *past_space;

	while (1) {
		for (j = read_pos; j < deldelay_cnt && deldelay_buf[j]; j++) {}
		if (j < deldelay_cnt)
			break;
		if (deldelay_fd < 0) {
			if (j > read_pos)
				goto invalid_data;
			return -1;
		}
		deldelay_cnt -= read_pos;
		if (deldelay_cnt == deldelay_size)
			goto invalid_data;
		if (deldelay_cnt && read_pos) {
			memmove(deldelay_buf, deldelay_buf + read_pos,
				deldelay_cnt);
		}
		len = read(deldelay_fd, deldelay_buf + deldelay_cnt,
			   deldelay_size - deldelay_cnt);
		if (len == 0) {
			if (deldelay_cnt) {
				rprintf(FERROR,
				    "ERROR: unexpected EOF in delete-delay file.\n");
			}
			return -1;
		}
		if (len < 0) {
			rsyserr(FERROR, errno,
				"reading delete-delay file");
			return -1;
		}
		deldelay_cnt += len;
		read_pos = 0;
	}

	bp = deldelay_buf + read_pos;
	if (*bp == '!') {
		bp++;
		*flags_p = DEL_NO_UID_WRITE;
	} else
		*flags_p = 0;

	if (sscanf(bp, "%x ", &mode) != 1) {
	  invalid_data:
		rprintf(FERROR, "ERROR: invalid data in delete-delay file.\n");
		return -1;
	}
	past_space = strchr(bp, ' ') + 1;
	len = j - read_pos - (past_space - bp) + 1; /* count the '\0' */
	read_pos = j + 1;

	if (len > MAXPATHLEN) {
		rprintf(FERROR, "ERROR: filename too long in delete-delay file.\n");
		return -1;
	}

	/* The caller needs the name in a MAXPATHLEN buffer, so we copy it
	 * instead of returning a pointer to our buffer. */
	memcpy(buf, past_space, len);

	return mode;
}

static void do_delayed_deletions(char *delbuf)
{
	int mode, flags;

	if (deldelay_fd >= 0) {
		if (deldelay_cnt && !flush_delete_delay())
			return;
		lseek(deldelay_fd, 0, 0);
	}
	while ((mode = read_delay_line(delbuf, &flags)) >= 0)
		delete_item(delbuf, mode, flags | DEL_RECURSE);
	if (deldelay_fd >= 0)
		close(deldelay_fd);
}

/* This function is used to implement per-directory deletion, and is used by
 * all the --delete-WHEN options.  Note that the fbuf pointer must point to a
 * MAXPATHLEN buffer with the name of the directory in it (the functions we
 * call will append names onto the end, but the old dir value will be restored
 * on exit). */
static void delete_in_dir(char *fbuf, struct file_struct *file, dev_t *fs_dev)
{
	static int already_warned = 0;
	struct file_list *dirlist;
	char delbuf[MAXPATHLEN];
	int dlen, i;

	if (!fbuf) {
		change_local_filter_dir(NULL, 0, 0);
		return;
	}

	if (DEBUG_GTE(DEL, 2))
		rprintf(FINFO, "delete_in_dir(%s)\n", fbuf);

	if (allowed_lull)
		maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH);

	if (io_error & IOERR_GENERAL && !ignore_errors) {
		if (already_warned)
			return;
		rprintf(FINFO,
			"IO error encountered -- skipping file deletion\n");
		already_warned = 1;
		return;
	}

	dlen = strlen(fbuf);
	change_local_filter_dir(fbuf, dlen, F_DEPTH(file));

	if (one_file_system) {
		if (file->flags & FLAG_TOP_DIR)
			filesystem_dev = *fs_dev;
		else if (filesystem_dev != *fs_dev)
			return;
	}

	dirlist = get_dirlist(fbuf, dlen, 0);

	/* If an item in dirlist is not found in flist, delete it
	 * from the filesystem. */
	for (i = dirlist->used; i--; ) {
		struct file_struct *fp = dirlist->files[i];
		if (!F_IS_ACTIVE(fp))
			continue;
		if (fp->flags & FLAG_MOUNT_DIR && S_ISDIR(fp->mode)) {
			if (INFO_GTE(MOUNT, 1))
				rprintf(FINFO, "cannot delete mount point: %s\n",
					f_name(fp, NULL));
			continue;
		}
		/* Here we want to match regardless of file type.  Replacement
		 * of a file with one of another type is handled separately by
		 * a delete_item call with a DEL_MAKE_ROOM flag. */
		if (flist_find_ignore_dirness(cur_flist, fp) < 0) {
			int flags = DEL_RECURSE;
			if (!(fp->mode & S_IWUSR) && !am_root && fp->flags & FLAG_OWNED_BY_US)
				flags |= DEL_NO_UID_WRITE;
			f_name(fp, delbuf);
			if (delete_during == 2) {
				if (!remember_delete(fp, delbuf, flags))
					break;
			} else
				delete_item(delbuf, fp->mode, flags);
		}
	}

	flist_free(dirlist);
}

/* This deletes any files on the receiving side that are not present on the
 * sending side.  This is used by --delete-before and --delete-after. */
static void do_delete_pass(void)
{
	char fbuf[MAXPATHLEN];
	STRUCT_STAT st;
	int j;

	/* dry_run is incremented when the destination doesn't exist yet. */
	if (dry_run > 1 || list_only)
		return;

	for (j = 0; j < cur_flist->used; j++) {
		struct file_struct *file = cur_flist->sorted[j];

		if (!F_IS_ACTIVE(file))
			continue;

		f_name(file, fbuf);

		if (!(file->flags & FLAG_CONTENT_DIR)) {
			change_local_filter_dir(fbuf, strlen(fbuf), F_DEPTH(file));
			continue;
		}

		if (DEBUG_GTE(DEL, 1) && file->flags & FLAG_TOP_DIR)
			rprintf(FINFO, "deleting in %s\n", fbuf);

		if (link_stat(fbuf, &st, keep_dirlinks) < 0
		 || !S_ISDIR(st.st_mode))
			continue;

		delete_in_dir(fbuf, file, &st.st_dev);
	}
	delete_in_dir(NULL, NULL, &dev_zero);

	if (INFO_GTE(FLIST, 2) && !am_server)
		rprintf(FINFO, "                    \r");
}

static inline int time_differs(struct file_struct *file, stat_x *sxp)
{
	return cmp_time(sxp->st.st_mtime, file->modtime);
}

static inline int perms_differ(struct file_struct *file, stat_x *sxp)
{
	if (preserve_perms)
		return !BITS_EQUAL(sxp->st.st_mode, file->mode, CHMOD_BITS);

	if (preserve_executability)
		return (sxp->st.st_mode & 0111 ? 1 : 0) ^ (file->mode & 0111 ? 1 : 0);

	return 0;
}

static inline int ownership_differs(struct file_struct *file, stat_x *sxp)
{
	if (am_root && uid_ndx && sxp->st.st_uid != (uid_t)F_OWNER(file))
		return 1;

	if (gid_ndx && !(file->flags & FLAG_SKIP_GROUP) && sxp->st.st_gid != (gid_t)F_GROUP(file))
		return 1;

	return 0;
}

#ifdef SUPPORT_ACLS
static inline int acls_differ(const char *fname, struct file_struct *file, stat_x *sxp)
{
	if (preserve_acls) {
		if (!ACL_READY(*sxp))
			get_acl(fname, sxp);
		if (set_acl(NULL, file, sxp, file->mode))
			return 1;
	}

	return 0;
}
#endif

#ifdef SUPPORT_XATTRS
static inline int xattrs_differ(const char *fname, struct file_struct *file, stat_x *sxp)
{
	if (preserve_xattrs) {
		if (!XATTR_READY(*sxp))
			get_xattr(fname, sxp);
		if (xattr_diff(file, sxp, 0))
			return 1;
	}

	return 0;
}
#endif

int unchanged_attrs(const char *fname, struct file_struct *file, stat_x *sxp)
{
	if (S_ISLNK(file->mode)) {
#ifdef CAN_SET_SYMLINK_TIMES
		if (preserve_times & PRESERVE_LINK_TIMES && time_differs(file, sxp))
			return 0;
#endif
#ifdef CAN_CHMOD_SYMLINK
		if (perms_differ(file, sxp))
			return 0;
#endif
#ifdef CAN_CHOWN_SYMLINK
		if (ownership_differs(file, sxp))
			return 0;
#endif
#if defined SUPPORT_ACLS && 0 /* no current symlink-ACL support */
		if (acls_differ(fname, file, sxp))
			return 0;
#endif
#if defined SUPPORT_XATTRS && !defined NO_SYMLINK_XATTRS
		if (xattrs_differ(fname, file, sxp))
			return 0;
#endif
	} else {
		if (preserve_times && time_differs(file, sxp))
			return 0;
		if (perms_differ(file, sxp))
			return 0;
		if (ownership_differs(file, sxp))
			return 0;
#ifdef SUPPORT_ACLS
		if (acls_differ(fname, file, sxp))
			return 0;
#endif
#ifdef SUPPORT_XATTRS
		if (xattrs_differ(fname, file, sxp))
			return 0;
#endif
	}

	return 1;
}

void itemize(const char *fnamecmp, struct file_struct *file, int ndx, int statret,
	     stat_x *sxp, int32 iflags, uchar fnamecmp_type,
	     const char *xname)
{
	if (statret >= 0) { /* A from-dest-dir statret can == 1! */
		int keep_time = !preserve_times ? 0
		    : S_ISDIR(file->mode) ? preserve_times & PRESERVE_DIR_TIMES
		    : S_ISLNK(file->mode) ? preserve_times & PRESERVE_LINK_TIMES
		    : 1;

		if (S_ISREG(file->mode) && F_LENGTH(file) != sxp->st.st_size)
			iflags |= ITEM_REPORT_SIZE;
		if (file->flags & FLAG_TIME_FAILED) { /* symlinks only */
			if (iflags & ITEM_LOCAL_CHANGE)
				iflags |= symlink_timeset_failed_flags;
		} else if (keep_time
		 ? cmp_time(file->modtime, sxp->st.st_mtime) != 0
		 : iflags & (ITEM_TRANSFER|ITEM_LOCAL_CHANGE) && !(iflags & ITEM_MATCHED)
		  && (!(iflags & ITEM_XNAME_FOLLOWS) || *xname))
			iflags |= ITEM_REPORT_TIME;
#if !defined HAVE_LCHMOD && !defined HAVE_SETATTRLIST
		if (S_ISLNK(file->mode)) {
			;
		} else
#endif
		if (preserve_perms) {
			if (!BITS_EQUAL(sxp->st.st_mode, file->mode, CHMOD_BITS))
				iflags |= ITEM_REPORT_PERMS;
		} else if (preserve_executability
		 && ((sxp->st.st_mode & 0111 ? 1 : 0) ^ (file->mode & 0111 ? 1 : 0)))
			iflags |= ITEM_REPORT_PERMS;
		if (uid_ndx && am_root && (uid_t)F_OWNER(file) != sxp->st.st_uid)
			iflags |= ITEM_REPORT_OWNER;
		if (gid_ndx && !(file->flags & FLAG_SKIP_GROUP)
		    && sxp->st.st_gid != (gid_t)F_GROUP(file))
			iflags |= ITEM_REPORT_GROUP;
#ifdef SUPPORT_ACLS
		if (preserve_acls && !S_ISLNK(file->mode)) {
			if (!ACL_READY(*sxp))
				get_acl(fnamecmp, sxp);
			if (set_acl(NULL, file, sxp, file->mode))
				iflags |= ITEM_REPORT_ACL;
		}
#endif
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs) {
			if (!XATTR_READY(*sxp))
				get_xattr(fnamecmp, sxp);
			if (xattr_diff(file, sxp, 1))
				iflags |= ITEM_REPORT_XATTR;
		}
#endif
	} else {
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs && xattr_diff(file, NULL, 1))
			iflags |= ITEM_REPORT_XATTR;
#endif
		iflags |= ITEM_IS_NEW;
	}

	iflags &= 0xffff;
	if ((iflags & (SIGNIFICANT_ITEM_FLAGS|ITEM_REPORT_XATTR) || INFO_GTE(NAME, 2)
	  || stdout_format_has_i > 1 || (xname && *xname)) && !read_batch) {
		if (protocol_version >= 29) {
			if (ndx >= 0)
				write_ndx(sock_f_out, ndx);
			write_shortint(sock_f_out, iflags);
			if (iflags & ITEM_BASIS_TYPE_FOLLOWS)
				write_byte(sock_f_out, fnamecmp_type);
			if (iflags & ITEM_XNAME_FOLLOWS)
				write_vstring(sock_f_out, xname, strlen(xname));
#ifdef SUPPORT_XATTRS
			if (preserve_xattrs && do_xfers
			 && iflags & (ITEM_REPORT_XATTR|ITEM_TRANSFER)) {
				int fd = iflags & ITEM_REPORT_XATTR
				      && !(want_xattr_optim && BITS_SET(iflags, ITEM_XNAME_FOLLOWS|ITEM_LOCAL_CHANGE))
				       ? sock_f_out : -1;
				send_xattr_request(NULL, file, fd);
			}
#endif
		} else if (ndx >= 0) {
			enum logcode code = logfile_format_has_i ? FINFO : FCLIENT;
			log_item(code, file, iflags, xname);
		}
	}
}


/* Perform our quick-check heuristic for determining if a file is unchanged. */
int unchanged_file(char *fn, struct file_struct *file, STRUCT_STAT *st)
{
	if (st->st_size != F_LENGTH(file))
		return 0;

	/* if always checksum is set then we use the checksum instead
	   of the file time to determine whether to sync */
	if (always_checksum > 0 && S_ISREG(st->st_mode)) {
		char sum[MAX_DIGEST_LEN];
		file_checksum(fn, st, sum);
		return memcmp(sum, F_SUM(file), checksum_len) == 0;
	}

	if (size_only > 0)
		return 1;

	if (ignore_times)
		return 0;

	return cmp_time(st->st_mtime, file->modtime) == 0;
}


/*
 * set (initialize) the size entries in the per-file sum_struct
 * calculating dynamic block and checksum sizes.
 *
 * This is only called from generate_and_send_sums() but is a separate
 * function to encapsulate the logic.
 *
 * The block size is a rounded square root of file length.
 *
 * The checksum size is determined according to:
 *     blocksum_bits = BLOCKSUM_BIAS + 2*log2(file_len) - log2(block_len)
 * provided by Donovan Baarda which gives a probability of rsync
 * algorithm corrupting data and falling back using the whole md4
 * checksums.
 *
 * This might be made one of several selectable heuristics.
 */
static void sum_sizes_sqroot(struct sum_struct *sum, int64 len)
{
	int32 blength;
	int s2length;
	int64 l;

	if (len < 0) {
		/* The file length overflowed our int64 var, so we can't process this file. */
		sum->count = -1; /* indicate overflow error */
		return;
	}

	if (block_size)
		blength = block_size;
	else if (len <= BLOCK_SIZE * BLOCK_SIZE)
		blength = BLOCK_SIZE;
	else {
		int32 max_blength = protocol_version < 30 ? OLD_MAX_BLOCK_SIZE : MAX_BLOCK_SIZE;
		int32 c;
		int cnt;
		for (c = 1, l = len, cnt = 0; l >>= 2; c <<= 1, cnt++) {}
		if (c < 0 || c >= max_blength)
			blength = max_blength;
		else {
		    blength = 0;
		    do {
			    blength |= c;
			    if (len < (int64)blength * blength)
				    blength &= ~c;
			    c >>= 1;
		    } while (c >= 8);	/* round to multiple of 8 */
		    blength = MAX(blength, BLOCK_SIZE);
		}
	}

	if (protocol_version < 27) {
		s2length = csum_length;
	} else if (csum_length == SUM_LENGTH) {
		s2length = SUM_LENGTH;
	} else {
		int32 c;
		int b = BLOCKSUM_BIAS;
		for (l = len; l >>= 1; b += 2) {}
		for (c = blength; (c >>= 1) && b; b--) {}
		/* add a bit, subtract rollsum, round up. */
		s2length = (b + 1 - 32 + 7) / 8; /* --optimize in compiler-- */
		s2length = MAX(s2length, csum_length);
		s2length = MIN(s2length, SUM_LENGTH);
	}

	sum->flength	= len;
	sum->blength	= blength;
	sum->s2length	= s2length;
	sum->remainder	= (int32)(len % blength);
	sum->count	= (int32)(l = (len / blength) + (sum->remainder != 0));

	if ((int64)sum->count != l)
		sum->count = -1;

	if (sum->count && DEBUG_GTE(DELTASUM, 2)) {
		rprintf(FINFO,
			"count=%s rem=%ld blength=%ld s2length=%d flength=%s\n",
			big_num(sum->count), (long)sum->remainder, (long)sum->blength,
			sum->s2length, big_num(sum->flength));
	}
}


/*
 * Generate and send a stream of signatures/checksums that describe a buffer
 *
 * Generate approximately one checksum every block_len bytes.
 */
static int generate_and_send_sums(int fd, OFF_T len, int f_out, int f_copy)
{
	int32 i;
	struct map_struct *mapbuf;
	struct sum_struct sum;
	OFF_T offset = 0;

	sum_sizes_sqroot(&sum, len);
	if (sum.count < 0)
		return -1;
	write_sum_head(f_out, &sum);

	if (append_mode > 0 && f_copy < 0)
		return 0;

	if (len > 0)
		mapbuf = map_file(fd, len, MAX_MAP_SIZE, sum.blength);
	else
		mapbuf = NULL;

	for (i = 0; i < sum.count; i++) {
		int32 n1 = (int32)MIN(len, (OFF_T)sum.blength);
		char *map = map_ptr(mapbuf, offset, n1);
		char sum2[SUM_LENGTH];
		uint32 sum1;

		len -= n1;
		offset += n1;

		if (f_copy >= 0) {
			full_write(f_copy, map, n1);
			if (append_mode > 0)
				continue;
		}

		sum1 = get_checksum1(map, n1);
		get_checksum2(map, n1, sum2);

		if (DEBUG_GTE(DELTASUM, 3)) {
			rprintf(FINFO,
				"chunk[%s] offset=%s len=%ld sum1=%08lx\n",
				big_num(i), big_num(offset - n1), (long)n1,
				(unsigned long)sum1);
		}
		write_int(f_out, sum1);
		write_buf(f_out, sum2, sum.s2length);
	}

	if (mapbuf)
		unmap_file(mapbuf);

	return 0;
}


/* Try to find a filename in the same dir as "fname" with a similar name. */
static struct file_struct *find_fuzzy(struct file_struct *file, struct file_list *dirlist_array[], uchar *fnamecmp_type_ptr)
{
	int fname_len, fname_suf_len;
	const char *fname_suf, *fname = file->basename;
	uint32 lowest_dist = 25 << 16; /* ignore a distance greater than 25 */
	int i, j;
	struct file_struct *lowest_fp = NULL;

	fname_len = strlen(fname);
	fname_suf = find_filename_suffix(fname, fname_len, &fname_suf_len);

	/* Try to find an exact size+mtime match first. */
	for (i = 0; i < fuzzy_basis; i++) {
		struct file_list *dirlist = dirlist_array[i];

		if (!dirlist)
			continue;

		for (j = 0; j < dirlist->used; j++) {
			struct file_struct *fp = dirlist->files[j];

			if (!F_IS_ACTIVE(fp))
				continue;

			if (!S_ISREG(fp->mode) || !F_LENGTH(fp) || fp->flags & FLAG_FILE_SENT)
				continue;

			if (F_LENGTH(fp) == F_LENGTH(file) && cmp_time(fp->modtime, file->modtime) == 0) {
				if (DEBUG_GTE(FUZZY, 2))
					rprintf(FINFO, "fuzzy size/modtime match for %s\n", f_name(fp, NULL));
				*fnamecmp_type_ptr = FNAMECMP_FUZZY + i;
				return fp;
			}

		}
	}

	for (i = 0; i < fuzzy_basis; i++) {
		struct file_list *dirlist = dirlist_array[i];

		if (!dirlist)
			continue;

		for (j = 0; j < dirlist->used; j++) {
			struct file_struct *fp = dirlist->files[j];
			const char *suf, *name;
			int len, suf_len;
			uint32 dist;

			if (!F_IS_ACTIVE(fp))
				continue;

			if (!S_ISREG(fp->mode) || !F_LENGTH(fp) || fp->flags & FLAG_FILE_SENT)
				continue;

			name = fp->basename;
			len = strlen(name);
			suf = find_filename_suffix(name, len, &suf_len);

			dist = fuzzy_distance(name, len, fname, fname_len);
			/* Add some extra weight to how well the suffixes match. */
			dist += fuzzy_distance(suf, suf_len, fname_suf, fname_suf_len) * 10;
			if (DEBUG_GTE(FUZZY, 2)) {
				rprintf(FINFO, "fuzzy distance for %s = %d.%05d\n",
					f_name(fp, NULL), (int)(dist>>16), (int)(dist&0xFFFF));
			}
			if (dist <= lowest_dist) {
				lowest_dist = dist;
				lowest_fp = fp;
				*fnamecmp_type_ptr = FNAMECMP_FUZZY + i;
			}
		}
	}

	return lowest_fp;
}

/* Copy a file found in our --copy-dest handling. */
static int copy_altdest_file(const char *src, const char *dest, struct file_struct *file)
{
	char buf[MAXPATHLEN];
	const char *copy_to, *partialptr;
	int save_preserve_xattrs = preserve_xattrs;
	int ok, fd_w;

	if (inplace) {
		/* Let copy_file open the destination in place. */
		fd_w = -1;
		copy_to = dest;
	} else {
		fd_w = open_tmpfile(buf, dest, file);
		if (fd_w < 0)
			return -1;
		copy_to = buf;
	}
	cleanup_set(copy_to, NULL, NULL, -1, -1);
	if (copy_file(src, copy_to, fd_w, file->mode) < 0) {
		if (INFO_GTE(COPY, 1)) {
			rsyserr(FINFO, errno, "copy_file %s => %s",
				full_fname(src), copy_to);
		}
		/* Try to clean up. */
		unlink(copy_to);
		cleanup_disable();
		return -1;
	}
	partialptr = partial_dir ? partial_dir_fname(dest) : NULL;
	preserve_xattrs = 0; /* xattrs were copied with file */
	ok = finish_transfer(dest, copy_to, src, partialptr, file, 1, 0);
	preserve_xattrs = save_preserve_xattrs;
	cleanup_disable();
	return ok ? 0 : -1;
}

/* This is only called for regular files.  We return -2 if we've finished
 * handling the file, -1 if no dest-linking occurred, or a non-negative
 * value if we found an alternate basis file.  If we're called with the
 * find_exact_for_existing flag, the destination file already exists, so
 * we only try to find an exact alt-dest match.  In this case, the returns
 * are only -2 & -1 (both as above). */
static int try_dests_reg(struct file_struct *file, char *fname, int ndx,
			 char *cmpbuf, stat_x *sxp, int find_exact_for_existing,
			 int itemizing, enum logcode code)
{
	STRUCT_STAT real_st = sxp->st;
	int best_match = -1;
	int match_level = 0;
	int j = 0;

	do {
		pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
		if (link_stat(cmpbuf, &sxp->st, 0) < 0 || !S_ISREG(sxp->st.st_mode))
			continue;
		switch (match_level) {
		case 0:
			best_match = j;
			match_level = 1;
			/* FALL THROUGH */
		case 1:
			if (!unchanged_file(cmpbuf, file, &sxp->st))
				continue;
			best_match = j;
			match_level = 2;
			/* FALL THROUGH */
		case 2:
			if (!unchanged_attrs(cmpbuf, file, sxp)) {
				free_stat_x(sxp);
				continue;
			}
			best_match = j;
			match_level = 3;
			break;
		}
		break;
	} while (basis_dir[++j] != NULL);

	if (!match_level)
		goto got_nothing_for_ya;

	if (j != best_match) {
		j = best_match;
		pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
		if (link_stat(cmpbuf, &sxp->st, 0) < 0)
			goto got_nothing_for_ya;
	}

	if (match_level == 3 && !copy_dest) {
		if (find_exact_for_existing) {
			if (link_dest && real_st.st_dev == sxp->st.st_dev && real_st.st_ino == sxp->st.st_ino)
				return -1;
			if (do_unlink(fname) < 0 && errno != ENOENT)
				goto got_nothing_for_ya;
		}
#ifdef SUPPORT_HARD_LINKS
		if (link_dest) {
			if (!hard_link_one(file, fname, cmpbuf, 1))
				goto try_a_copy;
			if (preserve_hard_links && F_IS_HLINKED(file))
				finish_hard_link(file, fname, ndx, &sxp->st, itemizing, code, j);
			if (!maybe_ATTRS_REPORT && (INFO_GTE(NAME, 2) || stdout_format_has_i > 1)) {
				itemize(cmpbuf, file, ndx, 1, sxp,
					ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS,
					0, "");
			}
		} else
#endif
		{
			if (itemizing)
				itemize(cmpbuf, file, ndx, 0, sxp, 0, 0, NULL);
		}
		if (INFO_GTE(NAME, 2) && maybe_ATTRS_REPORT)
			rprintf(FCLIENT, "%s is uptodate\n", fname);
		return -2;
	}

	if (find_exact_for_existing)
		goto got_nothing_for_ya;

	if (match_level >= 2) {
#ifdef SUPPORT_HARD_LINKS
	  try_a_copy: /* Copy the file locally. */
#endif
		if (!dry_run && copy_altdest_file(cmpbuf, fname, file) < 0) {
			if (find_exact_for_existing) /* Can get here via hard-link failure */
				goto got_nothing_for_ya;
			return -1;
		}
		if (itemizing)
			itemize(cmpbuf, file, ndx, 0, sxp, ITEM_LOCAL_CHANGE, 0, NULL);
		if (maybe_ATTRS_REPORT
		 && ((!itemizing && INFO_GTE(NAME, 1) && match_level == 2)
		  || (INFO_GTE(NAME, 2) && match_level == 3))) {
			code = match_level == 3 ? FCLIENT : FINFO;
			rprintf(code, "%s%s\n", fname,
				match_level == 3 ? " is uptodate" : "");
		}
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && F_IS_HLINKED(file))
			finish_hard_link(file, fname, ndx, &sxp->st, itemizing, code, -1);
#endif
		return -2;
	}

	return FNAMECMP_BASIS_DIR_LOW + j;

got_nothing_for_ya:
	sxp->st = real_st;
	return -1;
}

/* This is only called for non-regular files.  We return -2 if we've finished
 * handling the file, or -1 if no dest-linking occurred, or a non-negative
 * value if we found an alternate basis file. */
static int try_dests_non(struct file_struct *file, char *fname, int ndx,
			 char *cmpbuf, stat_x *sxp, int itemizing,
			 enum logcode code)
{
	int best_match = -1;
	int match_level = 0;
	enum nonregtype type;
	uint32 *devp;
#ifdef SUPPORT_LINKS
	char lnk[MAXPATHLEN];
	int len;
#endif
	int j = 0;

#ifndef SUPPORT_LINKS
	if (S_ISLNK(file->mode))
		return -1;
#endif
	if (S_ISDIR(file->mode)) {
		type = TYPE_DIR;
	} else if (IS_SPECIAL(file->mode))
		type = TYPE_SPECIAL;
	else if (IS_DEVICE(file->mode))
		type = TYPE_DEVICE;
#ifdef SUPPORT_LINKS
	else if (S_ISLNK(file->mode))
		type = TYPE_SYMLINK;
#endif
	else {
		rprintf(FERROR,
			"internal: try_dests_non() called with invalid mode (%o)\n",
			(int)file->mode);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	do {
		pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
		if (link_stat(cmpbuf, &sxp->st, 0) < 0)
			continue;
		switch (type) {
		case TYPE_DIR:
			if (!S_ISDIR(sxp->st.st_mode))
				continue;
			break;
		case TYPE_SPECIAL:
			if (!IS_SPECIAL(sxp->st.st_mode))
				continue;
			break;
		case TYPE_DEVICE:
			if (!IS_DEVICE(sxp->st.st_mode))
				continue;
			break;
		case TYPE_SYMLINK:
#ifdef SUPPORT_LINKS
			if (!S_ISLNK(sxp->st.st_mode))
				continue;
			break;
#else
			return -1;
#endif
		}
		if (match_level < 1) {
			match_level = 1;
			best_match = j;
		}
		switch (type) {
		case TYPE_DIR:
		case TYPE_SPECIAL:
			break;
		case TYPE_DEVICE:
			devp = F_RDEV_P(file);
			if (sxp->st.st_rdev != MAKEDEV(DEV_MAJOR(devp), DEV_MINOR(devp)))
				continue;
			break;
		case TYPE_SYMLINK:
#ifdef SUPPORT_LINKS
			if ((len = do_readlink(cmpbuf, lnk, MAXPATHLEN-1)) <= 0)
				continue;
			lnk[len] = '\0';
			if (strcmp(lnk, F_SYMLINK(file)) != 0)
				continue;
			break;
#else
			return -1;
#endif
		}
		if (match_level < 2) {
			match_level = 2;
			best_match = j;
		}
		if (unchanged_attrs(cmpbuf, file, sxp)) {
			match_level = 3;
			best_match = j;
			break;
		}
	} while (basis_dir[++j] != NULL);

	if (!match_level)
		return -1;

	if (j != best_match) {
		j = best_match;
		pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
		if (link_stat(cmpbuf, &sxp->st, 0) < 0)
			return -1;
	}

	if (match_level == 3) {
#ifdef SUPPORT_HARD_LINKS
		if (link_dest
#ifndef CAN_HARDLINK_SYMLINK
		 && !S_ISLNK(file->mode)
#endif
#ifndef CAN_HARDLINK_SPECIAL
		 && !IS_SPECIAL(file->mode) && !IS_DEVICE(file->mode)
#endif
		 && !S_ISDIR(file->mode)) {
			if (do_link(cmpbuf, fname) < 0) {
				rsyserr(FERROR_XFER, errno,
					"failed to hard-link %s with %s",
					cmpbuf, fname);
				return j;
			}
			if (preserve_hard_links && F_IS_HLINKED(file))
				finish_hard_link(file, fname, ndx, NULL, itemizing, code, -1);
		} else
#endif
			match_level = 2;
		if (itemizing && stdout_format_has_i
		 && (INFO_GTE(NAME, 2) || stdout_format_has_i > 1)) {
			int chg = compare_dest && type != TYPE_DIR ? 0
			    : ITEM_LOCAL_CHANGE + (match_level == 3 ? ITEM_XNAME_FOLLOWS : 0);
			char *lp = match_level == 3 ? "" : NULL;
			itemize(cmpbuf, file, ndx, 0, sxp, chg + ITEM_MATCHED, 0, lp);
		}
		if (INFO_GTE(NAME, 2) && maybe_ATTRS_REPORT) {
			rprintf(FCLIENT, "%s%s is uptodate\n",
				fname, type == TYPE_DIR ? "/" : "");
		}
		return -2;
	}

	return j;
}

static void list_file_entry(struct file_struct *f)
{
	char permbuf[PERMSTRING_SIZE];
	int64 len;
	int colwidth = human_readable ? 14 : 11;

	if (!F_IS_ACTIVE(f)) {
		/* this can happen if duplicate names were removed */
		return;
	}

	permstring(permbuf, f->mode);
	len = F_LENGTH(f);

	/* TODO: indicate '+' if the entry has an ACL. */

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(f->mode)) {
		rprintf(FINFO, "%s %*s %s %s -> %s\n",
			permbuf, colwidth, human_num(len),
			timestring(f->modtime), f_name(f, NULL),
			F_SYMLINK(f));
	} else
#endif
	if (missing_args == 2 && f->mode == 0) {
		rprintf(FINFO, "%-*s %s\n",
			colwidth + 31, "*missing",
			f_name(f, NULL));
	} else {
		rprintf(FINFO, "%s %*s %s %s\n",
			permbuf, colwidth, human_num(len),
			timestring(f->modtime), f_name(f, NULL));
	}
}

static int phase = 0;
static int dflt_perms;

static int implied_dirs_are_missing;
/* Helper for recv_generator's skip_dir and dry_missing_dir tests. */
static BOOL is_below(struct file_struct *file, struct file_struct *subtree)
{
	return F_DEPTH(file) > F_DEPTH(subtree)
		&& (!implied_dirs_are_missing || f_name_has_prefix(file, subtree));
}

/* Acts on the indicated item in cur_flist whose name is fname.  If a dir,
 * make sure it exists, and has the right permissions/timestamp info.  For
 * all other non-regular files (symlinks, etc.) we create them here.  For
 * regular files that have changed, we try to find a basis file and then
 * start sending checksums.  The ndx is the file's unique index value.
 *
 * The fname parameter must point to a MAXPATHLEN buffer!  (e.g it gets
 * passed to delete_item(), which can use it during a recursive delete.)
 *
 * Note that f_out is set to -1 when doing final directory-permission and
 * modification-time repair. */
static void recv_generator(char *fname, struct file_struct *file, int ndx,
			   int itemizing, enum logcode code, int f_out)
{
	static const char *parent_dirname = "";
	static struct file_struct *prior_dir_file = NULL;
	/* Missing dir not created due to --dry-run; will still be scanned. */
	static struct file_struct *dry_missing_dir = NULL;
	/* Missing dir whose contents are skipped altogether due to
	 * --ignore-non-existing, daemon exclude, or mkdir failure. */
	static struct file_struct *skip_dir = NULL;
	static struct file_list *fuzzy_dirlist[MAX_BASIS_DIRS+1];
	static int need_fuzzy_dirlist = 0;
	struct file_struct *fuzzy_file = NULL;
	int fd = -1, f_copy = -1;
	stat_x sx, real_sx;
	STRUCT_STAT partial_st;
	struct file_struct *back_file = NULL;
	int statret, real_ret, stat_errno;
	char *fnamecmp, *partialptr, *backupptr = NULL;
	char fnamecmpbuf[MAXPATHLEN];
	uchar fnamecmp_type;
	int del_opts = delete_mode || force_delete ? DEL_RECURSE : 0;
	int is_dir = !S_ISDIR(file->mode) ? 0
		   : inc_recurse && ndx != cur_flist->ndx_start - 1 ? -1
		   : 1;

	if (DEBUG_GTE(GENR, 1))
		rprintf(FINFO, "recv_generator(%s,%d)\n", fname, ndx);

	if (list_only) {
		if (is_dir < 0
		 || (is_dir && !implied_dirs && file->flags & FLAG_IMPLIED_DIR))
			return;
		list_file_entry(file);
		return;
	}

	if (skip_dir) {
		if (is_below(file, skip_dir)) {
			if (is_dir)
				file->flags |= FLAG_MISSING_DIR;
#ifdef SUPPORT_HARD_LINKS
			else if (F_IS_HLINKED(file))
				handle_skipped_hlink(file, itemizing, code, f_out);
#endif
			return;
		}
		skip_dir = NULL;
	}

	init_stat_x(&sx);
	if (daemon_filter_list.head && (*fname != '.' || fname[1])) {
		if (check_filter(&daemon_filter_list, FLOG, fname, is_dir) < 0) {
			if (is_dir < 0)
				return;
#ifdef SUPPORT_HARD_LINKS
			if (F_IS_HLINKED(file))
				handle_skipped_hlink(file, itemizing, code, f_out);
#endif
			rprintf(FERROR_XFER,
				"ERROR: daemon refused to receive %s \"%s\"\n",
				is_dir ? "directory" : "file", fname);
			if (is_dir)
				goto skipping_dir_contents;
			return;
		}
	}

	if (dry_run > 1 || (dry_missing_dir && is_below(file, dry_missing_dir))) {
		int i;
	  parent_is_dry_missing:
		for (i = 0; i < fuzzy_basis; i++) {
			if (fuzzy_dirlist[i]) {
				flist_free(fuzzy_dirlist[i]);
				fuzzy_dirlist[i] = NULL;
			}
		}
		parent_dirname = "";
		statret = -1;
		stat_errno = ENOENT;
	} else {
		const char *dn = file->dirname ? file->dirname : ".";
		dry_missing_dir = NULL;
		if (parent_dirname != dn && strcmp(parent_dirname, dn) != 0) {
			/* Each parent dir must be in the file list or the flist data is bad.
			 * Optimization: most of the time the parent dir will be the last dir
			 * this function was asked to process in the file list. */
			if (!inc_recurse
			 && (*dn != '.' || dn[1]) /* Avoid an issue with --relative and the "." dir. */
			 && (prior_dir_file && strcmp(dn, f_name(prior_dir_file, NULL)) != 0)
			 && flist_find_name(cur_flist, dn, 1) < 0) {
				rprintf(FERROR,
					"ABORTING due to invalid path from sender: %s/%s\n",
					dn, file->basename);
				exit_cleanup(RERR_PROTOCOL);
			}
			if (relative_paths && !implied_dirs
			 && do_stat(dn, &sx.st) < 0) {
				if (dry_run)
					goto parent_is_dry_missing;
				if (make_path(fname, MKP_DROP_NAME | MKP_SKIP_SLASH) < 0) {
					rsyserr(FERROR_XFER, errno,
						"recv_generator: mkdir %s failed",
						full_fname(dn));
				}
			}
			if (fuzzy_basis) {
				int i;
				for (i = 0; i < fuzzy_basis; i++) {
					if (fuzzy_dirlist[i]) {
						flist_free(fuzzy_dirlist[i]);
						fuzzy_dirlist[i] = NULL;
					}
				}
				need_fuzzy_dirlist = 1;
			}
#ifdef SUPPORT_ACLS
			if (!preserve_perms)
				dflt_perms = default_perms_for_dir(dn);
#endif
		}
		parent_dirname = dn;

		if (need_fuzzy_dirlist && S_ISREG(file->mode)) {
			int i;
			strlcpy(fnamecmpbuf, dn, sizeof fnamecmpbuf);
			for (i = 0; i < fuzzy_basis; i++) {
				if (i && pathjoin(fnamecmpbuf, MAXPATHLEN, basis_dir[i-1], dn) >= MAXPATHLEN)
					continue;
				fuzzy_dirlist[i] = get_dirlist(fnamecmpbuf, -1, GDL_IGNORE_FILTER_RULES);
				if (fuzzy_dirlist[i] && fuzzy_dirlist[i]->used == 0) {
					flist_free(fuzzy_dirlist[i]);
					fuzzy_dirlist[i] = NULL;
				}
			}
			need_fuzzy_dirlist = 0;
		}

		statret = link_stat(fname, &sx.st, keep_dirlinks && is_dir);
		stat_errno = errno;
	}

	if (missing_args == 2 && file->mode == 0) {
		if (filter_list.head && check_filter(&filter_list, FINFO, fname, is_dir) < 0)
			return;
		if (statret == 0)
			delete_item(fname, sx.st.st_mode, del_opts);
		return;
	}

	if (ignore_non_existing > 0 && statret == -1 && stat_errno == ENOENT) {
		if (is_dir) {
			if (is_dir < 0)
				return;
			skip_dir = file;
			file->flags |= FLAG_MISSING_DIR;
		}
#ifdef SUPPORT_HARD_LINKS
		else if (F_IS_HLINKED(file))
			handle_skipped_hlink(file, itemizing, code, f_out);
#endif
		if (INFO_GTE(SKIP, 1)) {
			rprintf(FINFO, "not creating new %s \"%s\"\n",
				is_dir ? "directory" : "file", fname);
		}
		return;
	}

	if (statret == 0 && !(sx.st.st_mode & S_IWUSR)
	 && !am_root && sx.st.st_uid == our_uid)
		del_opts |= DEL_NO_UID_WRITE;

	if (ignore_existing > 0 && statret == 0
	 && (!is_dir || !S_ISDIR(sx.st.st_mode))) {
		if (INFO_GTE(SKIP, 1) && is_dir >= 0)
			rprintf(FINFO, "%s exists\n", fname);
#ifdef SUPPORT_HARD_LINKS
		if (F_IS_HLINKED(file))
			handle_skipped_hlink(file, itemizing, code, f_out);
#endif
		goto cleanup;
	}

	fnamecmp = fname;

	if (is_dir) {
		mode_t added_perms;
		if (!implied_dirs && file->flags & FLAG_IMPLIED_DIR)
			goto cleanup;
		if (am_root < 0) {
			/* For --fake-super, the dir must be useable by the copying
			 * user, just like it would be for root. */
			added_perms = S_IRUSR|S_IWUSR|S_IXUSR;
		} else
			added_perms = 0;
		if (is_dir < 0) {
			if (!(preserve_times & PRESERVE_DIR_TIMES))
				return;
			/* In inc_recurse mode we want to make sure any missing
			 * directories get created while we're still processing
			 * the parent dir (which allows us to touch the parent
			 * dir's mtime right away).  We will handle the dir in
			 * full later (right before we handle its contents). */
			if (statret == 0
			 && (S_ISDIR(sx.st.st_mode)
			  || delete_item(fname, sx.st.st_mode, del_opts | DEL_FOR_DIR) != 0))
				goto cleanup; /* Any errors get reported later. */
			if (do_mkdir(fname, (file->mode|added_perms) & 0700) == 0)
				file->flags |= FLAG_DIR_CREATED;
			goto cleanup;
		}
		/* The file to be received is a directory, so we need
		 * to prepare appropriately.  If there is already a
		 * file of that name and it is *not* a directory, then
		 * we need to delete it.  If it doesn't exist, then
		 * (perhaps recursively) create it. */
		if (statret == 0 && !S_ISDIR(sx.st.st_mode)) {
			if (delete_item(fname, sx.st.st_mode, del_opts | DEL_FOR_DIR) != 0)
				goto skipping_dir_contents;
			statret = -1;
		}
		if (dry_run && statret != 0) {
			if (!dry_missing_dir)
				dry_missing_dir = file;
			file->flags |= FLAG_MISSING_DIR;
		}
		init_stat_x(&real_sx);
		real_sx.st = sx.st;
		real_ret = statret;
		if (file->flags & FLAG_DIR_CREATED)
			statret = -1;
		if (!preserve_perms) { /* See comment in non-dir code below. */
			file->mode = dest_mode(file->mode, sx.st.st_mode,
					       dflt_perms, statret == 0);
		}
		if (statret != 0 && basis_dir[0] != NULL) {
			int j = try_dests_non(file, fname, ndx, fnamecmpbuf, &sx,
					      itemizing, code);
			if (j == -2) {
				itemizing = 0;
				code = FNONE;
				statret = 1;
			} else if (j >= 0) {
				statret = 1;
				fnamecmp = fnamecmpbuf;
			}
		}
		if (itemizing && f_out != -1) {
			itemize(fnamecmp, file, ndx, statret, &sx,
				statret ? ITEM_LOCAL_CHANGE : 0, 0, NULL);
		}
		if (real_ret != 0 && do_mkdir(fname,file->mode|added_perms) < 0 && errno != EEXIST) {
			if (!relative_paths || errno != ENOENT
			 || make_path(fname, MKP_DROP_NAME | MKP_SKIP_SLASH) < 0
			 || (do_mkdir(fname, file->mode|added_perms) < 0 && errno != EEXIST)) {
				rsyserr(FERROR_XFER, errno,
					"recv_generator: mkdir %s failed",
					full_fname(fname));
			  skipping_dir_contents:
				rprintf(FERROR,
				    "*** Skipping any contents from this failed directory ***\n");
				skip_dir = file;
				file->flags |= FLAG_MISSING_DIR;
				goto cleanup;
			}
		}

#ifdef SUPPORT_XATTRS
		if (preserve_xattrs && statret == 1)
			copy_xattrs(fnamecmpbuf, fname);
#endif
		if (set_file_attrs(fname, file, real_ret ? NULL : &real_sx, NULL, 0)
		    && INFO_GTE(NAME, 1) && code != FNONE && f_out != -1)
			rprintf(code, "%s/\n", fname);

		/* We need to ensure that the dirs in the transfer have both
		 * readable and writable permissions during the time we are
		 * putting files within them.  This is then restored to the
		 * former permissions after the transfer is done. */
#ifdef HAVE_CHMOD
		if (!am_root && (file->mode & S_IRWXU) != S_IRWXU && dir_tweaking) {
			mode_t mode = file->mode | S_IRWXU;
			if (do_chmod(fname, mode) < 0) {
				rsyserr(FERROR_XFER, errno,
					"failed to modify permissions on %s",
					full_fname(fname));
			}
			need_retouch_dir_perms = 1;
		}
#endif

		if (real_ret != 0 && one_file_system)
			real_sx.st.st_dev = filesystem_dev;
		if (inc_recurse) {
			if (one_file_system) {
				uint32 *devp = F_DIR_DEV_P(file);
				DEV_MAJOR(devp) = major(real_sx.st.st_dev);
				DEV_MINOR(devp) = minor(real_sx.st.st_dev);
			}
		}
		else if (delete_during && f_out != -1 && !phase
		    && !(file->flags & FLAG_MISSING_DIR)) {
			if (file->flags & FLAG_CONTENT_DIR)
				delete_in_dir(fname, file, &real_sx.st.st_dev);
			else
				change_local_filter_dir(fname, strlen(fname), F_DEPTH(file));
		}
		prior_dir_file = file;
		goto cleanup;
	}

	/* If we're not preserving permissions, change the file-list's
	 * mode based on the local permissions and some heuristics. */
	if (!preserve_perms) {
		int exists = statret == 0 && !S_ISDIR(sx.st.st_mode);
		file->mode = dest_mode(file->mode, sx.st.st_mode, dflt_perms,
				       exists);
	}

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && F_HLINK_NOT_FIRST(file)
	 && hard_link_check(file, ndx, fname, statret, &sx, itemizing, code))
		goto cleanup;
#endif

	if (preserve_links && S_ISLNK(file->mode)) {
#ifdef SUPPORT_LINKS
		const char *sl = F_SYMLINK(file);
		if (safe_symlinks && unsafe_symlink(sl, fname)) {
			if (INFO_GTE(NAME, 1)) {
				if (solo_file) {
					/* fname contains the destination path, but we
					 * want to report the source path. */
					fname = f_name(file, NULL);
				}
				rprintf(FINFO,
					"ignoring unsafe symlink \"%s\" -> \"%s\"\n",
					fname, sl);
			}
			return;
		}
		if (statret == 0) {
			char lnk[MAXPATHLEN];
			int len;

			if (S_ISLNK(sx.st.st_mode)
			 && (len = do_readlink(fname, lnk, MAXPATHLEN-1)) > 0
			 && strncmp(lnk, sl, len) == 0 && sl[len] == '\0') {
				/* The link is pointing to the right place. */
				set_file_attrs(fname, file, &sx, NULL, maybe_ATTRS_REPORT);
				if (itemizing)
					itemize(fname, file, ndx, 0, &sx, 0, 0, NULL);
#ifdef SUPPORT_HARD_LINKS
				if (preserve_hard_links && F_IS_HLINKED(file))
					finish_hard_link(file, fname, ndx, &sx.st, itemizing, code, -1);
#endif
				if (remove_source_files == 1)
					goto return_with_success;
				goto cleanup;
			}
		} else if (basis_dir[0] != NULL) {
			int j = try_dests_non(file, fname, ndx, fnamecmpbuf, &sx,
					      itemizing, code);
			if (j == -2) {
#ifndef CAN_HARDLINK_SYMLINK
				if (link_dest) {
					/* Resort to --copy-dest behavior. */
				} else
#endif
				if (!copy_dest)
					goto cleanup;
				itemizing = 0;
				code = FNONE;
			} else if (j >= 0) {
				statret = 1;
				fnamecmp = fnamecmpbuf;
			}
		}
		if (atomic_create(file, fname, sl, NULL, MAKEDEV(0, 0), &sx, statret == 0 ? DEL_FOR_SYMLINK : 0)) {
			set_file_attrs(fname, file, NULL, NULL, 0);
			if (itemizing) {
				if (statret == 0 && !S_ISLNK(sx.st.st_mode))
					statret = -1;
				itemize(fnamecmp, file, ndx, statret, &sx,
					ITEM_LOCAL_CHANGE|ITEM_REPORT_CHANGE, 0, NULL);
			}
			if (code != FNONE && INFO_GTE(NAME, 1))
				rprintf(code, "%s -> %s\n", fname, sl);
#ifdef SUPPORT_HARD_LINKS
			if (preserve_hard_links && F_IS_HLINKED(file))
				finish_hard_link(file, fname, ndx, NULL, itemizing, code, -1);
#endif
			/* This does not check remove_source_files == 1
			 * because this is one of the items that the old
			 * --remove-sent-files option would remove. */
			if (remove_source_files)
				goto return_with_success;
		}
#endif
		goto cleanup;
	}

	if ((am_root && preserve_devices && IS_DEVICE(file->mode))
	 || (preserve_specials && IS_SPECIAL(file->mode))) {
		dev_t rdev;
		int del_for_flag = 0;
		if (IS_DEVICE(file->mode)) {
			uint32 *devp = F_RDEV_P(file);
			rdev = MAKEDEV(DEV_MAJOR(devp), DEV_MINOR(devp));
		} else
			rdev = 0;
		if (statret == 0) {
			if (IS_DEVICE(file->mode)) {
				if (!IS_DEVICE(sx.st.st_mode))
					statret = -1;
				del_for_flag = DEL_FOR_DEVICE;
			} else {
				if (!IS_SPECIAL(sx.st.st_mode))
					statret = -1;
				del_for_flag = DEL_FOR_SPECIAL;
			}
			if (statret == 0
			 && BITS_EQUAL(sx.st.st_mode, file->mode, _S_IFMT)
			 && (IS_SPECIAL(sx.st.st_mode) || sx.st.st_rdev == rdev)) {
				/* The device or special file is identical. */
				set_file_attrs(fname, file, &sx, NULL, maybe_ATTRS_REPORT);
				if (itemizing)
					itemize(fname, file, ndx, 0, &sx, 0, 0, NULL);
#ifdef SUPPORT_HARD_LINKS
				if (preserve_hard_links && F_IS_HLINKED(file))
					finish_hard_link(file, fname, ndx, &sx.st, itemizing, code, -1);
#endif
				if (remove_source_files == 1)
					goto return_with_success;
				goto cleanup;
			}
		} else if (basis_dir[0] != NULL) {
			int j = try_dests_non(file, fname, ndx, fnamecmpbuf, &sx,
					      itemizing, code);
			if (j == -2) {
#ifndef CAN_HARDLINK_SPECIAL
				if (link_dest) {
					/* Resort to --copy-dest behavior. */
				} else
#endif
				if (!copy_dest)
					goto cleanup;
				itemizing = 0;
				code = FNONE;
			} else if (j >= 0) {
				statret = 1;
				fnamecmp = fnamecmpbuf;
			}
		}
		if (DEBUG_GTE(GENR, 1)) {
			rprintf(FINFO, "mknod(%s, 0%o, [%ld,%ld])\n",
				fname, (int)file->mode,
				(long)major(rdev), (long)minor(rdev));
		}
		if (atomic_create(file, fname, NULL, NULL, rdev, &sx, del_for_flag)) {
			set_file_attrs(fname, file, NULL, NULL, 0);
			if (itemizing) {
				itemize(fnamecmp, file, ndx, statret, &sx,
					ITEM_LOCAL_CHANGE|ITEM_REPORT_CHANGE, 0, NULL);
			}
			if (code != FNONE && INFO_GTE(NAME, 1))
				rprintf(code, "%s\n", fname);
#ifdef SUPPORT_HARD_LINKS
			if (preserve_hard_links && F_IS_HLINKED(file))
				finish_hard_link(file, fname, ndx, NULL, itemizing, code, -1);
#endif
			if (remove_source_files == 1)
				goto return_with_success;
		}
		goto cleanup;
	}

	if (!S_ISREG(file->mode)) {
		if (solo_file)
			fname = f_name(file, NULL);
		rprintf(FINFO, "skipping non-regular file \"%s\"\n", fname);
		goto cleanup;
	}

	if (max_size >= 0 && F_LENGTH(file) > max_size) {
		if (INFO_GTE(SKIP, 1)) {
			if (solo_file)
				fname = f_name(file, NULL);
			rprintf(FINFO, "%s is over max-size\n", fname);
		}
		goto cleanup;
	}
	if (min_size >= 0 && F_LENGTH(file) < min_size) {
		if (INFO_GTE(SKIP, 1)) {
			if (solo_file)
				fname = f_name(file, NULL);
			rprintf(FINFO, "%s is under min-size\n", fname);
		}
		goto cleanup;
	}

	if (update_only > 0 && statret == 0
	    && cmp_time(sx.st.st_mtime, file->modtime) > 0) {
		if (INFO_GTE(SKIP, 1))
			rprintf(FINFO, "%s is newer\n", fname);
#ifdef SUPPORT_HARD_LINKS
		if (F_IS_HLINKED(file))
			handle_skipped_hlink(file, itemizing, code, f_out);
#endif
		goto cleanup;
	}

	fnamecmp_type = FNAMECMP_FNAME;

	if (statret == 0 && !S_ISREG(sx.st.st_mode)) {
		if (delete_item(fname, sx.st.st_mode, del_opts | DEL_FOR_FILE) != 0)
			goto cleanup;
		statret = -1;
		stat_errno = ENOENT;
	}

	if (basis_dir[0] != NULL && (statret != 0 || !copy_dest)) {
		int j = try_dests_reg(file, fname, ndx, fnamecmpbuf, &sx,
				      statret == 0, itemizing, code);
		if (j == -2) {
			if (remove_source_files == 1)
				goto return_with_success;
			goto cleanup;
		}
		if (j >= 0) {
			fnamecmp = fnamecmpbuf;
			fnamecmp_type = j;
			statret = 0;
		}
	}

	init_stat_x(&real_sx);
	real_sx.st = sx.st; /* Don't copy xattr/acl pointers, as they would free wrong. */
	real_ret = statret;

	if (partial_dir && (partialptr = partial_dir_fname(fname)) != NULL
	    && link_stat(partialptr, &partial_st, 0) == 0
	    && S_ISREG(partial_st.st_mode)) {
		if (statret != 0)
			goto prepare_to_open;
	} else
		partialptr = NULL;

	if (statret != 0 && fuzzy_basis) {
		/* Sets fnamecmp_type to FNAMECMP_FUZZY or above. */
		fuzzy_file = find_fuzzy(file, fuzzy_dirlist, &fnamecmp_type);
		if (fuzzy_file) {
			f_name(fuzzy_file, fnamecmpbuf);
			if (DEBUG_GTE(FUZZY, 1)) {
				rprintf(FINFO, "fuzzy basis selected for %s: %s\n",
					fname, fnamecmpbuf);
			}
			sx.st.st_size = F_LENGTH(fuzzy_file);
			statret = 0;
			fnamecmp = fnamecmpbuf;
		}
	}

	if (statret != 0) {
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && F_HLINK_NOT_LAST(file)) {
			cur_flist->in_progress++;
			goto cleanup;
		}
#endif
		if (stat_errno == ENOENT)
			goto notify_others;
		rsyserr(FERROR_XFER, stat_errno, "recv_generator: failed to stat %s",
			full_fname(fname));
		goto cleanup;
	}

	if (fnamecmp_type <= FNAMECMP_BASIS_DIR_HIGH)
		;
	else if (fnamecmp_type == FNAMECMP_FUZZY)
		;
	else if (unchanged_file(fnamecmp, file, &sx.st)) {
		if (partialptr) {
			do_unlink(partialptr);
			handle_partial_dir(partialptr, PDIR_DELETE);
		}
		set_file_attrs(fname, file, &sx, NULL, maybe_ATTRS_REPORT);
		if (itemizing)
			itemize(fnamecmp, file, ndx, statret, &sx, 0, 0, NULL);
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && F_IS_HLINKED(file))
			finish_hard_link(file, fname, ndx, &sx.st, itemizing, code, -1);
#endif
		if (remove_source_files != 1)
			goto cleanup;
	  return_with_success:
		if (!dry_run)
			send_msg_int(MSG_SUCCESS, ndx);
		goto cleanup;
	}

	if (append_mode > 0 && sx.st.st_size >= F_LENGTH(file)) {
#ifdef SUPPORT_HARD_LINKS
		if (F_IS_HLINKED(file))
			handle_skipped_hlink(file, itemizing, code, f_out);
#endif
		goto cleanup;
	}

  prepare_to_open:
	if (partialptr) {
		sx.st = partial_st;
		fnamecmp = partialptr;
		fnamecmp_type = FNAMECMP_PARTIAL_DIR;
		statret = 0;
	}

	if (!do_xfers)
		goto notify_others;

	if (read_batch || whole_file) {
		if (inplace && make_backups > 0 && fnamecmp_type == FNAMECMP_FNAME) {
			if (!(backupptr = get_backup_name(fname)))
				goto cleanup;
			if (!(back_file = make_file(fname, NULL, NULL, 0, NO_FILTERS)))
				goto pretend_missing;
			if (copy_file(fname, backupptr, -1, back_file->mode) < 0) {
				unmake_file(back_file);
				back_file = NULL;
				goto cleanup;
			}
		}
		goto notify_others;
	}

	if (fuzzy_dirlist[0]) {
		int j = flist_find(fuzzy_dirlist[0], file);
		if (j >= 0) /* don't use changing file as future fuzzy basis */
			fuzzy_dirlist[0]->files[j]->flags |= FLAG_FILE_SENT;
	}

	/* open the file */
	if ((fd = do_open(fnamecmp, O_RDONLY, 0)) < 0) {
		rsyserr(FERROR, errno, "failed to open %s, continuing",
			full_fname(fnamecmp));
	  pretend_missing:
		/* pretend the file didn't exist */
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && F_HLINK_NOT_LAST(file)) {
			cur_flist->in_progress++;
			goto cleanup;
		}
#endif
		statret = real_ret = -1;
		goto notify_others;
	}

	if (inplace && make_backups > 0 && fnamecmp_type == FNAMECMP_FNAME) {
		if (!(backupptr = get_backup_name(fname))) {
			close(fd);
			goto cleanup;
		}
		if (!(back_file = make_file(fname, NULL, NULL, 0, NO_FILTERS))) {
			close(fd);
			goto pretend_missing;
		}
		if (robust_unlink(backupptr) && errno != ENOENT) {
			rsyserr(FERROR_XFER, errno, "unlink %s",
				full_fname(backupptr));
			unmake_file(back_file);
			back_file = NULL;
			close(fd);
			goto cleanup;
		}
		if ((f_copy = do_open(backupptr, O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, 0600)) < 0) {
			rsyserr(FERROR_XFER, errno, "open %s", full_fname(backupptr));
			unmake_file(back_file);
			back_file = NULL;
			close(fd);
			goto cleanup;
		}
		fnamecmp_type = FNAMECMP_BACKUP;
	}

	if (DEBUG_GTE(DELTASUM, 3)) {
		rprintf(FINFO, "gen mapped %s of size %s\n",
			fnamecmp, big_num(sx.st.st_size));
	}

	if (DEBUG_GTE(DELTASUM, 2))
		rprintf(FINFO, "generating and sending sums for %d\n", ndx);

  notify_others:
	if (remove_source_files && !delay_updates && !phase && !dry_run)
		increment_active_files(ndx, itemizing, code);
	if (inc_recurse && (!dry_run || write_batch < 0))
		cur_flist->in_progress++;
#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && F_IS_HLINKED(file))
		file->flags |= FLAG_FILE_SENT;
#endif
	write_ndx(f_out, ndx);
	if (itemizing) {
		int iflags = ITEM_TRANSFER;
		if (always_checksum > 0)
			iflags |= ITEM_REPORT_CHANGE;
		if (fnamecmp_type != FNAMECMP_FNAME)
			iflags |= ITEM_BASIS_TYPE_FOLLOWS;
		if (fnamecmp_type >= FNAMECMP_FUZZY)
			iflags |= ITEM_XNAME_FOLLOWS;
		itemize(fnamecmp, file, -1, real_ret, &real_sx, iflags, fnamecmp_type,
			fuzzy_file ? fuzzy_file->basename : NULL);
		free_stat_x(&real_sx);
	}

	if (!do_xfers) {
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && F_IS_HLINKED(file))
			finish_hard_link(file, fname, ndx, &sx.st, itemizing, code, -1);
#endif
		goto cleanup;
	}
	if (read_batch)
		goto cleanup;

	if (statret != 0 || whole_file)
		write_sum_head(f_out, NULL);
	else if (sx.st.st_size <= 0) {
		write_sum_head(f_out, NULL);
		close(fd);
	} else {
		if (generate_and_send_sums(fd, sx.st.st_size, f_out, f_copy) < 0) {
			rprintf(FWARNING,
			    "WARNING: file is too large for checksum sending: %s\n",
			    fnamecmp);
			write_sum_head(f_out, NULL);
		}
		close(fd);
	}

  cleanup:
	if (back_file) {
		int save_preserve_xattrs = preserve_xattrs;
		if (f_copy >= 0)
			close(f_copy);
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs) {
			copy_xattrs(fname, backupptr);
			preserve_xattrs = 0;
		}
#endif
		set_file_attrs(backupptr, back_file, NULL, NULL, 0);
		preserve_xattrs = save_preserve_xattrs;
		if (INFO_GTE(BACKUP, 1)) {
			rprintf(FINFO, "backed up %s to %s\n",
				fname, backupptr);
		}
		unmake_file(back_file);
	}

	free_stat_x(&sx);
}

/* If we are replacing an existing hard link, symlink, device, or special file,
 * create a temp-name item and rename it into place.  A symlimk specifies slnk,
 * a hard link specifies hlnk, otherwise we create a device based on rdev.
 * Specify 0 for the del_for_flag if there is not a file to replace.  This
 * returns 1 on success and 0 on failure. */
int atomic_create(struct file_struct *file, char *fname, const char *slnk, const char *hlnk,
		  dev_t rdev, stat_x *sxp, int del_for_flag)
{
	char tmpname[MAXPATHLEN];
	const char *create_name;
	int skip_atomic, dir_in_the_way = del_for_flag && S_ISDIR(sxp->st.st_mode);

	if (!del_for_flag || dir_in_the_way || tmpdir || !get_tmpname(tmpname, fname, True))
		skip_atomic = 1;
	else
		skip_atomic = 0;

	if (del_for_flag) {
		if (make_backups > 0 && !dir_in_the_way) {
			if (!make_backup(fname, skip_atomic))
				return 0;
		} else if (skip_atomic) {
			int del_opts = delete_mode || force_delete ? DEL_RECURSE : 0;
			if (delete_item(fname, sxp->st.st_mode, del_opts | del_for_flag) != 0)
				return 0;
		}
	}

	create_name = skip_atomic ? fname : tmpname;

	if (slnk) {
#ifdef SUPPORT_LINKS
		if (do_symlink(slnk, create_name) < 0) {
			rsyserr(FERROR_XFER, errno, "symlink %s -> \"%s\" failed",
				full_fname(create_name), slnk);
			return 0;
		}
#else
		return 0;
#endif
	} else if (hlnk) {
#ifdef SUPPORT_HARD_LINKS
		if (!hard_link_one(file, create_name, hlnk, 0))
			return 0;
#else
		return 0;
#endif
	} else {
		if (do_mknod(create_name, file->mode, rdev) < 0) {
			rsyserr(FERROR_XFER, errno, "mknod %s failed",
				full_fname(create_name));
			return 0;
		}
	}

	if (!skip_atomic) {
		if (do_rename(tmpname, fname) < 0) {
			rsyserr(FERROR_XFER, errno, "rename %s -> \"%s\" failed",
				full_fname(tmpname), full_fname(fname));
			do_unlink(tmpname);
			return 0;
		}
	}

	return 1;
}

#ifdef SUPPORT_HARD_LINKS
static void handle_skipped_hlink(struct file_struct *file, int itemizing,
				 enum logcode code, int f_out)
{
	char fbuf[MAXPATHLEN];
	int new_last_ndx;
	struct file_list *save_flist = cur_flist;

	/* If we skip the last item in a chain of links and there was a
	 * prior non-skipped hard-link waiting to finish, finish it now. */
	if ((new_last_ndx = skip_hard_link(file, &cur_flist)) < 0)
		return;

	file = cur_flist->files[new_last_ndx - cur_flist->ndx_start];
	cur_flist->in_progress--; /* undo prior increment */
	f_name(file, fbuf);
	recv_generator(fbuf, file, new_last_ndx, itemizing, code, f_out);

	cur_flist = save_flist;
}
#endif

static void touch_up_dirs(struct file_list *flist, int ndx)
{
	static int counter = 0;
	struct file_struct *file;
	char *fname;
	BOOL fix_dir_perms;
	int i, start, end;

	if (ndx < 0) {
		start = 0;
		end = flist->used - 1;
	} else
		start = end = ndx;

	/* Fix any directory permissions that were modified during the
	 * transfer and/or re-set any tweaked modified-time values. */
	for (i = start; i <= end; i++, counter++) {
		file = flist->files[i];
		if (!F_IS_ACTIVE(file))
			continue;
		if (!S_ISDIR(file->mode)
		 || (!implied_dirs && file->flags & FLAG_IMPLIED_DIR))
			continue;
		if (DEBUG_GTE(TIME, 2)) {
			fname = f_name(file, NULL);
			rprintf(FINFO, "touch_up_dirs: %s (%d)\n",
				NS(fname), i);
		}
		/* Be sure not to retouch permissions with --fake-super. */
		fix_dir_perms = !am_root && !(file->mode & S_IWUSR);
		if (file->flags & FLAG_MISSING_DIR || !(need_retouch_dir_times || fix_dir_perms))
			continue;
		fname = f_name(file, NULL);
		if (fix_dir_perms)
			do_chmod(fname, file->mode);
		if (need_retouch_dir_times) {
			STRUCT_STAT st;
			if (link_stat(fname, &st, 0) == 0
			 && cmp_time(st.st_mtime, file->modtime) != 0)
				set_modtime(fname, file->modtime, F_MOD_NSEC(file), file->mode);
		}
		if (counter >= loopchk_limit) {
			if (allowed_lull)
				maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH);
			else
				maybe_flush_socket(0);
			counter = 0;
		}
	}
}

void check_for_finished_files(int itemizing, enum logcode code, int check_redo)
{
	struct file_struct *file;
	struct file_list *flist;
	char fbuf[MAXPATHLEN];
	int ndx;

	while (1) {
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && (ndx = get_hlink_num()) != -1) {
			int send_failed = (ndx == -2);
			if (send_failed)
				ndx = get_hlink_num();
			flist = flist_for_ndx(ndx, "check_for_finished_files.1");
			file = flist->files[ndx - flist->ndx_start];
			assert(file->flags & FLAG_HLINKED);
			if (send_failed)
				handle_skipped_hlink(file, itemizing, code, sock_f_out);
			else
				finish_hard_link(file, f_name(file, fbuf), ndx, NULL, itemizing, code, -1);
			flist->in_progress--;
			continue;
		}
#endif

		if (check_redo && (ndx = get_redo_num()) != -1) {
			OFF_T save_max_size = max_size;
			OFF_T save_min_size = min_size;
			csum_length = SUM_LENGTH;
			max_size = -1;
			min_size = -1;
			ignore_existing = -ignore_existing;
			ignore_non_existing = -ignore_non_existing;
			update_only = -update_only;
			always_checksum = -always_checksum;
			size_only = -size_only;
			append_mode = -append_mode;
			make_backups = -make_backups; /* avoid dup backup w/inplace */
			ignore_times++;

			flist = cur_flist;
			cur_flist = flist_for_ndx(ndx, "check_for_finished_files.2");

			file = cur_flist->files[ndx - cur_flist->ndx_start];
			if (solo_file)
				strlcpy(fbuf, solo_file, sizeof fbuf);
			else
				f_name(file, fbuf);
			recv_generator(fbuf, file, ndx, itemizing, code, sock_f_out);
			cur_flist->to_redo--;

			cur_flist = flist;

			csum_length = SHORT_SUM_LENGTH;
			max_size = save_max_size;
			min_size = save_min_size;
			ignore_existing = -ignore_existing;
			ignore_non_existing = -ignore_non_existing;
			update_only = -update_only;
			always_checksum = -always_checksum;
			size_only = -size_only;
			append_mode = -append_mode;
			make_backups = -make_backups;
			ignore_times--;
			continue;
		}

		if (cur_flist == first_flist)
			break;

		/* We only get here if inc_recurse is enabled. */
		if (first_flist->in_progress || first_flist->to_redo)
			break;

		write_ndx(sock_f_out, NDX_DONE);
		if (!read_batch && !flist_eof) {
			int old_total = 0;
			for (flist = first_flist; flist != cur_flist; flist = flist->next)
				old_total += flist->used;
			maybe_flush_socket(!flist_eof && file_total - old_total < MIN_FILECNT_LOOKAHEAD/2);
		}

		if (delete_during == 2 || !dir_tweaking) {
			/* Skip directory touch-up. */
		} else if (first_flist->parent_ndx >= 0)
			touch_up_dirs(dir_flist, first_flist->parent_ndx);

		flist_free(first_flist); /* updates first_flist */
	}
}

void generate_files(int f_out, const char *local_name)
{
	int i, ndx, next_loopchk = 0;
	char fbuf[MAXPATHLEN];
	int itemizing;
	enum logcode code;
	int save_info_flist = info_levels[INFO_FLIST];
	int save_info_progress = info_levels[INFO_PROGRESS];

	if (protocol_version >= 29) {
		itemizing = 1;
		maybe_ATTRS_REPORT = stdout_format_has_i ? 0 : ATTRS_REPORT;
		code = logfile_format_has_i ? FNONE : FLOG;
	} else if (am_daemon) {
		itemizing = logfile_format_has_i && do_xfers;
		maybe_ATTRS_REPORT = ATTRS_REPORT;
		code = itemizing || !do_xfers ? FCLIENT : FINFO;
	} else if (!am_server) {
		itemizing = stdout_format_has_i;
		maybe_ATTRS_REPORT = stdout_format_has_i ? 0 : ATTRS_REPORT;
		code = itemizing ? FNONE : FINFO;
	} else {
		itemizing = 0;
		maybe_ATTRS_REPORT = ATTRS_REPORT;
		code = FINFO;
	}
	solo_file = local_name;
	dir_tweaking = !(list_only || solo_file || dry_run);
	need_retouch_dir_times = preserve_times & PRESERVE_DIR_TIMES;
	loopchk_limit = allowed_lull ? allowed_lull * 5 : 200;
	symlink_timeset_failed_flags = ITEM_REPORT_TIME
	    | (protocol_version >= 30 || !am_server ? ITEM_REPORT_TIMEFAIL : 0);
	implied_dirs_are_missing = relative_paths && !implied_dirs && protocol_version < 30;

	if (DEBUG_GTE(GENR, 1))
		rprintf(FINFO, "generator starting pid=%d\n", (int)getpid());

	if (delete_before && !solo_file && cur_flist->used > 0)
		do_delete_pass();
	if (delete_during == 2) {
		deldelay_size = BIGPATHBUFLEN * 4;
		deldelay_buf = new_array(char, deldelay_size);
		if (!deldelay_buf)
			out_of_memory("delete-delay");
	}
	info_levels[INFO_FLIST] = info_levels[INFO_PROGRESS] = 0;

	if (append_mode > 0 || whole_file < 0)
		whole_file = 0;
	if (DEBUG_GTE(FLIST, 1)) {
		rprintf(FINFO, "delta-transmission %s\n",
			whole_file
			? "disabled for local transfer or --whole-file"
			: "enabled");
	}

	dflt_perms = (ACCESSPERMS & ~orig_umask);

	do {
#ifdef SUPPORT_HARD_LINKS
		if (preserve_hard_links && inc_recurse) {
			while (!flist_eof && file_total < MIN_FILECNT_LOOKAHEAD/2)
				wait_for_receiver();
		}
#endif

		if (inc_recurse && cur_flist->parent_ndx >= 0) {
			struct file_struct *fp = dir_flist->files[cur_flist->parent_ndx];
			if (solo_file)
				strlcpy(fbuf, solo_file, sizeof fbuf);
			else
				f_name(fp, fbuf);
			ndx = cur_flist->ndx_start - 1;
			recv_generator(fbuf, fp, ndx, itemizing, code, f_out);
			if (delete_during && dry_run < 2 && !list_only
			 && !(fp->flags & FLAG_MISSING_DIR)) {
				if (fp->flags & FLAG_CONTENT_DIR) {
					dev_t dirdev;
					if (one_file_system) {
						uint32 *devp = F_DIR_DEV_P(fp);
						dirdev = MAKEDEV(DEV_MAJOR(devp), DEV_MINOR(devp));
					} else
						dirdev = MAKEDEV(0, 0);
					delete_in_dir(fbuf, fp, &dirdev);
				} else
					change_local_filter_dir(fbuf, strlen(fbuf), F_DEPTH(fp));
			}
		}
		for (i = cur_flist->low; i <= cur_flist->high; i++) {
			struct file_struct *file = cur_flist->sorted[i];

			if (!F_IS_ACTIVE(file))
				continue;

			if (unsort_ndx)
				ndx = F_NDX(file);
			else
				ndx = i + cur_flist->ndx_start;

			if (solo_file)
				strlcpy(fbuf, solo_file, sizeof fbuf);
			else
				f_name(file, fbuf);
			recv_generator(fbuf, file, ndx, itemizing, code, f_out);

			check_for_finished_files(itemizing, code, 0);

			if (i + cur_flist->ndx_start >= next_loopchk) {
				if (allowed_lull)
					maybe_send_keepalive(time(NULL), MSK_ALLOW_FLUSH);
				else
					maybe_flush_socket(0);
				next_loopchk += loopchk_limit;
			}
		}

		if (!inc_recurse) {
			write_ndx(f_out, NDX_DONE);
			break;
		}

		while (1) {
			check_for_finished_files(itemizing, code, 1);
			if (cur_flist->next || flist_eof)
				break;
			wait_for_receiver();
		}
	} while ((cur_flist = cur_flist->next) != NULL);

	if (delete_during)
		delete_in_dir(NULL, NULL, &dev_zero);
	phase++;
	if (DEBUG_GTE(GENR, 1))
		rprintf(FINFO, "generate_files phase=%d\n", phase);

	while (1) {
		check_for_finished_files(itemizing, code, 1);
		if (msgdone_cnt)
			break;
		wait_for_receiver();
	}

	phase++;
	if (DEBUG_GTE(GENR, 1))
		rprintf(FINFO, "generate_files phase=%d\n", phase);

	write_ndx(f_out, NDX_DONE);

	/* Reduce round-trip lag-time for a useless delay-updates phase. */
	if (protocol_version >= 29 && EARLY_DELAY_DONE_MSG())
		write_ndx(f_out, NDX_DONE);

	if (protocol_version >= 31 && EARLY_DELETE_DONE_MSG()) {
		if ((INFO_GTE(STATS, 2) && (delete_mode || force_delete)) || read_batch)
			write_del_stats(f_out);
		if (EARLY_DELAY_DONE_MSG()) /* Can't send this before delay */
			write_ndx(f_out, NDX_DONE);
	}

	/* Read MSG_DONE for the redo phase (and any prior messages). */
	while (1) {
		check_for_finished_files(itemizing, code, 0);
		if (msgdone_cnt > 1)
			break;
		wait_for_receiver();
	}

	if (protocol_version >= 29) {
		phase++;
		if (DEBUG_GTE(GENR, 1))
			rprintf(FINFO, "generate_files phase=%d\n", phase);
		if (!EARLY_DELAY_DONE_MSG()) {
			write_ndx(f_out, NDX_DONE);
			if (protocol_version >= 31 && EARLY_DELETE_DONE_MSG())
				write_ndx(f_out, NDX_DONE);
		}
		/* Read MSG_DONE for delay-updates phase & prior messages. */
		while (msgdone_cnt == 2)
			wait_for_receiver();
	}

	info_levels[INFO_FLIST] = save_info_flist;
	info_levels[INFO_PROGRESS] = save_info_progress;

	if (delete_during == 2)
		do_delayed_deletions(fbuf);
	if (delete_after && !solo_file && file_total > 0)
		do_delete_pass();

	if (max_delete >= 0 && skipped_deletes) {
		rprintf(FWARNING,
			"Deletions stopped due to --max-delete limit (%d skipped)\n",
			skipped_deletes);
		io_error |= IOERR_DEL_LIMIT;
	}

	if (protocol_version >= 31) {
		if (!EARLY_DELETE_DONE_MSG()) {
			if (INFO_GTE(STATS, 2) || read_batch)
				write_del_stats(f_out);
			write_ndx(f_out, NDX_DONE);
		}

		/* Read MSG_DONE for late-delete phase & prior messages. */
		while (msgdone_cnt == 3)
			wait_for_receiver();
	}

	if ((need_retouch_dir_perms || need_retouch_dir_times)
	 && dir_tweaking && (!inc_recurse || delete_during == 2))
		touch_up_dirs(dir_flist, -1);

	if (DEBUG_GTE(GENR, 1))
		rprintf(FINFO, "generate_files finished\n");
}
