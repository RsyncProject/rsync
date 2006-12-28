/*
 * Generate and receive file lists.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2002, 2003, 2004, 2005, 2006 Wayne Davison
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
#include "rounding.h"

extern int verbose;
extern int list_only;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int am_sender;
extern int incremental;
extern int do_progress;
extern int always_checksum;
extern int module_id;
extern int ignore_errors;
extern int numeric_ids;
extern int recurse;
extern int xfer_dirs;
extern int filesfrom_fd;
extern int one_file_system;
extern int copy_dirlinks;
extern int keep_dirlinks;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_devices;
extern int preserve_specials;
extern int preserve_uid;
extern int preserve_gid;
extern int relative_paths;
extern int implied_dirs;
extern int file_extra_cnt;
extern int ignore_perishable;
extern int non_perishable_cnt;
extern int prune_empty_dirs;
extern int copy_links;
extern int copy_unsafe_links;
extern int protocol_version;
extern int sanitize_paths;
extern struct stats stats;

extern char curr_dir[MAXPATHLEN];

extern struct chmod_mode_struct *chmod_modes;

extern struct filter_list_struct filter_list;
extern struct filter_list_struct server_filter_list;

int io_error;
int checksum_len;
dev_t filesystem_dev; /* used to implement -x */

struct file_list *cur_flist, *first_flist, *dir_flist;
int send_dir_ndx = -1, send_dir_depth = 0;
int flist_cnt = 0; /* how many (non-tmp) file list objects exist */
int file_total = 0; /* total of all active items over all file-lists */
int flist_eof = 0; /* all the file-lists are now known */

/* The tmp_* vars are used as a cache area by make_file() to store data
 * that the sender doesn't need to remember in its file list.  The data
 * will survive just long enough to be used by send_file_entry(). */
static dev_t tmp_rdev;
#ifdef SUPPORT_HARD_LINKS
static int64 tmp_dev, tmp_ino;
#endif
static char tmp_sum[MD4_SUM_LENGTH];

static char empty_sum[MD4_SUM_LENGTH];
static int flist_count_offset; /* for --delete --progress */

static void clean_flist(struct file_list *flist, int strip_root, int no_dups);
static void output_flist(struct file_list *flist);

void init_flist(void)
{
	if (verbose > 4) {
		rprintf(FINFO, "FILE_STRUCT_LEN=%d, EXTRA_LEN=%d\n",
			(int)FILE_STRUCT_LEN, (int)EXTRA_LEN);
	}
	checksum_len = protocol_version < 21 ? 2 : MD4_SUM_LENGTH;
}

static int show_filelist_p(void)
{
	return verbose && xfer_dirs && !am_server && !incremental;
}

static void start_filelist_progress(char *kind)
{
	rprintf(FCLIENT, "%s ... ", kind);
	if (verbose > 1 || do_progress)
		rprintf(FCLIENT, "\n");
	rflush(FINFO);
}

static void emit_filelist_progress(int count)
{
	rprintf(FCLIENT, " %d files...\r", count);
}

static void maybe_emit_filelist_progress(int count)
{
	if (do_progress && show_filelist_p() && (count % 100) == 0)
		emit_filelist_progress(count);
}

static void finish_filelist_progress(const struct file_list *flist)
{
	if (do_progress) {
		/* This overwrites the progress line */
		rprintf(FINFO, "%d file%sto consider\n",
			flist->count, flist->count == 1 ? " " : "s ");
	} else
		rprintf(FINFO, "done\n");
}

void show_flist_stats(void)
{
	/* Nothing yet */
}

static void list_file_entry(struct file_struct *f)
{
	char permbuf[PERMSTRING_SIZE];
	double len;

	if (!F_IS_ACTIVE(f)) {
		/* this can happen if duplicate names were removed */
		return;
	}

	permstring(permbuf, f->mode);
	len = F_LENGTH(f);

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(f->mode)) {
		rprintf(FINFO, "%s %11.0f %s %s -> %s\n",
			permbuf, len, timestring(f->modtime),
			f_name(f, NULL), F_SYMLINK(f));
	} else
#endif
	{
		rprintf(FINFO, "%s %11.0f %s %s\n",
			permbuf, len, timestring(f->modtime),
			f_name(f, NULL));
	}
}

/* Stat either a symlink or its referent, depending on the settings of
 * copy_links, copy_unsafe_links, etc.  Returns -1 on error, 0 on success.
 *
 * If path is the name of a symlink, then the linkbuf buffer (which must hold
 * MAXPATHLEN chars) will be set to the symlink's target string.
 *
 * The stat structure pointed to by stp will contain information about the
 * link or the referent as appropriate, if they exist. */
static int readlink_stat(const char *path, STRUCT_STAT *stp, char *linkbuf)
{
#ifdef SUPPORT_LINKS
	if (link_stat(path, stp, copy_dirlinks) < 0)
		return -1;
	if (S_ISLNK(stp->st_mode)) {
		int llen = readlink(path, linkbuf, MAXPATHLEN - 1);
		if (llen < 0)
			return -1;
		linkbuf[llen] = '\0';
		if (copy_unsafe_links && unsafe_symlink(linkbuf, path)) {
			if (verbose > 1) {
				rprintf(FINFO,"copying unsafe symlink \"%s\" -> \"%s\"\n",
					path, linkbuf);
			}
			return do_stat(path, stp);
		}
	}
	return 0;
#else
	return do_stat(path, stp);
#endif
}

int link_stat(const char *path, STRUCT_STAT *stp, int follow_dirlinks)
{
#ifdef SUPPORT_LINKS
	if (copy_links)
		return do_stat(path, stp);
	if (do_lstat(path, stp) < 0)
		return -1;
	if (follow_dirlinks && S_ISLNK(stp->st_mode)) {
		STRUCT_STAT st;
		if (do_stat(path, &st) == 0 && S_ISDIR(st.st_mode))
			*stp = st;
	}
	return 0;
#else
	return do_stat(path, stp);
#endif
}

/* This function is used to check if a file should be included/excluded
 * from the list of files based on its name and type etc.  The value of
 * filter_level is set to either SERVER_FILTERS or ALL_FILTERS. */
static int is_excluded(char *fname, int is_dir, int filter_level)
{
#if 0 /* This currently never happens, so avoid a useless compare. */
	if (filter_level == NO_FILTERS)
		return 0;
#endif
	if (fname) {
		/* never exclude '.', even if somebody does --exclude '*' */
		if (fname[0] == '.' && !fname[1])
			return 0;
		/* Handle the -R version of the '.' dir. */
		if (fname[0] == '/') {
			int len = strlen(fname);
			if (fname[len-1] == '.' && fname[len-2] == '/')
				return 0;
		}
	}
	if (server_filter_list.head
	    && check_filter(&server_filter_list, fname, is_dir) < 0)
		return 1;
	if (filter_level != ALL_FILTERS)
		return 0;
	if (filter_list.head
	    && check_filter(&filter_list, fname, is_dir) < 0)
		return 1;
	return 0;
}

static int to_wire_mode(mode_t mode)
{
#ifdef SUPPORT_LINKS
#if _S_IFLNK != 0120000
	if (S_ISLNK(mode))
		return (mode & ~(_S_IFMT)) | 0120000;
#endif
#endif
	return mode;
}

static mode_t from_wire_mode(int mode)
{
#if _S_IFLNK != 0120000
	if ((mode & (_S_IFMT)) == 0120000)
		return (mode & ~(_S_IFMT)) | _S_IFLNK;
#endif
	return mode;
}

static void send_directory(int f, struct file_list *flist, int ndx,
			   char *fbuf, int len, int flags);

static const char *flist_dir, *orig_dir;
static int flist_dir_len;


/**
 * Make sure @p flist is big enough to hold at least @p flist->count
 * entries.
 **/
void flist_expand(struct file_list *flist)
{
	struct file_struct **new_ptr;

	if (flist->count < flist->malloced)
		return;

	if (flist->malloced < FLIST_START)
		flist->malloced = FLIST_START;
	else if (flist->malloced >= FLIST_LINEAR)
		flist->malloced += FLIST_LINEAR;
	else
		flist->malloced *= 2;

	/*
	 * In case count jumped or we are starting the list
	 * with a known size just set it.
	 */
	if (flist->malloced < flist->count)
		flist->malloced = flist->count;

	new_ptr = realloc_array(flist->files, struct file_struct *,
				flist->malloced);

	if (verbose >= 2 && flist->malloced != FLIST_START) {
		rprintf(FCLIENT, "[%s] expand file_list to %.0f bytes, did%s move\n",
		    who_am_i(),
		    (double)sizeof flist->files[0] * flist->malloced,
		    (new_ptr == flist->files) ? " not" : "");
	}

	flist->files = new_ptr;

	if (!flist->files)
		out_of_memory("flist_expand");
}

int push_flist_dir(const char *dir, int len)
{
	if (dir == flist_dir)
		return 1;

	if (!orig_dir)
		orig_dir = strdup(curr_dir);

	if (flist_dir && !pop_dir(orig_dir)) {
		rsyserr(FERROR, errno, "pop_dir %s failed",
			full_fname(orig_dir));
		exit_cleanup(RERR_FILESELECT);
	}

	if (dir && !push_dir(dir, 0)) {
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR, errno, "push_dir %s failed",
			full_fname(dir));
		return 0;
	}

	flist_dir = dir;
	flist_dir_len = len >= 0 ? len : dir ? (int)strlen(dir) : 0;

	return 1;
}

static void send_file_entry(struct file_struct *file, int f, int ndx)
{
	static time_t modtime;
	static mode_t mode;
	static int64 dev;
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static char *user_name, *group_name;
	static char lastname[MAXPATHLEN];
	char fname[MAXPATHLEN];
	int first_hlink_ndx = -1;
	int l1, l2;
	int flags;

	f_name(file, fname);

	flags = file->flags & FLAG_TOP_DIR; /* FLAG_TOP_DIR == XMIT_TOP_DIR */

	if (file->mode == mode)
		flags |= XMIT_SAME_MODE;
	else
		mode = file->mode;
	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		if (protocol_version < 28) {
			if (tmp_rdev == rdev)
				flags |= XMIT_SAME_RDEV_pre28;
			else
				rdev = tmp_rdev;
		} else {
			rdev = tmp_rdev;
			if ((uint32)major(rdev) == rdev_major)
				flags |= XMIT_SAME_RDEV_MAJOR;
			else
				rdev_major = major(rdev);
			if ((uint32)minor(rdev) <= 0xFFu)
				flags |= XMIT_RDEV_MINOR_IS_SMALL;
		}
	} else if (protocol_version < 28)
		rdev = MAKEDEV(0, 0);
	if (preserve_uid) {
		if (F_UID(file) == uid && *lastname)
			flags |= XMIT_SAME_UID;
		else {
			uid = F_UID(file);
			if (preserve_uid && !numeric_ids) {
				user_name = add_uid(uid);
				if (incremental && user_name)
					flags |= XMIT_USER_NAME_FOLLOWS;
			}
		}
	}
	if (preserve_gid) {
		if (F_GID(file) == gid && *lastname)
			flags |= XMIT_SAME_GID;
		else {
			gid = F_GID(file);
			if (preserve_gid && !numeric_ids) {
				group_name = add_gid(gid);
				if (incremental && group_name)
					flags |= XMIT_GROUP_NAME_FOLLOWS;
			}
		}
	}
	if (file->modtime == modtime)
		flags |= XMIT_SAME_TIME;
	else
		modtime = file->modtime;

#ifdef SUPPORT_HARD_LINKS
	if (tmp_dev != 0) {
		if (protocol_version >= 30) {
			struct idev_node *np = idev_node(tmp_dev, tmp_ino);
			first_hlink_ndx = (int32)np->data - 1;
			if (first_hlink_ndx < 0) {
				np->data = (void*)(ndx + 1);
				flags |= XMIT_HLINK_FIRST;
			}
			flags |= XMIT_HLINKED;
		} else {
			if (tmp_dev == dev) {
				if (protocol_version >= 28)
					flags |= XMIT_SAME_DEV_pre30;
			} else
				dev = tmp_dev;
			flags |= XMIT_HLINKED;
		}
	}
#endif

	for (l1 = 0;
	    lastname[l1] && (fname[l1] == lastname[l1]) && (l1 < 255);
	    l1++) {}
	l2 = strlen(fname+l1);

	if (l1 > 0)
		flags |= XMIT_SAME_NAME;
	if (l2 > 255)
		flags |= XMIT_LONG_NAME;

	/* We must make sure we don't send a zero flag byte or the
	 * other end will terminate the flist transfer.  Note that
	 * the use of XMIT_TOP_DIR on a non-dir has no meaning, so
	 * it's harmless way to add a bit to the first flag byte. */
	if (protocol_version >= 28) {
		if (!flags && !S_ISDIR(mode))
			flags |= XMIT_TOP_DIR;
		if ((flags & 0xFF00) || !flags) {
			flags |= XMIT_EXTENDED_FLAGS;
			write_shortint(f, flags);
		} else
			write_byte(f, flags);
	} else {
		if (!(flags & 0xFF))
			flags |= S_ISDIR(mode) ? XMIT_LONG_NAME : XMIT_TOP_DIR;
		write_byte(f, flags);
	}
	if (flags & XMIT_SAME_NAME)
		write_byte(f, l1);
	if (flags & XMIT_LONG_NAME)
		write_int(f, l2);
	else
		write_byte(f, l2);
	write_buf(f, fname + l1, l2);

	if (first_hlink_ndx >= 0) {
		write_int(f, first_hlink_ndx);
		goto the_end;
	}

	write_longint(f, F_LENGTH(file));
	if (!(flags & XMIT_SAME_TIME))
		write_int(f, modtime);
	if (!(flags & XMIT_SAME_MODE))
		write_int(f, to_wire_mode(mode));
	if (preserve_uid && !(flags & XMIT_SAME_UID)) {
		write_int(f, uid);
		if (flags & XMIT_USER_NAME_FOLLOWS) {
			int len = strlen(user_name);
			write_byte(f, len);
			write_buf(f, user_name, len);
		}
	}
	if (preserve_gid && !(flags & XMIT_SAME_GID)) {
		write_int(f, gid);
		if (flags & XMIT_GROUP_NAME_FOLLOWS) {
			int len = strlen(group_name);
			write_byte(f, len);
			write_buf(f, group_name, len);
		}
	}
	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		if (protocol_version < 28) {
			if (!(flags & XMIT_SAME_RDEV_pre28))
				write_int(f, (int)rdev);
		} else {
			if (!(flags & XMIT_SAME_RDEV_MAJOR))
				write_int(f, major(rdev));
			if (flags & XMIT_RDEV_MINOR_IS_SMALL)
				write_byte(f, minor(rdev));
			else
				write_int(f, minor(rdev));
		}
	}

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(mode)) {
		const char *sl = F_SYMLINK(file);
		int len = strlen(sl);
		write_int(f, len);
		write_buf(f, sl, len);
	}
#endif

#ifdef SUPPORT_HARD_LINKS
	if (tmp_dev != 0 && protocol_version < 30) {
		if (protocol_version < 26) {
			/* 32-bit dev_t and ino_t */
			write_int(f, (int32)dev);
			write_int(f, (int32)tmp_ino);
		} else {
			/* 64-bit dev_t and ino_t */
			if (!(flags & XMIT_SAME_DEV_pre30))
				write_longint(f, dev);
			write_longint(f, tmp_ino);
		}
	}
#endif

	if (always_checksum && (S_ISREG(mode) || protocol_version < 28)) {
		const char *sum;
		if (S_ISREG(mode))
			sum = tmp_sum;
		else {
			/* Prior to 28, we sent a useless set of nulls. */
			sum = empty_sum;
		}
		write_buf(f, sum, checksum_len);
	}

  the_end:
	strlcpy(lastname, fname, MAXPATHLEN);
}

static struct file_struct *recv_file_entry(struct file_list *flist,
					   int flags, int f)
{
	static time_t modtime;
	static mode_t mode;
	static int64 dev;
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static char lastname[MAXPATHLEN], *lastdir;
	static int lastdir_depth, lastdir_len = -1;
	static unsigned int del_hier_name_len = 0;
	static int in_del_hier = 0;
	char thisname[MAXPATHLEN];
	unsigned int l1 = 0, l2 = 0;
	int alloc_len, basename_len, linkname_len;
	int extra_len = file_extra_cnt * EXTRA_LEN;
	int first_hlink_ndx = -1;
	OFF_T file_length;
	const char *basename;
	char *bp;
	struct file_struct *file;

	if (flags & XMIT_SAME_NAME)
		l1 = read_byte(f);

	if (flags & XMIT_LONG_NAME)
		l2 = read_int(f);
	else
		l2 = read_byte(f);

	if (l2 >= MAXPATHLEN - l1) {
		rprintf(FERROR,
			"overflow: flags=0x%x l1=%d l2=%d lastname=%s [%s]\n",
			flags, l1, l2, lastname, who_am_i());
		overflow_exit("recv_file_entry");
	}

	strlcpy(thisname, lastname, l1 + 1);
	read_sbuf(f, &thisname[l1], l2);
	thisname[l1 + l2] = 0;

	strlcpy(lastname, thisname, MAXPATHLEN);

	clean_fname(thisname, 0);

	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0, NULL);

	if ((basename = strrchr(thisname, '/')) != NULL) {
		int len = basename++ - thisname;
		if (len != lastdir_len || memcmp(thisname, lastdir, len) != 0) {
			lastdir = new_array(char, len + 1);
			memcpy(lastdir, thisname, len);
			lastdir[len] = '\0';
			lastdir_len = len;
			lastdir_depth = count_dir_elements(lastdir);
		}
	} else
		basename = thisname;
	basename_len = strlen(basename) + 1; /* count the '\0' */

#ifdef SUPPORT_HARD_LINKS
	if (protocol_version >= 30
	 && BITS_SETnUNSET(flags, XMIT_HLINKED, XMIT_HLINK_FIRST)) {
		struct file_struct *first;
		first_hlink_ndx = read_int(f);
		if (first_hlink_ndx < 0 || first_hlink_ndx >= flist->count) {
			rprintf(FERROR,
				"hard-link reference out of range: %d (%d)\n",
				first_hlink_ndx, flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}
		first = flist->files[first_hlink_ndx];
		file_length = F_LENGTH(first);
		modtime = first->modtime;
		mode = first->mode;
		if (preserve_uid)
			uid = F_UID(first);
		if (preserve_gid)
			gid = F_GID(first);
		if ((preserve_devices && IS_DEVICE(mode))
		 || (preserve_specials && IS_SPECIAL(mode))) {
			uint32 *devp = F_RDEV_P(first);
			rdev = MAKEDEV(DEV_MAJOR(devp), DEV_MINOR(devp));
		}
		if (preserve_links && S_ISLNK(mode))
			linkname_len = strlen(F_SYMLINK(first)) + 1;
		else
			linkname_len = 0;
		goto create_object;
	}
#endif

	file_length = read_longint(f);
	if (!(flags & XMIT_SAME_TIME))
		modtime = (time_t)read_int(f);
	if (!(flags & XMIT_SAME_MODE))
		mode = from_wire_mode(read_int(f));

	if (chmod_modes && !S_ISLNK(mode))
		mode = tweak_mode(mode, chmod_modes);

	if (preserve_uid && !(flags & XMIT_SAME_UID)) {
		uid = (uid_t)read_int(f);
		if (flags & XMIT_USER_NAME_FOLLOWS)
			uid = recv_user_name(f, uid);
		else if (incremental && am_root && !numeric_ids)
			uid = match_uid(uid);
	}
	if (preserve_gid && !(flags & XMIT_SAME_GID)) {
		gid = (gid_t)read_int(f);
		if (flags & XMIT_GROUP_NAME_FOLLOWS)
			gid = recv_group_name(f, gid);
		else if (incremental && (!am_root || !numeric_ids))
			gid = match_gid(gid);
	}

	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		if (protocol_version < 28) {
			if (!(flags & XMIT_SAME_RDEV_pre28))
				rdev = (dev_t)read_int(f);
		} else {
			uint32 rdev_minor;
			if (!(flags & XMIT_SAME_RDEV_MAJOR))
				rdev_major = read_int(f);
			if (flags & XMIT_RDEV_MINOR_IS_SMALL)
				rdev_minor = read_byte(f);
			else
				rdev_minor = read_int(f);
			rdev = MAKEDEV(rdev_major, rdev_minor);
		}
		extra_len += 2 * EXTRA_LEN;
		file_length = 0;
	} else if (protocol_version < 28)
		rdev = MAKEDEV(0, 0);

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(mode)) {
		linkname_len = read_int(f) + 1; /* count the '\0' */
		if (linkname_len <= 0 || linkname_len > MAXPATHLEN) {
			rprintf(FERROR, "overflow: linkname_len=%d\n",
				linkname_len - 1);
			overflow_exit("recv_file_entry");
		}
	}
	else
#endif
		linkname_len = 0;

#ifdef SUPPORT_HARD_LINKS
  create_object:
	if (preserve_hard_links) {
		if (protocol_version < 28 && S_ISREG(mode))
			flags |= XMIT_HLINKED;
		if (flags & XMIT_HLINKED)
			extra_len += EXTRA_LEN;
	}
#endif

	if (always_checksum && S_ISREG(mode))
		extra_len += SUM_EXTRA_CNT * EXTRA_LEN;

	if (file_length > 0xFFFFFFFFu && S_ISREG(mode))
		extra_len += EXTRA_LEN;

#if EXTRA_ROUNDING > 0
	if (extra_len & (EXTRA_ROUNDING * EXTRA_LEN))
		extra_len = (extra_len | (EXTRA_ROUNDING * EXTRA_LEN)) + EXTRA_LEN;
#endif

	if (incremental && S_ISDIR(mode)) {
		if (one_file_system) {
			/* Room to save the dir's device for -x */
			extra_len += 2 * EXTRA_LEN;
		}
		flist = dir_flist;
	}

	alloc_len = FILE_STRUCT_LEN + extra_len + basename_len
		  + linkname_len;
	bp = pool_alloc(flist->file_pool, alloc_len, "recv_file_entry");

	memset(bp, 0, extra_len + FILE_STRUCT_LEN);
	bp += extra_len;
	file = (struct file_struct *)bp;
	bp += FILE_STRUCT_LEN;

	memcpy(bp, basename, basename_len);
	bp += basename_len + linkname_len; /* skip space for symlink too */

#ifdef SUPPORT_HARD_LINKS
	if (flags & XMIT_HLINKED)
		file->flags |= FLAG_HLINKED;
#endif
	file->modtime = modtime;
	file->len32 = (uint32)file_length;
	if (file_length > 0xFFFFFFFFu && S_ISREG(mode)) {
		file->flags |= FLAG_LENGTH64;
		OPT_EXTRA(file, 0)->unum = (uint32)(file_length >> 32);
	}
	file->mode = mode;
	if (preserve_uid)
		F_UID(file) = uid;
	if (preserve_gid)
		F_GID(file) = gid;

	if (basename != thisname) {
		file->dirname = lastdir;
		F_DEPTH(file) = lastdir_depth + 1;
	} else
		F_DEPTH(file) = 1;

	if (S_ISDIR(mode)) {
		if (basename_len == 1+1 && *basename == '.') /* +1 for '\0' */
			F_DEPTH(file)--;
		if (flags & XMIT_TOP_DIR) {
			in_del_hier = recurse;
			del_hier_name_len = F_DEPTH(file) == 0 ? 0 : l1 + l2;
			if (relative_paths && del_hier_name_len > 2
			    && lastname[del_hier_name_len-1] == '.'
			    && lastname[del_hier_name_len-2] == '/')
				del_hier_name_len -= 2;
			file->flags |= FLAG_TOP_DIR | FLAG_XFER_DIR;
		} else if (in_del_hier) {
			if (!relative_paths || !del_hier_name_len
			 || (l1 >= del_hier_name_len
			  && lastname[del_hier_name_len] == '/'))
				file->flags |= FLAG_XFER_DIR;
			else
				in_del_hier = 0;
		}
	}

	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		uint32 *devp = F_RDEV_P(file);
		DEV_MAJOR(devp) = major(rdev);
		DEV_MINOR(devp) = minor(rdev);
	}

#ifdef SUPPORT_LINKS
	if (linkname_len) {
		bp = (char*)file->basename + basename_len;
		if (first_hlink_ndx >= 0) {
			struct file_struct *first = flist->files[first_hlink_ndx];
			memcpy(bp, F_SYMLINK(first), linkname_len);
		} else
			read_sbuf(f, bp, linkname_len - 1);
		if (sanitize_paths)
			sanitize_path(bp, bp, "", lastdir_depth, NULL);
	}
#endif

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && flags & XMIT_HLINKED) {
		if (protocol_version >= 30) {
			F_HL_GNUM(file) = flags & XMIT_HLINK_FIRST
					? flist->count : first_hlink_ndx;
		} else {
			static int32 cnt = 0;
			struct idev_node *np;
			int64 ino;
			int32 ndx;
			if (protocol_version < 26) {
				dev = read_int(f);
				ino = read_int(f);
			} else {
				if (!(flags & XMIT_SAME_DEV_pre30))
					dev = read_longint(f);
				ino = read_longint(f);
			}
			np = idev_node(dev, ino);
			ndx = (int32)np->data - 1;
			if (ndx < 0) {
				ndx = cnt++;
				np->data = (void*)cnt;
			}
			F_HL_GNUM(file) = ndx;
		}
	}
#endif

	if (always_checksum && (S_ISREG(mode) || protocol_version < 28)) {
		if (S_ISREG(mode))
			bp = (char*)F_SUM(file);
		else {
			/* Prior to 28, we get a useless set of nulls. */
			bp = tmp_sum;
		}
		if (first_hlink_ndx >= 0) {
			struct file_struct *first = flist->files[first_hlink_ndx];
			memcpy(bp, F_SUM(first), checksum_len);
		} else
			read_buf(f, bp, checksum_len);
	}

	return file;
}

/**
 * Create a file_struct for a named file by reading its stat()
 * information and performing extensive checks against global
 * options.
 *
 * @return the new file, or NULL if there was an error or this file
 * should be excluded.
 *
 * @todo There is a small optimization opportunity here to avoid
 * stat()ing the file in some circumstances, which has a certain cost.
 * We are called immediately after doing readdir(), and so we may
 * already know the d_type of the file.  We could for example avoid
 * statting directories if we're not recursing, but this is not a very
 * important case.  Some systems may not have d_type.
 **/
struct file_struct *make_file(const char *fname, struct file_list *flist,
			      STRUCT_STAT *stp, int flags, int filter_level)
{
	static char *lastdir;
	static int lastdir_len = -1;
	struct file_struct *file;
	STRUCT_STAT st;
	char thisname[MAXPATHLEN];
	char linkname[MAXPATHLEN];
	int alloc_len, basename_len, linkname_len;
	int extra_len = file_extra_cnt * EXTRA_LEN;
	const char *basename;
	char *bp;

	if (strlcpy(thisname, fname, sizeof thisname)
	    >= sizeof thisname - flist_dir_len) {
		rprintf(FINFO, "skipping overly long name: %s\n", fname);
		return NULL;
	}
	clean_fname(thisname, 0);
	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0, NULL);

	if (stp && S_ISDIR(stp->st_mode)) {
		st = *stp; /* Needed for "symlink/." with --relative. */
		*linkname = '\0'; /* make IBM code checker happy */
	} else if (readlink_stat(thisname, &st, linkname) != 0) {
		int save_errno = errno;
		/* See if file is excluded before reporting an error. */
		if (filter_level != NO_FILTERS
		 && (is_excluded(thisname, 0, filter_level)
		  || is_excluded(thisname, 1, filter_level))) {
			if (ignore_perishable && save_errno != ENOENT)
				non_perishable_cnt++;
			return NULL;
		}
		if (save_errno == ENOENT) {
#ifdef SUPPORT_LINKS
			/* Avoid "vanished" error if symlink points nowhere. */
			if (copy_links && do_lstat(thisname, &st) == 0
			    && S_ISLNK(st.st_mode)) {
				io_error |= IOERR_GENERAL;
				rprintf(FERROR, "symlink has no referent: %s\n",
					full_fname(thisname));
			} else
#endif
			{
				enum logcode c = am_daemon && protocol_version < 28
				    ? FERROR : FINFO;
				io_error |= IOERR_VANISHED;
				rprintf(c, "file has vanished: %s\n",
					full_fname(thisname));
			}
		} else {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR, save_errno, "readlink %s failed",
				full_fname(thisname));
		}
		return NULL;
	}

	/* backup.c calls us with filter_level set to NO_FILTERS. */
	if (filter_level == NO_FILTERS)
		goto skip_filters;

	if (S_ISDIR(st.st_mode) && !xfer_dirs) {
		rprintf(FINFO, "skipping directory %s\n", thisname);
		return NULL;
	}

	/* -x only affects directories because we need to avoid recursing
	 * into a mount-point directory, not to avoid copying a symlinked
	 * file if -L (or similar) was specified. */
	if (one_file_system && st.st_dev != filesystem_dev
	 && S_ISDIR(st.st_mode)) {
		if (one_file_system > 1) {
			if (verbose > 2) {
				rprintf(FINFO, "skipping mount-point dir %s\n",
					thisname);
			}
			return NULL;
		}
		flags |= FLAG_MOUNT_DIR;
	}

	if (is_excluded(thisname, S_ISDIR(st.st_mode) != 0, filter_level)) {
		if (ignore_perishable)
			non_perishable_cnt++;
		return NULL;
	}

	if (lp_ignore_nonreadable(module_id)) {
#ifdef SUPPORT_LINKS
		if (!S_ISLNK(st.st_mode))
#endif
			if (access(thisname, R_OK) != 0)
				return NULL;
	}

  skip_filters:

	/* Only divert a directory in the main transfer. */
	if (flist && flist->prev && S_ISDIR(st.st_mode)
	 && flags & FLAG_DIVERT_DIRS) {
		flist = dir_flist;
		/* Room for parent/sibling/next-child info. */
		extra_len += 3 * EXTRA_LEN;
	}

	if (verbose > 2) {
		rprintf(FINFO, "[%s] make_file(%s,*,%d)\n",
			who_am_i(), thisname, filter_level);
	}

	if ((basename = strrchr(thisname, '/')) != NULL) {
		int len = basename++ - thisname;
		if (len != lastdir_len || memcmp(thisname, lastdir, len) != 0) {
			lastdir = new_array(char, len + 1);
			memcpy(lastdir, thisname, len);
			lastdir[len] = '\0';
			lastdir_len = len;
		}
	} else
		basename = thisname;
	basename_len = strlen(basename) + 1; /* count the '\0' */

#ifdef SUPPORT_LINKS
	linkname_len = S_ISLNK(st.st_mode) ? strlen(linkname) + 1 : 0;
#else
	linkname_len = 0;
#endif

	if (st.st_size > 0xFFFFFFFFu && S_ISREG(st.st_mode))
		extra_len += EXTRA_LEN;

#if EXTRA_ROUNDING > 0
	if (extra_len & (EXTRA_ROUNDING * EXTRA_LEN))
		extra_len = (extra_len | (EXTRA_ROUNDING * EXTRA_LEN)) + EXTRA_LEN;
#endif

	alloc_len = FILE_STRUCT_LEN + extra_len + basename_len
		  + linkname_len;
	if (flist)
		bp = pool_alloc(flist->file_pool, alloc_len, "make_file");
	else {
		if (!(bp = new_array(char, alloc_len)))
			out_of_memory("make_file");
	}

	memset(bp, 0, extra_len + FILE_STRUCT_LEN);
	bp += extra_len;
	file = (struct file_struct *)bp;
	bp += FILE_STRUCT_LEN;

	memcpy(bp, basename, basename_len);
	bp += basename_len + linkname_len; /* skip space for symlink too */

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && flist && flist->prev) {
		if (protocol_version >= 28
		 ? (!S_ISDIR(st.st_mode) && st.st_nlink > 1)
		 : S_ISREG(st.st_mode)) {
			tmp_dev = st.st_dev;
			tmp_ino = st.st_ino;
		} else
			tmp_dev = 0;
	}
#endif

#ifdef HAVE_STRUCT_STAT_ST_RDEV
	if (IS_DEVICE(st.st_mode) || IS_SPECIAL(st.st_mode)) {
		tmp_rdev = st.st_rdev;
		st.st_size = 0;
	}
#endif

	file->flags = flags;
	file->modtime = st.st_mtime;
	file->len32 = (uint32)st.st_size;
	if (st.st_size > 0xFFFFFFFFu && S_ISREG(st.st_mode)) {
		file->flags |= FLAG_LENGTH64;
		OPT_EXTRA(file, 0)->unum = (uint32)(st.st_size >> 32);
	}
	file->mode = st.st_mode;
	if (preserve_uid)
		F_UID(file) = st.st_uid;
	if (preserve_gid)
		F_GID(file) = st.st_gid;

	if (basename != thisname)
		file->dirname = lastdir;

#ifdef SUPPORT_LINKS
	if (linkname_len) {
		bp = (char*)file->basename + basename_len;
		memcpy(bp, linkname, linkname_len);
	}
#endif

	if (always_checksum && am_sender && S_ISREG(st.st_mode))
		file_checksum(thisname, tmp_sum, st.st_size);

	F_ROOTDIR(file) = flist_dir;

	/* This code is only used by the receiver when it is building
	 * a list of files for a delete pass. */
	if (keep_dirlinks && linkname_len && flist) {
		STRUCT_STAT st2;
		int save_mode = file->mode;
		file->mode = S_IFDIR; /* Find a directory with our name. */
		if (flist_find(dir_flist, file) >= 0
		    && do_stat(thisname, &st2) == 0 && S_ISDIR(st2.st_mode)) {
			file->modtime = st2.st_mtime;
			file->len32 = 0;
			file->mode = st2.st_mode;
			if (preserve_uid)
				F_UID(file) = st2.st_uid;
			if (preserve_gid)
				F_GID(file) = st2.st_gid;
		} else
			file->mode = save_mode;
	}

	if (S_ISREG(st.st_mode) || S_ISLNK(st.st_mode))
		stats.total_size += st.st_size;

	if (basename_len == 0+1)
		return NULL;

	if (incremental && flist == dir_flist) {
		flist_expand(flist);
		flist->files[flist->count++] = file;
	}

	return file;
}

/* Only called for temporary file_struct entries created by make_file(). */
void unmake_file(struct file_struct *file)
{
	int extra_cnt = file_extra_cnt + LEN64_BUMP(file);
#if EXTRA_ROUNDING > 0
	if (extra_cnt & EXTRA_ROUNDING)
		extra_cnt = (extra_cnt | EXTRA_ROUNDING) + 1;
#endif
	free(REQ_EXTRA(file, extra_cnt));
}

static struct file_struct *send_file_name(struct file_list *flist,
					  char *fname, STRUCT_STAT *stp,
					  int flags, int filter_flags)
{
	struct file_struct *file;

	file = make_file(fname, flist, stp, flags, filter_flags);
	if (!file)
		return NULL;

	if (chmod_modes && !S_ISLNK(file->mode))
		file->mode = tweak_mode(file->mode, chmod_modes);

	maybe_emit_filelist_progress(flist->count + flist_count_offset);

	flist_expand(flist);
	flist->files[flist->count++] = file;
	return file;
}

static void send_if_directory(int f, struct file_list *flist,
			      struct file_struct *file,
			      char *fbuf, unsigned int ol,
			      int flags)
{
	char is_dot_dir = fbuf[ol-1] == '.' && (ol == 1 || fbuf[ol-2] == '/');

	if (S_ISDIR(file->mode)
	    && !(file->flags & FLAG_MOUNT_DIR) && f_name(file, fbuf)) {
		void *save_filters;
		unsigned int len = strlen(fbuf);
		if (len > 1 && fbuf[len-1] == '/')
			fbuf[--len] = '\0';
		if (len >= MAXPATHLEN - 1) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR, "skipping long-named directory: %s\n",
				full_fname(fbuf));
			return;
		}
		save_filters = push_local_filters(fbuf, len);
		send_directory(f, flist, -1, fbuf, len, flags);
		pop_local_filters(save_filters);
		fbuf[ol] = '\0';
		if (is_dot_dir)
			fbuf[ol-1] = '.';
	}
}

static int file_compare(struct file_struct **file1, struct file_struct **file2)
{
	return f_name_cmp(*file1, *file2);
}

/* We take an entire set of sibling dirs from dir_flist (start <= ndx <= end),
 * sort them by name, and link them into the tree, setting the appropriate
 * parent/child/sibling pointers. */
static void add_dirs_to_tree(int parent_ndx, int start, int end)
{
	int i;
	int32 *dp = NULL;
	int32 *parent_dp = parent_ndx < 0 ? NULL
			 : F_DIRNODE_P(dir_flist->files[parent_ndx]);

	qsort(dir_flist->files + start, end - start + 1,
	      sizeof dir_flist->files[0], (int (*)())file_compare);

	for (i = start; i <= end; i++) {
		struct file_struct *file = dir_flist->files[i];
		if (!(file->flags & FLAG_XFER_DIR)
		 || file->flags & FLAG_MOUNT_DIR)
			continue;
		if (dp)
			DIR_NEXT_SIBLING(dp) = i;
		else if (parent_dp)
			DIR_FIRST_CHILD(parent_dp) = i;
		else
			send_dir_ndx = i;
		dp = F_DIRNODE_P(file);
		DIR_PARENT(dp) = parent_ndx;
		DIR_FIRST_CHILD(dp) = -1;
	}
	if (dp)
		DIR_NEXT_SIBLING(dp) = -1;
}

/* This function is normally called by the sender, but the receiving side also
 * calls it from get_dirlist() with f set to -1 so that we just construct the
 * file list in memory without sending it over the wire.  Also, get_dirlist()
 * might call this with f set to -2, which also indicates that local filter
 * rules should be ignored. */
static void send_directory(int f, struct file_list *flist, int parent_ndx,
			   char *fbuf, int len, int flags)
{
	struct dirent *di;
	unsigned remainder;
	char *p;
	DIR *d;
	int divert_dirs = (flags & FLAG_DIVERT_DIRS) != 0;
	int start = divert_dirs ? dir_flist->count : flist->count;
	int filter_flags = f == -2 ? SERVER_FILTERS : ALL_FILTERS;
	struct file_struct *file;

	assert(flist != NULL);

	if (!(d = opendir(fbuf))) {
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR, errno, "opendir %s failed", full_fname(fbuf));
		return;
	}

	p = fbuf + len;
	if (len != 1 || *fbuf != '/')
		*p++ = '/';
	*p = '\0';
	remainder = MAXPATHLEN - (p - fbuf);

	for (errno = 0, di = readdir(d); di; errno = 0, di = readdir(d)) {
		char *dname = d_name(di);
		if (dname[0] == '.' && (dname[1] == '\0'
		    || (dname[1] == '.' && dname[2] == '\0')))
			continue;
		if (strlcpy(p, dname, remainder) >= remainder) {
			io_error |= IOERR_GENERAL;
			rprintf(FINFO,
				"cannot send long-named file %s\n",
				full_fname(fbuf));
			continue;
		}

		file = send_file_name(flist, fbuf, NULL, flags, filter_flags);
		if (file && f >= 0)
			send_file_entry(file, f, flist->count - 1);
	}

	fbuf[len] = '\0';

	if (errno) {
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR, errno, "readdir(%s)", full_fname(fbuf));
	}

	closedir(d);

	if (f < 0)
		return;

	if (divert_dirs)
		add_dirs_to_tree(parent_ndx, start, dir_flist->count - 1);
	else if (recurse) {
		int i, end = flist->count - 1;
		/* send_if_directory() bumps flist->count, so use "end". */
		for (i = start; i <= end; i++)
			send_if_directory(f, flist, flist->files[i], fbuf, len, flags);
	}
}

void send_extra_file_list(int f, int at_least)
{
	char fbuf[MAXPATHLEN];
	struct file_list *flist;
	int64 start_write;
	int past_and_present, save_io_error = io_error;

	if (send_dir_ndx < 0)
		return;

	/* Keep sending data until we have the requested number of
	 * files in the upcoming file-lists. */
	past_and_present = cur_flist->ndx_start - first_flist->ndx_start
			 + cur_flist->count;
	while (file_total - past_and_present < at_least) {
		start_write = stats.total_written;
		struct file_struct *file = dir_flist->files[send_dir_ndx];
		int32 *dp;
		int dlen;

		f_name(file, fbuf);
		dlen = strlen(fbuf);

		if (F_ROOTDIR(file) != flist_dir) {
			if (!push_flist_dir(F_ROOTDIR(file), -1))
				exit_cleanup(RERR_FILESELECT);
		}

		flist = flist_new(0, "send_extra_file_list");

		write_int(f, NDX_FLIST_OFFSET - send_dir_ndx);
		change_local_filter_dir(fbuf, dlen, send_dir_depth);
		send_directory(f, flist, send_dir_ndx, fbuf, dlen, FLAG_DIVERT_DIRS | FLAG_XFER_DIR);
		write_byte(f, 0);

		clean_flist(flist, 0, 0);
		file_total += flist->count;
		stats.flist_size += stats.total_written - start_write;
		stats.num_files += flist->count;
		if (verbose > 3)
			output_flist(flist);

		dp = F_DIRNODE_P(file);
		if (DIR_FIRST_CHILD(dp) >= 0) {
			send_dir_ndx = DIR_FIRST_CHILD(dp);
			send_dir_depth++;
		} else {
			while (DIR_NEXT_SIBLING(dp) < 0) {
				if ((send_dir_ndx = DIR_PARENT(dp)) < 0) {
					write_int(f, NDX_FLIST_EOF);
					flist_eof = 1;
					change_local_filter_dir(NULL, 0, 0);
					goto finish;
				}
				send_dir_depth--;
				file = dir_flist->files[send_dir_ndx];
				dp = F_DIRNODE_P(file);
			}
			send_dir_ndx = DIR_NEXT_SIBLING(dp);
		}
	}

  finish:
	if (io_error != save_io_error && !ignore_errors)
		send_msg_int(MSG_IO_ERROR, io_error);
}

struct file_list *send_file_list(int f, int argc, char *argv[])
{
	int len;
	STRUCT_STAT st;
	char *p, *dir;
	char lastpath[MAXPATHLEN] = "";
	struct file_list *flist;
	struct timeval start_tv, end_tv;
	struct file_struct *file;
	int64 start_write;
	int use_ff_fd = 0;
	int flags, disable_buffering;

	rprintf(FLOG, "building file list\n");
	if (show_filelist_p())
		start_filelist_progress("building file list");

	start_write = stats.total_written;
	gettimeofday(&start_tv, NULL);

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version >= 30 && !cur_flist)
		init_hard_links();
#endif

	flist = cur_flist = flist_new(0, "send_file_list");
	if (incremental) {
		dir_flist = flist_new(FLIST_TEMP, "send_file_list");
		flags = FLAG_DIVERT_DIRS;
	} else {
		dir_flist = cur_flist;
		flags = 0;
	}

	disable_buffering = io_start_buffering_out(f);
	if (filesfrom_fd >= 0) {
		if (argv[0] && !push_dir(argv[0], 0)) {
			rsyserr(FERROR, errno, "push_dir %s failed",
				full_fname(argv[0]));
			exit_cleanup(RERR_FILESELECT);
		}
		use_ff_fd = 1;
	}

	while (1) {
		char fbuf[MAXPATHLEN];
		char *fn;
		int is_dot_dir;

		if (use_ff_fd) {
			if (read_filesfrom_line(filesfrom_fd, fbuf) == 0)
				break;
			sanitize_path(fbuf, fbuf, "", 0, NULL);
		} else {
			if (argc-- == 0)
				break;
			strlcpy(fbuf, *argv++, MAXPATHLEN);
			if (sanitize_paths)
				sanitize_path(fbuf, fbuf, "", 0, NULL);
		}

		len = strlen(fbuf);
		if (relative_paths) {
			/* We clean up fbuf below. */
			is_dot_dir = 0;
		} else if (!len || fbuf[len - 1] == '/') {
			if (len == 2 && fbuf[0] == '.') {
				/* Turn "./" into just "." rather than "./." */
				fbuf[1] = '\0';
			} else {
				if (len + 1 >= MAXPATHLEN)
					overflow_exit("send_file_list");
				fbuf[len++] = '.';
				fbuf[len] = '\0';
			}
			is_dot_dir = 1;
		} else if (len > 1 && fbuf[len-1] == '.' && fbuf[len-2] == '.'
		    && (len == 2 || fbuf[len-3] == '/')) {
			if (len + 2 >= MAXPATHLEN)
				overflow_exit("send_file_list");
			fbuf[len++] = '/';
			fbuf[len++] = '.';
			fbuf[len] = '\0';
			is_dot_dir = 1;
		} else {
			is_dot_dir = fbuf[len-1] == '.'
				   && (len == 1 || fbuf[len-2] == '/');
		}

		if (link_stat(fbuf, &st, copy_dirlinks) != 0) {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR, errno, "link_stat %s failed",
				full_fname(fbuf));
			continue;
		}

		if (S_ISDIR(st.st_mode) && !xfer_dirs) {
			rprintf(FINFO, "skipping directory %s\n", fbuf);
			continue;
		}

		dir = NULL;

		if (!relative_paths) {
			p = strrchr(fbuf, '/');
			if (p) {
				*p = '\0';
				if (p == fbuf)
					dir = "/";
				else
					dir = fbuf;
				len -= p - fbuf + 1;
				fn = p + 1;
			} else
				fn = fbuf;
		} else {
			if ((p = strstr(fbuf, "/./")) != NULL) {
				*p = '\0';
				if (p == fbuf)
					dir = "/";
				else
					dir = fbuf;
				len -= p - fbuf + 3;
				fn = p + 3;
			} else
				fn = fbuf;
			/* Get rid of trailing "/" and "/.". */
			while (len) {
				if (fn[len - 1] == '/') {
					is_dot_dir = 1;
					if (!--len && !dir) {
						len++;
						break;
					}
				}
				else if (len >= 2 && fn[len - 1] == '.'
						  && fn[len - 2] == '/') {
					is_dot_dir = 1;
					if (!(len -= 2) && !dir) {
						len++;
						break;
					}
				} else
					break;
			}
			if (len == 1 && fn[0] == '/')
				fn[len++] = '.';
			fn[len] = '\0';
			/* Reject a ".." dir in the active part of the path. */
			for (p = fn; (p = strstr(p, "..")) != NULL; p += 2) {
				if ((p[2] == '/' || p[2] == '\0')
				 && (p == fn || p[-1] == '/')) {
					rprintf(FERROR,
					    "found \"..\" dir in relative path: %s\n",
					    fbuf);
					exit_cleanup(RERR_SYNTAX);
				}
			}
		}

		if (!*fn) {
			len = 1;
			fn = ".";
		}

		if (dir && *dir) {
			static const char *lastdir;
			static int lastdir_len = -1;
			int len = strlen(dir);

			if (len != lastdir_len || memcmp(lastdir, dir, len) != 0) {
				if (!push_flist_dir(strdup(dir), len))
					goto push_error;
				lastdir = flist_dir;
				lastdir_len = flist_dir_len;
			} else if (!push_flist_dir(lastdir, lastdir_len)) {
			  push_error:
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR, errno, "push_dir %s failed",
					full_fname(dir));
				continue;
			}
		}

		if (fn != fbuf)
			memmove(fbuf, fn, len + 1);

		if (implied_dirs && (p=strrchr(fbuf,'/')) && p != fbuf) {
			/* Send the implied directories at the start of the
			 * source spec, so we get their permissions right. */
			char *lp = lastpath, *slash = fbuf;
			*p = '\0';
			/* Skip any initial directories in our path that we
			 * have in common with lastpath. */
			for (fn = fbuf; *fn && *lp == *fn; lp++, fn++) {
				if (*fn == '/')
					slash = fn;
			}
			*p = '/';
			if (fn != p || (*lp && *lp != '/')) {
				int save_copy_links = copy_links;
				int save_xfer_dirs = xfer_dirs;
				int dir_flags = incremental ? FLAG_DIVERT_DIRS : 0;
				copy_links |= copy_unsafe_links;
				xfer_dirs = 1;
				while ((slash = strchr(slash+1, '/')) != 0) {
					*slash = '\0';
					file = send_file_name(flist, fbuf, NULL, dir_flags, ALL_FILTERS);
					if (file)
						send_file_entry(file, f, flist->count - 1);
					*slash = '/';
				}
				copy_links = save_copy_links;
				xfer_dirs = save_xfer_dirs;
				*p = '\0';
				strlcpy(lastpath, fbuf, sizeof lastpath);
				*p = '/';
			}
		}

		if (one_file_system)
			filesystem_dev = st.st_dev;

		if (recurse || (xfer_dirs && is_dot_dir)) {
			int top_flags = FLAG_TOP_DIR | FLAG_XFER_DIR
				      | (is_dot_dir ? 0 : flags)
				      | (incremental ? FLAG_DIVERT_DIRS : 0);
			file = send_file_name(flist, fbuf, &st, top_flags, ALL_FILTERS);
			if (file) {
				send_file_entry(file, f, flist->count - 1);
				if (!incremental)
					send_if_directory(f, flist, file, fbuf, len, flags);
			}
		} else {
			file = send_file_name(flist, fbuf, &st, flags, ALL_FILTERS);
			if (file)
				send_file_entry(file, f, flist->count - 1);
		}
	}

	gettimeofday(&end_tv, NULL);
	stats.flist_buildtime = (int64)(end_tv.tv_sec - start_tv.tv_sec) * 1000
			      + (end_tv.tv_usec - start_tv.tv_usec) / 1000;
	if (stats.flist_buildtime == 0)
		stats.flist_buildtime = 1;
	start_tv = end_tv;

	write_byte(f, 0); /* Indicate end of file list */

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version >= 30 && !incremental)
		idev_destroy();
#endif

	if (show_filelist_p())
		finish_filelist_progress(flist);

	gettimeofday(&end_tv, NULL);
	stats.flist_xfertime = (int64)(end_tv.tv_sec - start_tv.tv_sec) * 1000
			     + (end_tv.tv_usec - start_tv.tv_usec) / 1000;

	/* Sort the list without removing any duplicates in non-incremental
	 * mode.  This allows the receiving side to ask for whatever name it
	 * kept.  For incremental mode, the sender also removes duplicates
	 * in this initial file-list so that it avoids re-sending duplicated
	 * directories. */
	clean_flist(flist, 0, incremental);
	file_total += flist->count;

	if (!numeric_ids && !incremental)
		send_uid_list(f);

	/* send the io_error flag */
	if (protocol_version < 30)
		write_int(f, ignore_errors ? 0 : io_error);
	else if (io_error && !ignore_errors)
		send_msg_int(MSG_IO_ERROR, io_error);

	if (disable_buffering)
		io_end_buffering_out();

	stats.flist_size = stats.total_written - start_write;
	stats.num_files = flist->count;

	if (verbose > 3)
		output_flist(flist);

	if (verbose > 2)
		rprintf(FINFO, "send_file_list done\n");

	if (incremental) {
		add_dirs_to_tree(-1, 0, dir_flist->count - 1);
		if (file_total == 1) {
			/* If we're creating incremental file-lists and there
			 * was just 1 item in the first file-list, send 1 more
			 * file-list to check if this is a 1-file xfer. */
			if (send_dir_ndx < 0)
				write_int(f, NDX_DONE);
			else
				send_extra_file_list(f, 1);
		}
	}

	return flist;
}

struct file_list *recv_file_list(int f)
{
	struct file_list *flist;
	int dstart, flags;
	int64 start_read;

	if (f >= 0 && !incremental)
		rprintf(FLOG, "receiving file list\n");
	if (show_filelist_p())
		start_filelist_progress("receiving file list");

	start_read = stats.total_read;

	flist = flist_new(0, "recv_file_list");

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version < 30)
		init_hard_links();
#endif

	if (incremental) {
		if (flist->ndx_start == 0)
			dir_flist = flist_new(FLIST_TEMP, "recv_file_list");
		dstart = dir_flist->count;
	} else {
		dir_flist = flist;
		dstart = 0;
	}

	while ((flags = read_byte(f)) != 0) {
		struct file_struct *file;

		flist_expand(flist);

		if (protocol_version >= 28 && (flags & XMIT_EXTENDED_FLAGS))
			flags |= read_byte(f) << 8;
		file = recv_file_entry(flist, flags, f);

		if (S_ISREG(file->mode) || S_ISLNK(file->mode))
			stats.total_size += F_LENGTH(file);

		if (incremental && S_ISDIR(file->mode)) {
			flist_expand(dir_flist);
			dir_flist->files[dir_flist->count++] = file;
		}

		flist->files[flist->count++] = file;

		maybe_emit_filelist_progress(flist->count);

		if (verbose > 2) {
			rprintf(FINFO, "recv_file_name(%s)\n",
				f_name(file, NULL));
		}
	}
	file_total += flist->count;

	if (verbose > 2)
		rprintf(FINFO, "received %d names\n", flist->count);

	if (show_filelist_p())
		finish_filelist_progress(flist);

	clean_flist(flist, relative_paths, 1);

	if (incremental) {
		qsort(dir_flist->files + dstart, dir_flist->count - dstart,
		      sizeof dir_flist->files[0], (int (*)())file_compare);
	} else if (f >= 0)
		recv_uid_list(f, flist);

	if (protocol_version < 30) {
		/* Recv the io_error flag */
		if (ignore_errors)
			read_int(f);
		else
			io_error |= read_int(f);
	}

	if (verbose > 3)
		output_flist(flist);

	if (list_only) {
		int i;
		for (i = 0; i < flist->count; i++)
			list_file_entry(flist->files[i]);
	}

	if (verbose > 2)
		rprintf(FINFO, "recv_file_list done\n");

	stats.flist_size += stats.total_read - start_read;
	stats.num_files += flist->count;

	return flist;
}

/* This is only used once by the receiver if the very first file-list
 * has exactly one item in it. */
void recv_additional_file_list(int f)
{
	struct file_list *flist;
	int ndx = read_int(f);
	if (ndx == NDX_DONE) {
		flist_eof = 1;
		change_local_filter_dir(NULL, 0, 0);
	} else {
		ndx = NDX_FLIST_OFFSET - ndx;
		if (ndx < 0 || ndx >= dir_flist->count) {
			ndx = NDX_FLIST_OFFSET - ndx;
			rprintf(FERROR,
				"Invalid dir index: %d (%d - %d)\n",
				ndx, NDX_FLIST_OFFSET,
				NDX_FLIST_OFFSET - dir_flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}
		flist = recv_file_list(f);
		flist->parent_ndx = ndx;
	}
}

/* Search for an identically-named item in the file list.  Note that the
 * items must agree in their directory-ness, or no match is returned. */
int flist_find(struct file_list *flist, struct file_struct *f)
{
	int low = flist->low, high = flist->high;
	int diff, mid, mid_up;

	while (low <= high) {
		mid = (low + high) / 2;
		if (F_IS_ACTIVE(flist->files[mid]))
			mid_up = mid;
		else {
			/* Scan for the next non-empty entry using the cached
			 * distance values.  If the value isn't fully up-to-
			 * date, update it. */
			mid_up = mid + F_DEPTH(flist->files[mid]);
			if (!F_IS_ACTIVE(flist->files[mid_up])) {
				do {
				    mid_up += F_DEPTH(flist->files[mid_up]);
				} while (!F_IS_ACTIVE(flist->files[mid_up]));
				F_DEPTH(flist->files[mid]) = mid_up - mid;
			}
			if (mid_up > high) {
				/* If there's nothing left above us, set high to
				 * a non-empty entry below us and continue. */
				high = mid - (int)flist->files[mid]->len32;
				if (!F_IS_ACTIVE(flist->files[high])) {
					do {
					    high -= (int)flist->files[high]->len32;
					} while (!F_IS_ACTIVE(flist->files[high]));
					flist->files[mid]->len32 = mid - high;
				}
				continue;
			}
		}
		diff = f_name_cmp(flist->files[mid_up], f);
		if (diff == 0) {
			if (protocol_version < 29
			    && S_ISDIR(flist->files[mid_up]->mode)
			    != S_ISDIR(f->mode))
				return -1;
			return mid_up;
		}
		if (diff < 0)
			low = mid_up + 1;
		else
			high = mid - 1;
	}
	return -1;
}

/*
 * Free up any resources a file_struct has allocated
 * and clear the file.
 */
void clear_file(struct file_struct *file)
{
	/* The +1 zeros out the first char of the basename. */
	memset(file, 0, FILE_STRUCT_LEN + 1);
	/* In an empty entry, F_DEPTH() is an offset to the next non-empty
	 * entry.  Likewise for len32 in the opposite direction.  We assume
	 * that we're alone for now since flist_find() will adjust the counts
	 * it runs into that aren't up-to-date. */
	file->len32 = F_DEPTH(file) = 1;
}

/* Allocate a new file list. */
struct file_list *flist_new(int flags, char *msg)
{
	struct file_list *flist;

	flist = new(struct file_list);
	if (!flist)
		out_of_memory(msg);

	memset(flist, 0, sizeof flist[0]);

	if (!(flags & FLIST_TEMP)) {
		if (first_flist) {
			flist->ndx_start = first_flist->prev->ndx_start
					 + first_flist->prev->count;
		}
		/* This is a doubly linked list with prev looping back to
		 * the end of the list, but the last next pointer is NULL. */
		if (!first_flist)
			first_flist = cur_flist = flist->prev = flist;
		else {
			flist->prev = first_flist->prev;
			flist->prev->next = first_flist->prev = flist;
		}
		flist_cnt++;
	}

	if (!(flist->file_pool = pool_create(FILE_EXTENT, 0, out_of_memory, POOL_INTERN)))
		out_of_memory(msg);

	return flist;
}

/* Free up all elements in a flist. */
void flist_free(struct file_list *flist)
{
	if (!flist->prev)
		; /* Was FLIST_TEMP dir-list. */
	else if (flist == flist->prev) {
		first_flist = cur_flist = NULL;
		file_total = 0;
		flist_cnt = 0;
	} else {
		if (flist == cur_flist)
			cur_flist = flist->next;
		if (flist == first_flist)
			first_flist = first_flist->next;
		else {
			flist->prev->next = flist->next;
			if (!flist->next)
				flist->next = first_flist;
		}
		flist->next->prev = flist->prev;
		file_total -= flist->count;
		flist_cnt--;
	}

	pool_destroy(flist->file_pool);
	free(flist->files);
	free(flist);
}

/*
 * This routine ensures we don't have any duplicate names in our file list.
 * duplicate names can cause corruption because of the pipelining
 */
static void clean_flist(struct file_list *flist, int strip_root, int no_dups)
{
	char fbuf[MAXPATHLEN];
	int i, prev_i = 0;

	if (!flist)
		return;
	if (flist->count == 0) {
		flist->high = -1;
		return;
	}

	qsort(flist->files, flist->count,
	    sizeof flist->files[0], (int (*)())file_compare);

	for (i = no_dups? 0 : flist->count; i < flist->count; i++) {
		if (F_IS_ACTIVE(flist->files[i])) {
			prev_i = i;
			break;
		}
	}
	flist->low = prev_i;
	while (++i < flist->count) {
		int j;
		struct file_struct *file = flist->files[i];

		if (!F_IS_ACTIVE(file))
			continue;
		if (f_name_cmp(file, flist->files[prev_i]) == 0)
			j = prev_i;
		else if (protocol_version >= 29 && S_ISDIR(file->mode)) {
			int save_mode = file->mode;
			/* Make sure that this directory doesn't duplicate a
			 * non-directory earlier in the list. */
			flist->high = prev_i;
			file->mode = S_IFREG;
			j = flist_find(flist, file);
			file->mode = save_mode;
		} else
			j = -1;
		if (j >= 0) {
			struct file_struct *fp = flist->files[j];
			int keep, drop;
			/* If one is a dir and the other is not, we want to
			 * keep the dir because it might have contents in the
			 * list. */
			if (S_ISDIR(file->mode) != S_ISDIR(fp->mode)) {
				if (S_ISDIR(file->mode))
					keep = i, drop = j;
				else
					keep = j, drop = i;
			} else if (protocol_version < 27)
				keep = j, drop = i;
			else
				keep = i, drop = j;
			if (verbose > 1 && !am_server) {
				rprintf(FINFO,
					"removing duplicate name %s from file list (%d)\n",
					f_name(file, fbuf), drop);
			}
			/* Make sure we don't lose track of a user-specified
			 * top directory. */
			flist->files[keep]->flags |= flist->files[drop]->flags
						   & (FLAG_TOP_DIR|FLAG_XFER_DIR);

			clear_file(flist->files[drop]);

			if (keep == i) {
				if (flist->low == drop) {
					for (j = drop + 1;
					     j < i && !F_IS_ACTIVE(flist->files[j]);
					     j++) {}
					flist->low = j;
				}
				prev_i = i;
			}
		} else
			prev_i = i;
	}
	flist->high = no_dups ? prev_i : flist->count - 1;

	if (strip_root) {
		/* We need to strip off the leading slashes for relative
		 * paths, but this must be done _after_ the sorting phase. */
		for (i = flist->low; i <= flist->high; i++) {
			struct file_struct *file = flist->files[i];

			if (!file->dirname)
				continue;
			while (*file->dirname == '/')
				file->dirname++;
			if (!*file->dirname)
				file->dirname = NULL;
		}
	}

	if (prune_empty_dirs && no_dups) {
		int j, prev_depth = 0;

		prev_i = 0; /* It's OK that this isn't really true. */

		for (i = flist->low; i <= flist->high; i++) {
			struct file_struct *fp, *file = flist->files[i];

			/* This temporarily abuses the F_DEPTH() value for a
			 * directory that is in a chain that might get pruned.
			 * We restore the old value if it gets a reprieve. */
			if (S_ISDIR(file->mode) && F_DEPTH(file)) {
				/* Dump empty dirs when coming back down. */
				for (j = prev_depth; j >= F_DEPTH(file); j--) {
					fp = flist->files[prev_i];
					if (F_DEPTH(fp) >= 0)
						break;
					prev_i = -F_DEPTH(fp)-1;
					clear_file(fp);
				}
				prev_depth = F_DEPTH(file);
				if (is_excluded(f_name(file, fbuf), 1,
						       ALL_FILTERS)) {
					/* Keep dirs through this dir. */
					for (j = prev_depth-1; ; j--) {
						fp = flist->files[prev_i];
						if (F_DEPTH(fp) >= 0)
							break;
						prev_i = -F_DEPTH(fp)-1;
						F_DEPTH(fp) = j;
					}
				} else
					F_DEPTH(file) = -prev_i-1;
				prev_i = i;
			} else {
				/* Keep dirs through this non-dir. */
				for (j = prev_depth; ; j--) {
					fp = flist->files[prev_i];
					if (F_DEPTH(fp) >= 0)
						break;
					prev_i = -F_DEPTH(fp)-1;
					F_DEPTH(fp) = j;
				}
			}
		}
		/* Dump empty all remaining empty dirs. */
		while (1) {
			struct file_struct *fp = flist->files[prev_i];
			if (F_DEPTH(fp) >= 0)
				break;
			prev_i = -F_DEPTH(fp)-1;
			clear_file(fp);
		}

		for (i = flist->low; i <= flist->high; i++) {
			if (F_IS_ACTIVE(flist->files[i]))
				break;
		}
		flist->low = i;
		for (i = flist->high; i >= flist->low; i--) {
			if (F_IS_ACTIVE(flist->files[i]))
				break;
		}
		flist->high = i;
	}
}

static void output_flist(struct file_list *flist)
{
	char uidbuf[16], gidbuf[16], depthbuf[16];
	struct file_struct *file;
	const char *root, *dir, *slash, *name, *trail;
	const char *who = who_am_i();
	int i;

	rprintf(FINFO, "[%s] flist start=%d, count=%d, low=%d, high=%d\n",
		who, flist->ndx_start, flist->count, flist->low, flist->high);
	for (i = 0; i < flist->count; i++) {
		file = flist->files[i];
		if ((am_root || am_sender) && preserve_uid) {
			snprintf(uidbuf, sizeof uidbuf, " uid=%ld",
				 (long)F_UID(file));
		} else
			*uidbuf = '\0';
		if (preserve_gid && F_GID(file) != GID_NONE) {
			snprintf(gidbuf, sizeof gidbuf, " gid=%ld",
				 (long)F_GID(file));
		} else
			*gidbuf = '\0';
		if (!am_sender)
			snprintf(depthbuf, sizeof depthbuf, "%d", F_DEPTH(file));
		if (F_IS_ACTIVE(file)) {
			root = am_sender ? NS(F_ROOTDIR(file)) : depthbuf;
			if ((dir = file->dirname) == NULL)
				dir = slash = "";
			else
				slash = "/";
			name = file->basename;
			trail = S_ISDIR(file->mode) ? "/" : "";
		} else
			root = dir = slash = name = trail = "";
		rprintf(FINFO, "[%s] i=%d %s %s%s%s%s mode=0%o len=%.0f%s%s flags=%x\n",
			who, i, root, dir, slash, name, trail, (int)file->mode,
			(double)F_LENGTH(file), uidbuf, gidbuf, file->flags);
	}
}

enum fnc_state { s_DIR, s_SLASH, s_BASE, s_TRAILING };
enum fnc_type { t_PATH, t_ITEM };

/* Compare the names of two file_struct entities, similar to how strcmp()
 * would do if it were operating on the joined strings.
 *
 * Some differences beginning with protocol_version 29: (1) directory names
 * are compared with an assumed trailing slash so that they compare in a
 * way that would cause them to sort immediately prior to any content they
 * may have; (2) a directory of any name compares after a non-directory of
 * any name at the same depth; (3) a directory with name "." compares prior
 * to anything else.  These changes mean that a directory and a non-dir
 * with the same name will not compare as equal (protocol_version >= 29).
 *
 * The dirname component can be an empty string, but the basename component
 * cannot (and never is in the current codebase).  The basename component
 * may be NULL (for a removed item), in which case it is considered to be
 * after any existing item. */
int f_name_cmp(struct file_struct *f1, struct file_struct *f2)
{
	int dif;
	const uchar *c1, *c2;
	enum fnc_state state1, state2;
	enum fnc_type type1, type2;
	enum fnc_type t_path = protocol_version >= 29 ? t_PATH : t_ITEM;

	if (!f1 || !F_IS_ACTIVE(f1)) {
		if (!f2 || !F_IS_ACTIVE(f2))
			return 0;
		return -1;
	}
	if (!f2 || !F_IS_ACTIVE(f2))
		return 1;

	c1 = (uchar*)f1->dirname;
	c2 = (uchar*)f2->dirname;
	if (c1 == c2)
		c1 = c2 = NULL;
	if (!c1) {
		type1 = S_ISDIR(f1->mode) ? t_path : t_ITEM;
		c1 = (const uchar*)f1->basename;
		if (type1 == t_PATH && *c1 == '.' && !c1[1]) {
			type1 = t_ITEM;
			state1 = s_TRAILING;
			c1 = (uchar*)"";
		} else
			state1 = s_BASE;
	} else {
		type1 = t_path;
		state1 = s_DIR;
	}
	if (!c2) {
		type2 = S_ISDIR(f2->mode) ? t_path : t_ITEM;
		c2 = (const uchar*)f2->basename;
		if (type2 == t_PATH && *c2 == '.' && !c2[1]) {
			type2 = t_ITEM;
			state2 = s_TRAILING;
			c2 = (uchar*)"";
		} else
			state2 = s_BASE;
	} else {
		type2 = t_path;
		state2 = s_DIR;
	}

	if (type1 != type2)
		return type1 == t_PATH ? 1 : -1;

	do {
		if (!*c1) {
			switch (state1) {
			case s_DIR:
				state1 = s_SLASH;
				c1 = (uchar*)"/";
				break;
			case s_SLASH:
				type1 = S_ISDIR(f1->mode) ? t_path : t_ITEM;
				c1 = (const uchar*)f1->basename;
				if (type1 == t_PATH && *c1 == '.' && !c1[1]) {
					type1 = t_ITEM;
					state1 = s_TRAILING;
					c1 = (uchar*)"";
				} else
					state1 = s_BASE;
				break;
			case s_BASE:
				state1 = s_TRAILING;
				if (type1 == t_PATH) {
					c1 = (uchar*)"/";
					break;
				}
				/* FALL THROUGH */
			case s_TRAILING:
				type1 = t_ITEM;
				break;
			}
			if (*c2 && type1 != type2)
				return type1 == t_PATH ? 1 : -1;
		}
		if (!*c2) {
			switch (state2) {
			case s_DIR:
				state2 = s_SLASH;
				c2 = (uchar*)"/";
				break;
			case s_SLASH:
				type2 = S_ISDIR(f2->mode) ? t_path : t_ITEM;
				c2 = (const uchar*)f2->basename;
				if (type2 == t_PATH && *c2 == '.' && !c2[1]) {
					type2 = t_ITEM;
					state2 = s_TRAILING;
					c2 = (uchar*)"";
				} else
					state2 = s_BASE;
				break;
			case s_BASE:
				state2 = s_TRAILING;
				if (type2 == t_PATH) {
					c2 = (uchar*)"/";
					break;
				}
				/* FALL THROUGH */
			case s_TRAILING:
				if (!*c1)
					return 0;
				type2 = t_ITEM;
				break;
			}
			if (type1 != type2)
				return type1 == t_PATH ? 1 : -1;
		}
	} while ((dif = (int)*c1++ - (int)*c2++) == 0);

	return dif;
}

char *f_name_buf(void)
{
	static char names[5][MAXPATHLEN];
	static unsigned int n;

	n = (n + 1) % (sizeof names / sizeof names[0]);

	return names[n];
}

/* Return a copy of the full filename of a flist entry, using the indicated
 * buffer or one of 5 static buffers if fbuf is NULL.  No size-checking is
 * done because we checked the size when creating the file_struct entry.
 */
char *f_name(struct file_struct *f, char *fbuf)
{
	if (!f || !F_IS_ACTIVE(f))
		return NULL;

	if (!fbuf)
		fbuf = f_name_buf();

	if (f->dirname) {
		int len = strlen(f->dirname);
		memcpy(fbuf, f->dirname, len);
		fbuf[len] = '/';
		strlcpy(fbuf + len + 1, f->basename, MAXPATHLEN - (len + 1));
	} else
		strlcpy(fbuf, f->basename, MAXPATHLEN);

	return fbuf;
}

/* Do a non-recursive scan of the named directory, possibly ignoring all
 * exclude rules except for the daemon's.  If "dlen" is >=0, it is the length
 * of the dirname string, and also indicates that "dirname" is a MAXPATHLEN
 * buffer (the functions we call will append names onto the end, but the old
 * dir value will be restored on exit). */
struct file_list *get_dirlist(char *dirname, int dlen, int ignore_filter_rules)
{
	struct file_list *dirlist;
	char dirbuf[MAXPATHLEN];
	int save_recurse = recurse;
	int save_xfer_dirs = xfer_dirs;

	if (dlen < 0) {
		dlen = strlcpy(dirbuf, dirname, MAXPATHLEN);
		if (dlen >= MAXPATHLEN)
			return NULL;
		dirname = dirbuf;
	}

	dirlist = flist_new(FLIST_TEMP, "get_dirlist");

	recurse = 0;
	xfer_dirs = 1;
	send_directory(ignore_filter_rules ? -2 : -1, dirlist, -1, dirname, dlen, 0);
	xfer_dirs = save_xfer_dirs;
	recurse = save_recurse;
	if (do_progress)
		flist_count_offset += dirlist->count;

	clean_flist(dirlist, 0, 0);

	if (verbose > 3)
		output_flist(dirlist);

	return dirlist;
}
