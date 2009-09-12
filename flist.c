/*
 * Generate and receive file lists.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2002-2009 Wayne Davison
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
#include "rounding.h"
#include "io.h"

extern int verbose;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int am_sender;
extern int am_generator;
extern int inc_recurse;
extern int do_progress;
extern int always_checksum;
extern int module_id;
extern int ignore_errors;
extern int numeric_ids;
extern int recurse;
extern int use_qsort;
extern int xfer_dirs;
extern int filesfrom_fd;
extern int one_file_system;
extern int copy_dirlinks;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_acls;
extern int preserve_xattrs;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_devices;
extern int preserve_specials;
extern int delete_during;
extern int eol_nulls;
extern int relative_paths;
extern int implied_dirs;
extern int ignore_perishable;
extern int non_perishable_cnt;
extern int prune_empty_dirs;
extern int copy_links;
extern int copy_unsafe_links;
extern int protocol_version;
extern int sanitize_paths;
extern int munge_symlinks;
extern int use_safe_inc_flist;
extern int need_unsorted_flist;
extern int sender_symlink_iconv;
extern int unsort_ndx;
extern uid_t our_uid;
extern struct stats stats;
extern char *filesfrom_host;

extern char curr_dir[MAXPATHLEN];

extern struct chmod_mode_struct *chmod_modes;

extern struct filter_list_struct filter_list;
extern struct filter_list_struct daemon_filter_list;

#ifdef ICONV_OPTION
extern int filesfrom_convert;
extern iconv_t ic_send, ic_recv;
#endif

#define PTR_SIZE (sizeof (struct file_struct *))

int io_error;
int checksum_len;
dev_t filesystem_dev; /* used to implement -x */

struct file_list *cur_flist, *first_flist, *dir_flist;
int send_dir_ndx = -1, send_dir_depth = -1;
int flist_cnt = 0; /* how many (non-tmp) file list objects exist */
int file_total = 0; /* total of all active items over all file-lists */
int flist_eof = 0; /* all the file-lists are now known */

#define NORMAL_NAME 0
#define SLASH_ENDING_NAME 1
#define DOTDIR_NAME 2

/* Starting from protocol version 26, we always use 64-bit ino_t and dev_t
 * internally, even if this platform does not allow files to have 64-bit inums.
 * The only exception is if we're on a platform with no 64-bit type at all.
 *
 * Because we use read_longint() to get these off the wire, if you transfer
 * devices or (for protocols < 30) hardlinks with dev or inum > 2**32 to a
 * machine with no 64-bit types then you will get an overflow error.
 *
 * Note that if you transfer devices from a 64-bit-devt machine (say, Solaris)
 * to a 32-bit-devt machine (say, Linux-2.2/x86) then the device numbers will
 * be truncated.  But it's a kind of silly thing to do anyhow. */

/* The tmp_* vars are used as a cache area by make_file() to store data
 * that the sender doesn't need to remember in its file list.  The data
 * will survive just long enough to be used by send_file_entry(). */
static dev_t tmp_rdev;
#ifdef SUPPORT_HARD_LINKS
static int64 tmp_dev = -1, tmp_ino;
#endif
static char tmp_sum[MAX_DIGEST_LEN];

static char empty_sum[MAX_DIGEST_LEN];
static int flist_count_offset; /* for --delete --progress */
static int dir_count = 0;

static void flist_sort_and_clean(struct file_list *flist, int strip_root);
static void output_flist(struct file_list *flist);

void init_flist(void)
{
	if (verbose > 4) {
		rprintf(FINFO, "FILE_STRUCT_LEN=%d, EXTRA_LEN=%d\n",
			(int)FILE_STRUCT_LEN, (int)EXTRA_LEN);
	}
	checksum_len = protocol_version < 21 ? 2
		     : protocol_version < 30 ? MD4_DIGEST_LEN
		     : MD5_DIGEST_LEN;
}

static int show_filelist_p(void)
{
	return verbose && xfer_dirs && !am_server && !inc_recurse;
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
			flist->used, flist->used == 1 ? " " : "s ");
	} else
		rprintf(FINFO, "done\n");
}

void show_flist_stats(void)
{
	/* Nothing yet */
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
			return x_stat(path, stp, NULL);
		}
		if (munge_symlinks && am_sender && llen > SYMLINK_PREFIX_LEN
		 && strncmp(linkbuf, SYMLINK_PREFIX, SYMLINK_PREFIX_LEN) == 0) {
			memmove(linkbuf, linkbuf + SYMLINK_PREFIX_LEN,
				llen - SYMLINK_PREFIX_LEN + 1);
		}
	}
	return 0;
#else
	return x_stat(path, stp, NULL);
#endif
}

int link_stat(const char *path, STRUCT_STAT *stp, int follow_dirlinks)
{
#ifdef SUPPORT_LINKS
	if (copy_links)
		return x_stat(path, stp, NULL);
	if (x_lstat(path, stp, NULL) < 0)
		return -1;
	if (follow_dirlinks && S_ISLNK(stp->st_mode)) {
		STRUCT_STAT st;
		if (x_stat(path, &st, NULL) == 0 && S_ISDIR(st.st_mode))
			*stp = st;
	}
	return 0;
#else
	return x_stat(path, stp, NULL);
#endif
}

static inline int is_daemon_excluded(const char *fname, int is_dir)
{
	if (daemon_filter_list.head
	 && check_filter(&daemon_filter_list, FLOG, fname, is_dir) < 0) {
		errno = ENOENT;
		return 1;
	}
	return 0;
}

static inline int path_is_daemon_excluded(char *path, int ignore_filename)
{
	if (daemon_filter_list.head) {
		char *slash = path;

		while ((slash = strchr(slash+1, '/')) != NULL) {
			int ret;
			*slash = '\0';
			ret = check_filter(&daemon_filter_list, FLOG, path, 1);
			*slash = '/';
			if (ret < 0) {
				errno = ENOENT;
				return 1;
			}
		}

		if (!ignore_filename
		 && check_filter(&daemon_filter_list, FLOG, path, 1) < 0) {
			errno = ENOENT;
			return 1;
		}
	}

	return 0;
}

/* This function is used to check if a file should be included/excluded
 * from the list of files based on its name and type etc.  The value of
 * filter_level is set to either SERVER_FILTERS or ALL_FILTERS. */
static int is_excluded(const char *fname, int is_dir, int filter_level)
{
#if 0 /* This currently never happens, so avoid a useless compare. */
	if (filter_level == NO_FILTERS)
		return 0;
#endif
	if (is_daemon_excluded(fname, is_dir))
		return 1;
	if (filter_level != ALL_FILTERS)
		return 0;
	if (filter_list.head
	    && check_filter(&filter_list, FINFO, fname, is_dir) < 0)
		return 1;
	return 0;
}

static void send_directory(int f, struct file_list *flist,
			   char *fbuf, int len, int flags);

static const char *pathname, *orig_dir;
static int pathname_len;

/* Make sure flist can hold at least flist->used + extra entries. */
static void flist_expand(struct file_list *flist, int extra)
{
	struct file_struct **new_ptr;

	if (flist->used + extra <= flist->malloced)
		return;

	if (flist->malloced < FLIST_START)
		flist->malloced = FLIST_START;
	else if (flist->malloced >= FLIST_LINEAR)
		flist->malloced += FLIST_LINEAR;
	else
		flist->malloced *= 2;

	/* In case count jumped or we are starting the list
	 * with a known size just set it. */
	if (flist->malloced < flist->used + extra)
		flist->malloced = flist->used + extra;

	new_ptr = realloc_array(flist->files, struct file_struct *,
				flist->malloced);

	if (verbose >= 2 && flist->malloced != FLIST_START) {
		rprintf(FCLIENT, "[%s] expand file_list pointer array to %.0f bytes, did%s move\n",
		    who_am_i(),
		    (double)sizeof flist->files[0] * flist->malloced,
		    (new_ptr == flist->files) ? " not" : "");
	}

	flist->files = new_ptr;

	if (!flist->files)
		out_of_memory("flist_expand");
}

static void flist_done_allocating(struct file_list *flist)
{
	void *ptr = pool_boundary(flist->file_pool, 8*1024);
	if (flist->pool_boundary == ptr)
		flist->pool_boundary = NULL; /* list didn't use any pool memory */
	else
		flist->pool_boundary = ptr;
}

/* Call this with EITHER (1) "file, NULL, 0" to chdir() to the file's
 * F_PATHNAME(), or (2) "NULL, dir, dirlen" to chdir() to the supplied dir,
 * with dir == NULL taken to be the starting directory, and dirlen < 0
 * indicating that strdup(dir) should be called and then the -dirlen length
 * value checked to ensure that it is not daemon-excluded. */
int change_pathname(struct file_struct *file, const char *dir, int dirlen)
{
	if (dirlen < 0) {
		char *cpy = strdup(dir);
		if (*cpy != '/')
			change_dir(orig_dir, CD_SKIP_CHDIR);
		if (path_is_daemon_excluded(cpy, 0))
			goto chdir_error;
		dir = cpy;
		dirlen = -dirlen;
	} else {
		if (file) {
			if (pathname == F_PATHNAME(file))
				return 1;
			dir = F_PATHNAME(file);
			if (dir)
				dirlen = strlen(dir);
		} else if (pathname == dir)
			return 1;
		if (dir && *dir != '/')
			change_dir(orig_dir, CD_SKIP_CHDIR);
	}

	pathname = dir;
	pathname_len = dirlen;

	if (!dir)
		dir = orig_dir;

	if (!change_dir(dir, CD_NORMAL)) {
	  chdir_error:
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR_XFER, errno, "change_dir %s failed", full_fname(dir));
		if (dir != orig_dir)
			change_dir(orig_dir, CD_NORMAL);
		pathname = NULL;
		pathname_len = 0;
		return 0;
	}

	return 1;
}

static void send_file_entry(int f, const char *fname, struct file_struct *file,
#ifdef SUPPORT_LINKS
			    const char *symlink_name, int symlink_len,
#endif
			    int ndx, int first_ndx)
{
	static time_t modtime;
	static mode_t mode;
#ifdef SUPPORT_HARD_LINKS
	static int64 dev;
#endif
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static const char *user_name, *group_name;
	static char lastname[MAXPATHLEN];
	int first_hlink_ndx = -1;
	int l1, l2;
	int xflags;

	/* Initialize starting value of xflags. */
	if (protocol_version >= 30 && S_ISDIR(file->mode)) {
		dir_count++;
		if (file->flags & FLAG_CONTENT_DIR)
			xflags = file->flags & FLAG_TOP_DIR;
		else if (file->flags & FLAG_IMPLIED_DIR)
			xflags = XMIT_TOP_DIR | XMIT_NO_CONTENT_DIR;
		else
			xflags = XMIT_NO_CONTENT_DIR;
	} else
		xflags = file->flags & FLAG_TOP_DIR; /* FLAG_TOP_DIR == XMIT_TOP_DIR */

	if (file->mode == mode)
		xflags |= XMIT_SAME_MODE;
	else
		mode = file->mode;

	if (preserve_devices && IS_DEVICE(mode)) {
		if (protocol_version < 28) {
			if (tmp_rdev == rdev)
				xflags |= XMIT_SAME_RDEV_pre28;
			else
				rdev = tmp_rdev;
		} else {
			rdev = tmp_rdev;
			if ((uint32)major(rdev) == rdev_major)
				xflags |= XMIT_SAME_RDEV_MAJOR;
			else
				rdev_major = major(rdev);
			if (protocol_version < 30 && (uint32)minor(rdev) <= 0xFFu)
				xflags |= XMIT_RDEV_MINOR_8_pre30;
		}
	} else if (preserve_specials && IS_SPECIAL(mode)) {
		/* Special files don't need an rdev number, so just make
		 * the historical transmission of the value efficient. */
		if (protocol_version < 28)
			xflags |= XMIT_SAME_RDEV_pre28;
		else {
			rdev = MAKEDEV(major(rdev), 0);
			xflags |= XMIT_SAME_RDEV_MAJOR;
			if (protocol_version < 30)
				xflags |= XMIT_RDEV_MINOR_8_pre30;
		}
	} else if (protocol_version < 28)
		rdev = MAKEDEV(0, 0);
	if (!preserve_uid || ((uid_t)F_OWNER(file) == uid && *lastname))
		xflags |= XMIT_SAME_UID;
	else {
		uid = F_OWNER(file);
		if (!numeric_ids) {
			user_name = add_uid(uid);
			if (inc_recurse && user_name)
				xflags |= XMIT_USER_NAME_FOLLOWS;
		}
	}
	if (!preserve_gid || ((gid_t)F_GROUP(file) == gid && *lastname))
		xflags |= XMIT_SAME_GID;
	else {
		gid = F_GROUP(file);
		if (!numeric_ids) {
			group_name = add_gid(gid);
			if (inc_recurse && group_name)
				xflags |= XMIT_GROUP_NAME_FOLLOWS;
		}
	}
	if (file->modtime == modtime)
		xflags |= XMIT_SAME_TIME;
	else
		modtime = file->modtime;

#ifdef SUPPORT_HARD_LINKS
	if (tmp_dev != -1) {
		if (protocol_version >= 30) {
			struct ht_int64_node *np = idev_find(tmp_dev, tmp_ino);
			first_hlink_ndx = (int32)(long)np->data - 1;
			if (first_hlink_ndx < 0) {
				np->data = (void*)(long)(first_ndx + ndx + 1);
				xflags |= XMIT_HLINK_FIRST;
			}
		} else {
			if (tmp_dev == dev) {
				if (protocol_version >= 28)
					xflags |= XMIT_SAME_DEV_pre30;
			} else
				dev = tmp_dev;
		}
		xflags |= XMIT_HLINKED;
	}
#endif

	for (l1 = 0;
	    lastname[l1] && (fname[l1] == lastname[l1]) && (l1 < 255);
	    l1++) {}
	l2 = strlen(fname+l1);

	if (l1 > 0)
		xflags |= XMIT_SAME_NAME;
	if (l2 > 255)
		xflags |= XMIT_LONG_NAME;

	/* We must make sure we don't send a zero flag byte or the
	 * other end will terminate the flist transfer.  Note that
	 * the use of XMIT_TOP_DIR on a non-dir has no meaning, so
	 * it's harmless way to add a bit to the first flag byte. */
	if (protocol_version >= 28) {
		if (!xflags && !S_ISDIR(mode))
			xflags |= XMIT_TOP_DIR;
		if ((xflags & 0xFF00) || !xflags) {
			xflags |= XMIT_EXTENDED_FLAGS;
			write_shortint(f, xflags);
		} else
			write_byte(f, xflags);
	} else {
		if (!(xflags & 0xFF))
			xflags |= S_ISDIR(mode) ? XMIT_LONG_NAME : XMIT_TOP_DIR;
		write_byte(f, xflags);
	}
	if (xflags & XMIT_SAME_NAME)
		write_byte(f, l1);
	if (xflags & XMIT_LONG_NAME)
		write_varint30(f, l2);
	else
		write_byte(f, l2);
	write_buf(f, fname + l1, l2);

	if (first_hlink_ndx >= 0) {
		write_varint(f, first_hlink_ndx);
		if (first_hlink_ndx >= first_ndx)
			goto the_end;
	}

	write_varlong30(f, F_LENGTH(file), 3);
	if (!(xflags & XMIT_SAME_TIME)) {
		if (protocol_version >= 30)
			write_varlong(f, modtime, 4);
		else
			write_int(f, modtime);
	}
	if (!(xflags & XMIT_SAME_MODE))
		write_int(f, to_wire_mode(mode));
	if (preserve_uid && !(xflags & XMIT_SAME_UID)) {
		if (protocol_version < 30)
			write_int(f, uid);
		else {
			write_varint(f, uid);
			if (xflags & XMIT_USER_NAME_FOLLOWS) {
				int len = strlen(user_name);
				write_byte(f, len);
				write_buf(f, user_name, len);
			}
		}
	}
	if (preserve_gid && !(xflags & XMIT_SAME_GID)) {
		if (protocol_version < 30)
			write_int(f, gid);
		else {
			write_varint(f, gid);
			if (xflags & XMIT_GROUP_NAME_FOLLOWS) {
				int len = strlen(group_name);
				write_byte(f, len);
				write_buf(f, group_name, len);
			}
		}
	}
	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		if (protocol_version < 28) {
			if (!(xflags & XMIT_SAME_RDEV_pre28))
				write_int(f, (int)rdev);
		} else {
			if (!(xflags & XMIT_SAME_RDEV_MAJOR))
				write_varint30(f, major(rdev));
			if (protocol_version >= 30)
				write_varint(f, minor(rdev));
			else if (xflags & XMIT_RDEV_MINOR_8_pre30)
				write_byte(f, minor(rdev));
			else
				write_int(f, minor(rdev));
		}
	}

#ifdef SUPPORT_LINKS
	if (symlink_len) {
		write_varint30(f, symlink_len);
		write_buf(f, symlink_name, symlink_len);
	}
#endif

#ifdef SUPPORT_HARD_LINKS
	if (tmp_dev != -1 && protocol_version < 30) {
		/* Older protocols expect the dev number to be transmitted
		 * 1-incremented so that it is never zero. */
		if (protocol_version < 26) {
			/* 32-bit dev_t and ino_t */
			write_int(f, (int32)(dev+1));
			write_int(f, (int32)tmp_ino);
		} else {
			/* 64-bit dev_t and ino_t */
			if (!(xflags & XMIT_SAME_DEV_pre30))
				write_longint(f, dev+1);
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

	if (S_ISREG(mode) || S_ISLNK(mode))
		stats.total_size += F_LENGTH(file);
}

static struct file_struct *recv_file_entry(int f, struct file_list *flist, int xflags)
{
	static int64 modtime;
	static mode_t mode;
#ifdef SUPPORT_HARD_LINKS
	static int64 dev;
#endif
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static uint16 gid_flags;
	static char lastname[MAXPATHLEN], *lastdir;
	static int lastdir_depth, lastdir_len = -1;
	static unsigned int del_hier_name_len = 0;
	static int in_del_hier = 0;
	char thisname[MAXPATHLEN];
	unsigned int l1 = 0, l2 = 0;
	int alloc_len, basename_len, linkname_len;
	int extra_len = file_extra_cnt * EXTRA_LEN;
	int first_hlink_ndx = -1;
	int64 file_length;
	const char *basename;
	struct file_struct *file;
	alloc_pool_t *pool;
	char *bp;

	if (xflags & XMIT_SAME_NAME)
		l1 = read_byte(f);

	if (xflags & XMIT_LONG_NAME)
		l2 = read_varint30(f);
	else
		l2 = read_byte(f);

	if (l2 >= MAXPATHLEN - l1) {
		rprintf(FERROR,
			"overflow: xflags=0x%x l1=%d l2=%d lastname=%s [%s]\n",
			xflags, l1, l2, lastname, who_am_i());
		overflow_exit("recv_file_entry");
	}

	strlcpy(thisname, lastname, l1 + 1);
	read_sbuf(f, &thisname[l1], l2);
	thisname[l1 + l2] = 0;

	/* Abuse basename_len for a moment... */
	basename_len = strlcpy(lastname, thisname, MAXPATHLEN);

#ifdef ICONV_OPTION
	if (ic_recv != (iconv_t)-1) {
		xbuf outbuf, inbuf;

		INIT_CONST_XBUF(outbuf, thisname);
		INIT_XBUF(inbuf, lastname, basename_len, -1);

		if (iconvbufs(ic_recv, &inbuf, &outbuf, 0) < 0) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR_UTF8,
			    "[%s] cannot convert filename: %s (%s)\n",
			    who_am_i(), lastname, strerror(errno));
			outbuf.len = 0;
		}
		thisname[outbuf.len] = '\0';
	}
#endif

	if (*thisname)
		clean_fname(thisname, 0);

	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0, SP_DEFAULT);

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
	 && BITS_SETnUNSET(xflags, XMIT_HLINKED, XMIT_HLINK_FIRST)) {
		first_hlink_ndx = read_varint(f);
		if (first_hlink_ndx < 0 || first_hlink_ndx >= flist->ndx_start + flist->used) {
			rprintf(FERROR,
				"hard-link reference out of range: %d (%d)\n",
				first_hlink_ndx, flist->ndx_start + flist->used);
			exit_cleanup(RERR_PROTOCOL);
		}
		if (first_hlink_ndx >= flist->ndx_start) {
			struct file_struct *first = flist->files[first_hlink_ndx - flist->ndx_start];
			file_length = F_LENGTH(first);
			modtime = first->modtime;
			mode = first->mode;
			if (preserve_uid)
				uid = F_OWNER(first);
			if (preserve_gid)
				gid = F_GROUP(first);
			if (preserve_devices && IS_DEVICE(mode)) {
				uint32 *devp = F_RDEV_P(first);
				rdev = MAKEDEV(DEV_MAJOR(devp), DEV_MINOR(devp));
				extra_len += DEV_EXTRA_CNT * EXTRA_LEN;
			}
			if (preserve_links && S_ISLNK(mode))
				linkname_len = strlen(F_SYMLINK(first)) + 1;
			else
				linkname_len = 0;
			goto create_object;
		}
	}
#endif

	file_length = read_varlong30(f, 3);
	if (!(xflags & XMIT_SAME_TIME)) {
		if (protocol_version >= 30) {
			modtime = read_varlong(f, 4);
#if SIZEOF_TIME_T < SIZEOF_INT64
			if (!am_generator && (int64)(time_t)modtime != modtime) {
				rprintf(FERROR_XFER,
				    "Time value of %s truncated on receiver.\n",
				    lastname);
			}
#endif
		} else
			modtime = read_int(f);
	}
	if (!(xflags & XMIT_SAME_MODE))
		mode = from_wire_mode(read_int(f));

	if (chmod_modes && !S_ISLNK(mode))
		mode = tweak_mode(mode, chmod_modes);

	if (preserve_uid && !(xflags & XMIT_SAME_UID)) {
		if (protocol_version < 30)
			uid = (uid_t)read_int(f);
		else {
			uid = (uid_t)read_varint(f);
			if (xflags & XMIT_USER_NAME_FOLLOWS)
				uid = recv_user_name(f, uid);
			else if (inc_recurse && am_root && !numeric_ids)
				uid = match_uid(uid);
		}
	}
	if (preserve_gid && !(xflags & XMIT_SAME_GID)) {
		if (protocol_version < 30)
			gid = (gid_t)read_int(f);
		else {
			gid = (gid_t)read_varint(f);
			gid_flags = 0;
			if (xflags & XMIT_GROUP_NAME_FOLLOWS)
				gid = recv_group_name(f, gid, &gid_flags);
			else if (inc_recurse && (!am_root || !numeric_ids))
				gid = match_gid(gid, &gid_flags);
		}
	}

	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		if (protocol_version < 28) {
			if (!(xflags & XMIT_SAME_RDEV_pre28))
				rdev = (dev_t)read_int(f);
		} else {
			uint32 rdev_minor;
			if (!(xflags & XMIT_SAME_RDEV_MAJOR))
				rdev_major = read_varint30(f);
			if (protocol_version >= 30)
				rdev_minor = read_varint(f);
			else if (xflags & XMIT_RDEV_MINOR_8_pre30)
				rdev_minor = read_byte(f);
			else
				rdev_minor = read_int(f);
			rdev = MAKEDEV(rdev_major, rdev_minor);
		}
		if (IS_DEVICE(mode))
			extra_len += DEV_EXTRA_CNT * EXTRA_LEN;
		file_length = 0;
	} else if (protocol_version < 28)
		rdev = MAKEDEV(0, 0);

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(mode)) {
		linkname_len = read_varint30(f) + 1; /* count the '\0' */
		if (linkname_len <= 0 || linkname_len > MAXPATHLEN) {
			rprintf(FERROR, "overflow: linkname_len=%d\n",
				linkname_len - 1);
			overflow_exit("recv_file_entry");
		}
#ifdef ICONV_OPTION
		/* We don't know how much extra room we need to convert
		 * the as-yet-unread symlink data, so let's hope that a
		 * double-size buffer is plenty. */
		if (sender_symlink_iconv)
			linkname_len *= 2;
#endif
		if (munge_symlinks)
			linkname_len += SYMLINK_PREFIX_LEN;
	}
	else
#endif
		linkname_len = 0;

#ifdef SUPPORT_HARD_LINKS
  create_object:
	if (preserve_hard_links) {
		if (protocol_version < 28 && S_ISREG(mode))
			xflags |= XMIT_HLINKED;
		if (xflags & XMIT_HLINKED)
			extra_len += (inc_recurse+1) * EXTRA_LEN;
	}
#endif

#ifdef SUPPORT_ACLS
	/* Directories need an extra int32 for the default ACL. */
	if (preserve_acls && S_ISDIR(mode))
		extra_len += EXTRA_LEN;
#endif

	if (always_checksum && S_ISREG(mode))
		extra_len += SUM_EXTRA_CNT * EXTRA_LEN;

#if SIZEOF_INT64 >= 8
	if (file_length > 0xFFFFFFFFu && S_ISREG(mode))
		extra_len += EXTRA_LEN;
#endif
	if (file_length < 0) {
		rprintf(FERROR, "Offset underflow: file-length is negative\n");
		exit_cleanup(RERR_UNSUPPORTED);
	}

	if (inc_recurse && S_ISDIR(mode)) {
		if (one_file_system) {
			/* Room to save the dir's device for -x */
			extra_len += DEV_EXTRA_CNT * EXTRA_LEN;
		}
		pool = dir_flist->file_pool;
	} else
		pool = flist->file_pool;

#if EXTRA_ROUNDING > 0
	if (extra_len & (EXTRA_ROUNDING * EXTRA_LEN))
		extra_len = (extra_len | (EXTRA_ROUNDING * EXTRA_LEN)) + EXTRA_LEN;
#endif

	alloc_len = FILE_STRUCT_LEN + extra_len + basename_len
		  + linkname_len;
	bp = pool_alloc(pool, alloc_len, "recv_file_entry");

	memset(bp, 0, extra_len + FILE_STRUCT_LEN);
	bp += extra_len;
	file = (struct file_struct *)bp;
	bp += FILE_STRUCT_LEN;

	memcpy(bp, basename, basename_len);

#ifdef SUPPORT_HARD_LINKS
	if (xflags & XMIT_HLINKED)
		file->flags |= FLAG_HLINKED;
#endif
	file->modtime = (time_t)modtime;
	file->len32 = (uint32)file_length;
#if SIZEOF_INT64 >= 8
	if (file_length > 0xFFFFFFFFu && S_ISREG(mode)) {
#if SIZEOF_CAPITAL_OFF_T < 8
		rprintf(FERROR, "Offset overflow: attempted 64-bit file-length\n");
		exit_cleanup(RERR_UNSUPPORTED);
#else
		file->flags |= FLAG_LENGTH64;
		OPT_EXTRA(file, 0)->unum = (uint32)(file_length >> 32);
#endif
	}
#endif
	file->mode = mode;
	if (preserve_uid)
		F_OWNER(file) = uid;
	if (preserve_gid) {
		F_GROUP(file) = gid;
		file->flags |= gid_flags;
	}
	if (unsort_ndx)
		F_NDX(file) = flist->used + flist->ndx_start;

	if (basename != thisname) {
		file->dirname = lastdir;
		F_DEPTH(file) = lastdir_depth + 1;
	} else
		F_DEPTH(file) = 1;

	if (S_ISDIR(mode)) {
		if (basename_len == 1+1 && *basename == '.') /* +1 for '\0' */
			F_DEPTH(file)--;
		if (protocol_version >= 30) {
			if (!(xflags & XMIT_NO_CONTENT_DIR)) {
				if (xflags & XMIT_TOP_DIR)
					file->flags |= FLAG_TOP_DIR;
				file->flags |= FLAG_CONTENT_DIR;
			} else if (xflags & XMIT_TOP_DIR)
				file->flags |= FLAG_IMPLIED_DIR;
		} else if (xflags & XMIT_TOP_DIR) {
			in_del_hier = recurse;
			del_hier_name_len = F_DEPTH(file) == 0 ? 0 : l1 + l2;
			if (relative_paths && del_hier_name_len > 2
			    && lastname[del_hier_name_len-1] == '.'
			    && lastname[del_hier_name_len-2] == '/')
				del_hier_name_len -= 2;
			file->flags |= FLAG_TOP_DIR | FLAG_CONTENT_DIR;
		} else if (in_del_hier) {
			if (!relative_paths || !del_hier_name_len
			 || (l1 >= del_hier_name_len
			  && lastname[del_hier_name_len] == '/'))
				file->flags |= FLAG_CONTENT_DIR;
			else
				in_del_hier = 0;
		}
	}

	if (preserve_devices && IS_DEVICE(mode)) {
		uint32 *devp = F_RDEV_P(file);
		DEV_MAJOR(devp) = major(rdev);
		DEV_MINOR(devp) = minor(rdev);
	}

#ifdef SUPPORT_LINKS
	if (linkname_len) {
		bp += basename_len;
		if (first_hlink_ndx >= flist->ndx_start) {
			struct file_struct *first = flist->files[first_hlink_ndx - flist->ndx_start];
			memcpy(bp, F_SYMLINK(first), linkname_len);
		} else {
			if (munge_symlinks) {
				strlcpy(bp, SYMLINK_PREFIX, linkname_len);
				bp += SYMLINK_PREFIX_LEN;
				linkname_len -= SYMLINK_PREFIX_LEN;
			}
#ifdef ICONV_OPTION
			if (sender_symlink_iconv) {
				xbuf outbuf, inbuf;

				alloc_len = linkname_len;
				linkname_len /= 2;

				/* Read the symlink data into the end of our double-sized
				 * buffer and then convert it into the right spot. */
				INIT_XBUF(inbuf, bp + alloc_len - linkname_len,
					  linkname_len - 1, (size_t)-1);
				read_sbuf(f, inbuf.buf, inbuf.len);
				INIT_XBUF(outbuf, bp, 0, alloc_len);

				if (iconvbufs(ic_recv, &inbuf, &outbuf, 0) < 0) {
					io_error |= IOERR_GENERAL;
					rprintf(FERROR_XFER,
					    "[%s] cannot convert symlink data for: %s (%s)\n",
					    who_am_i(), full_fname(thisname), strerror(errno));
					bp = (char*)file->basename;
					*bp++ = '\0';
					outbuf.len = 0;
				}
				bp[outbuf.len] = '\0';
			} else
#endif
				read_sbuf(f, bp, linkname_len - 1);
			if (sanitize_paths && !munge_symlinks && *bp)
				sanitize_path(bp, bp, "", lastdir_depth, SP_DEFAULT);
		}
	}
#endif

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && xflags & XMIT_HLINKED) {
		if (protocol_version >= 30) {
			if (xflags & XMIT_HLINK_FIRST) {
				F_HL_GNUM(file) = flist->ndx_start + flist->used;
			} else
				F_HL_GNUM(file) = first_hlink_ndx;
		} else {
			static int32 cnt = 0;
			struct ht_int64_node *np;
			int64 ino;
			int32 ndx;
			if (protocol_version < 26) {
				dev = read_int(f);
				ino = read_int(f);
			} else {
				if (!(xflags & XMIT_SAME_DEV_pre30))
					dev = read_longint(f);
				ino = read_longint(f);
			}
			np = idev_find(dev, ino);
			ndx = (int32)(long)np->data - 1;
			if (ndx < 0) {
				ndx = cnt++;
				np->data = (void*)(long)cnt;
			}
			F_HL_GNUM(file) = ndx;
		}
	}
#endif

	if (always_checksum && (S_ISREG(mode) || protocol_version < 28)) {
		if (S_ISREG(mode))
			bp = F_SUM(file);
		else {
			/* Prior to 28, we get a useless set of nulls. */
			bp = tmp_sum;
		}
		if (first_hlink_ndx >= flist->ndx_start) {
			struct file_struct *first = flist->files[first_hlink_ndx - flist->ndx_start];
			memcpy(bp, F_SUM(first), checksum_len);
		} else
			read_buf(f, bp, checksum_len);
	}

#ifdef SUPPORT_ACLS
	if (preserve_acls && !S_ISLNK(mode))
		receive_acl(f, file);
#endif
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs)
		receive_xattr(f, file);
#endif

	if (S_ISREG(mode) || S_ISLNK(mode))
		stats.total_size += file_length;

	return file;
}

/* Create a file_struct for a named file by reading its stat() information
 * and performing extensive checks against global options.
 *
 * Returns a pointer to the new file struct, or NULL if there was an error
 * or this file should be excluded.
 *
 * Note: Any error (here or in send_file_name) that results in the omission of
 * an existent source file from the file list should set
 * "io_error |= IOERR_GENERAL" to avoid deletion of the file from the
 * destination if --delete is on. */
struct file_struct *make_file(const char *fname, struct file_list *flist,
			      STRUCT_STAT *stp, int flags, int filter_level)
{
	static char *lastdir;
	static int lastdir_len = -1;
	struct file_struct *file;
	char thisname[MAXPATHLEN];
	char linkname[MAXPATHLEN];
	int alloc_len, basename_len, linkname_len;
	int extra_len = file_extra_cnt * EXTRA_LEN;
	const char *basename;
	alloc_pool_t *pool;
	STRUCT_STAT st;
	char *bp;

	if (strlcpy(thisname, fname, sizeof thisname) >= sizeof thisname) {
		io_error |= IOERR_GENERAL;
		rprintf(FERROR_XFER, "skipping overly long name: %s\n", fname);
		return NULL;
	}
	clean_fname(thisname, 0);
	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0, SP_DEFAULT);

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
			/* When our options tell us to follow a symlink that
			 * points nowhere, tell the user about the symlink
			 * instead of giving a "vanished" message.  We only
			 * dereference a symlink if one of the --copy*links
			 * options was specified, so there's no need for the
			 * extra lstat() if one of these options isn't on. */
			if ((copy_links || copy_unsafe_links || copy_dirlinks)
			 && x_lstat(thisname, &st, NULL) == 0
			 && S_ISLNK(st.st_mode)) {
				io_error |= IOERR_GENERAL;
				rprintf(FERROR_XFER, "symlink has no referent: %s\n",
					full_fname(thisname));
			} else
#endif
			{
				enum logcode c = am_daemon && protocol_version < 28
					       ? FERROR : FWARNING;
				io_error |= IOERR_VANISHED;
				rprintf(c, "file has vanished: %s\n",
					full_fname(thisname));
			}
		} else {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR_XFER, save_errno, "readlink_stat(%s) failed",
				full_fname(thisname));
		}
		return NULL;
	}

	if (filter_level == NO_FILTERS)
		goto skip_filters;

	if (S_ISDIR(st.st_mode)) {
		if (!xfer_dirs) {
			rprintf(FINFO, "skipping directory %s\n", thisname);
			return NULL;
		}
		/* -x only affects dirs because we need to avoid recursing
		 * into a mount-point directory, not to avoid copying a
		 * symlinked file if -L (or similar) was specified. */
		if (one_file_system && st.st_dev != filesystem_dev
		 && BITS_SETnUNSET(flags, FLAG_CONTENT_DIR, FLAG_TOP_DIR)) {
			if (one_file_system > 1) {
				if (verbose > 1) {
					rprintf(FINFO,
					    "[%s] skipping mount-point dir %s\n",
					    who_am_i(), thisname);
				}
				return NULL;
			}
			flags |= FLAG_MOUNT_DIR;
			flags &= ~FLAG_CONTENT_DIR;
		}
	} else
		flags &= ~FLAG_CONTENT_DIR;

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
	if (flist) {
		if (flist->prev && S_ISDIR(st.st_mode)
		 && flags & FLAG_DIVERT_DIRS) {
			/* Room for parent/sibling/next-child info. */
			extra_len += DIRNODE_EXTRA_CNT * EXTRA_LEN;
			if (relative_paths)
				extra_len += PTR_EXTRA_CNT * EXTRA_LEN;
			pool = dir_flist->file_pool;
		} else
			pool = flist->file_pool;
	} else {
#ifdef SUPPORT_ACLS
		/* Directories need an extra int32 for the default ACL. */
		if (preserve_acls && S_ISDIR(st.st_mode))
			extra_len += EXTRA_LEN;
#endif
		pool = NULL;
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

#if SIZEOF_CAPITAL_OFF_T >= 8
	if (st.st_size > 0xFFFFFFFFu && S_ISREG(st.st_mode))
		extra_len += EXTRA_LEN;
#endif

#if EXTRA_ROUNDING > 0
	if (extra_len & (EXTRA_ROUNDING * EXTRA_LEN))
		extra_len = (extra_len | (EXTRA_ROUNDING * EXTRA_LEN)) + EXTRA_LEN;
#endif

	alloc_len = FILE_STRUCT_LEN + extra_len + basename_len
		  + linkname_len;
	if (pool)
		bp = pool_alloc(pool, alloc_len, "make_file");
	else {
		if (!(bp = new_array(char, alloc_len)))
			out_of_memory("make_file");
	}

	memset(bp, 0, extra_len + FILE_STRUCT_LEN);
	bp += extra_len;
	file = (struct file_struct *)bp;
	bp += FILE_STRUCT_LEN;

	memcpy(bp, basename, basename_len);

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && flist && flist->prev) {
		if (protocol_version >= 28
		 ? (!S_ISDIR(st.st_mode) && st.st_nlink > 1)
		 : S_ISREG(st.st_mode)) {
			tmp_dev = (int64)st.st_dev;
			tmp_ino = (int64)st.st_ino;
		} else
			tmp_dev = -1;
	}
#endif

#ifdef HAVE_STRUCT_STAT_ST_RDEV
	if (IS_DEVICE(st.st_mode)) {
		tmp_rdev = st.st_rdev;
		st.st_size = 0;
	} else if (IS_SPECIAL(st.st_mode))
		st.st_size = 0;
#endif

	file->flags = flags;
	file->modtime = st.st_mtime;
	file->len32 = (uint32)st.st_size;
#if SIZEOF_CAPITAL_OFF_T >= 8
	if (st.st_size > 0xFFFFFFFFu && S_ISREG(st.st_mode)) {
		file->flags |= FLAG_LENGTH64;
		OPT_EXTRA(file, 0)->unum = (uint32)(st.st_size >> 32);
	}
#endif
	file->mode = st.st_mode;
	if (preserve_uid)
		F_OWNER(file) = st.st_uid;
	if (preserve_gid)
		F_GROUP(file) = st.st_gid;
	if (am_generator && st.st_uid == our_uid)
		file->flags |= FLAG_OWNED_BY_US;

	if (basename != thisname)
		file->dirname = lastdir;

#ifdef SUPPORT_LINKS
	if (linkname_len)
		memcpy(bp + basename_len, linkname, linkname_len);
#endif

	if (always_checksum && am_sender && S_ISREG(st.st_mode))
		file_checksum(thisname, tmp_sum, st.st_size);

	if (am_sender)
		F_PATHNAME(file) = pathname;
	else if (!pool)
		F_DEPTH(file) = extra_len / EXTRA_LEN;

	if (basename_len == 0+1) {
		if (!pool)
			unmake_file(file);
		return NULL;
	}

	if (unsort_ndx)
		F_NDX(file) = dir_count;

	return file;
}

/* Only called for temporary file_struct entries created by make_file(). */
void unmake_file(struct file_struct *file)
{
	free(REQ_EXTRA(file, F_DEPTH(file)));
}

static struct file_struct *send_file_name(int f, struct file_list *flist,
					  const char *fname, STRUCT_STAT *stp,
					  int flags, int filter_level)
{
	struct file_struct *file;

	file = make_file(fname, flist, stp, flags, filter_level);
	if (!file)
		return NULL;

	if (chmod_modes && !S_ISLNK(file->mode))
		file->mode = tweak_mode(file->mode, chmod_modes);

	if (f >= 0) {
		char fbuf[MAXPATHLEN];
#ifdef SUPPORT_LINKS
		const char *symlink_name;
		int symlink_len;
#ifdef ICONV_OPTION
		char symlink_buf[MAXPATHLEN];
#endif
#endif
#if defined SUPPORT_ACLS || defined SUPPORT_XATTRS
		stat_x sx;
#endif

#ifdef SUPPORT_LINKS
		if (preserve_links && S_ISLNK(file->mode)) {
			symlink_name = F_SYMLINK(file);
			symlink_len = strlen(symlink_name);
			if (symlink_len == 0) {
				io_error |= IOERR_GENERAL;
				f_name(file, fbuf);
				rprintf(FERROR_XFER,
				    "skipping symlink with 0-length value: %s\n",
				    full_fname(fbuf));
				return NULL;
			}
		} else {
			symlink_name = NULL;
			symlink_len = 0;
		}
#endif

#ifdef ICONV_OPTION
		if (ic_send != (iconv_t)-1) {
			xbuf outbuf, inbuf;

			INIT_CONST_XBUF(outbuf, fbuf);

			if (file->dirname) {
				INIT_XBUF_STRLEN(inbuf, (char*)file->dirname);
				outbuf.size -= 2; /* Reserve room for '/' & 1 more char. */
				if (iconvbufs(ic_send, &inbuf, &outbuf, 0) < 0)
					goto convert_error;
				outbuf.size += 2;
				fbuf[outbuf.len++] = '/';
			}

			INIT_XBUF_STRLEN(inbuf, (char*)file->basename);
			if (iconvbufs(ic_send, &inbuf, &outbuf, 0) < 0) {
			  convert_error:
				io_error |= IOERR_GENERAL;
				rprintf(FERROR_XFER,
				    "[%s] cannot convert filename: %s (%s)\n",
				    who_am_i(), f_name(file, fbuf), strerror(errno));
				return NULL;
			}
			fbuf[outbuf.len] = '\0';

#ifdef SUPPORT_LINKS
			if (symlink_len && sender_symlink_iconv) {
				INIT_XBUF(inbuf, (char*)symlink_name, symlink_len, (size_t)-1);
				INIT_CONST_XBUF(outbuf, symlink_buf);
				if (iconvbufs(ic_send, &inbuf, &outbuf, 0) < 0) {
					io_error |= IOERR_GENERAL;
					f_name(file, fbuf);
					rprintf(FERROR_XFER,
					    "[%s] cannot convert symlink data for: %s (%s)\n",
					    who_am_i(), full_fname(fbuf), strerror(errno));
					return NULL;
				}
				symlink_buf[outbuf.len] = '\0';

				symlink_name = symlink_buf;
				symlink_len = outbuf.len;
			}
#endif
		} else
#endif
			f_name(file, fbuf);

#ifdef SUPPORT_ACLS
		if (preserve_acls && !S_ISLNK(file->mode)) {
			sx.st.st_mode = file->mode;
			sx.acc_acl = sx.def_acl = NULL;
			if (get_acl(fname, &sx) < 0) {
				io_error |= IOERR_GENERAL;
				return NULL;
			}
		}
#endif
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs) {
			sx.st.st_mode = file->mode;
			sx.xattr = NULL;
			if (get_xattr(fname, &sx) < 0) {
				io_error |= IOERR_GENERAL;
				return NULL;
			}
		}
#endif

		send_file_entry(f, fbuf, file,
#ifdef SUPPORT_LINKS
				symlink_name, symlink_len,
#endif
				flist->used, flist->ndx_start);

#ifdef SUPPORT_ACLS
		if (preserve_acls && !S_ISLNK(file->mode)) {
			send_acl(f, &sx);
			free_acl(&sx);
		}
#endif
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs) {
			F_XATTR(file) = send_xattr(f, &sx);
			free_xattr(&sx);
		}
#endif
	}

	maybe_emit_filelist_progress(flist->used + flist_count_offset);

	flist_expand(flist, 1);
	flist->files[flist->used++] = file;

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
		save_filters = push_local_filters(fbuf, len);
		send_directory(f, flist, fbuf, len, flags);
		pop_local_filters(save_filters);
		fbuf[ol] = '\0';
		if (is_dot_dir)
			fbuf[ol-1] = '.';
	}
}

static int file_compare(const void *file1, const void *file2)
{
	return f_name_cmp(*(struct file_struct **)file1,
			  *(struct file_struct **)file2);
}

/* The guts of a merge-sort algorithm.  This was derived from the glibc
 * version, but I (Wayne) changed the merge code to do less copying and
 * to require only half the amount of temporary memory. */
static void fsort_tmp(struct file_struct **fp, size_t num,
		      struct file_struct **tmp)
{
	struct file_struct **f1, **f2, **t;
	size_t n1, n2;

	n1 = num / 2;
	n2 = num - n1;
	f1 = fp;
	f2 = fp + n1;

	if (n1 > 1)
		fsort_tmp(f1, n1, tmp);
	if (n2 > 1)
		fsort_tmp(f2, n2, tmp);

	while (f_name_cmp(*f1, *f2) <= 0) {
		if (!--n1)
			return;
		f1++;
	}

	t = tmp;
	memcpy(t, f1, n1 * PTR_SIZE);

	*f1++ = *f2++, n2--;

	while (n1 > 0 && n2 > 0) {
		if (f_name_cmp(*t, *f2) <= 0)
			*f1++ = *t++, n1--;
		else
			*f1++ = *f2++, n2--;
	}

	if (n1 > 0)
		memcpy(f1, t, n1 * PTR_SIZE);
}

/* This file-struct sorting routine makes sure that any identical names in
 * the file list stay in the same order as they were in the original list.
 * This is particularly vital in inc_recurse mode where we expect a sort
 * on the flist to match the exact order of a sort on the dir_flist. */
static void fsort(struct file_struct **fp, size_t num)
{
	if (num <= 1)
		return;

	if (use_qsort)
		qsort(fp, num, PTR_SIZE, file_compare);
	else {
		struct file_struct **tmp = new_array(struct file_struct *,
						     (num+1) / 2);
		fsort_tmp(fp, num, tmp);
		free(tmp);
	}
}

/* We take an entire set of sibling dirs from the sorted flist and link them
 * into the tree, setting the appropriate parent/child/sibling pointers. */
static void add_dirs_to_tree(int parent_ndx, struct file_list *from_flist,
			     int dir_cnt)
{
	int i;
	int32 *dp = NULL;
	int32 *parent_dp = parent_ndx < 0 ? NULL
			 : F_DIR_NODE_P(dir_flist->sorted[parent_ndx]);

	flist_expand(dir_flist, dir_cnt);
	dir_flist->sorted = dir_flist->files;

	for (i = 0; dir_cnt; i++) {
		struct file_struct *file = from_flist->sorted[i];

		if (!S_ISDIR(file->mode))
			continue;

		dir_flist->files[dir_flist->used++] = file;
		dir_cnt--;

		if (file->basename[0] == '.' && file->basename[1] == '\0')
			continue;

		if (dp)
			DIR_NEXT_SIBLING(dp) = dir_flist->used - 1;
		else if (parent_dp)
			DIR_FIRST_CHILD(parent_dp) = dir_flist->used - 1;
		else
			send_dir_ndx = dir_flist->used - 1;

		dp = F_DIR_NODE_P(file);
		DIR_PARENT(dp) = parent_ndx;
		DIR_FIRST_CHILD(dp) = -1;
	}
	if (dp)
		DIR_NEXT_SIBLING(dp) = -1;
}

static void interpret_stat_error(const char *fname, int is_dir)
{
	if (errno == ENOENT) {
		io_error |= IOERR_VANISHED;
		rprintf(FWARNING, "%s has vanished: %s\n",
			is_dir ? "directory" : "file", full_fname(fname));
	} else {
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR_XFER, errno, "link_stat %s failed",
			full_fname(fname));
	}
}

/* This function is normally called by the sender, but the receiving side also
 * calls it from get_dirlist() with f set to -1 so that we just construct the
 * file list in memory without sending it over the wire.  Also, get_dirlist()
 * might call this with f set to -2, which also indicates that local filter
 * rules should be ignored. */
static void send_directory(int f, struct file_list *flist, char *fbuf, int len,
			   int flags)
{
	struct dirent *di;
	unsigned remainder;
	char *p;
	DIR *d;
	int divert_dirs = (flags & FLAG_DIVERT_DIRS) != 0;
	int start = flist->used;
	int filter_level = f == -2 ? SERVER_FILTERS : ALL_FILTERS;

	assert(flist != NULL);

	if (!(d = opendir(fbuf))) {
		if (errno == ENOENT) {
			if (am_sender) /* Can abuse this for vanished error w/ENOENT: */
				interpret_stat_error(fbuf, True);
			return;
		}
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR_XFER, errno, "opendir %s failed", full_fname(fbuf));
		return;
	}

	p = fbuf + len;
	if (len == 1 && *fbuf == '/')
		remainder = MAXPATHLEN - 1;
	else if (len < MAXPATHLEN-1) {
		*p++ = '/';
		*p = '\0';
		remainder = MAXPATHLEN - (len + 1);
	} else
		remainder = 0;

	for (errno = 0, di = readdir(d); di; errno = 0, di = readdir(d)) {
		char *dname = d_name(di);
		if (dname[0] == '.' && (dname[1] == '\0'
		    || (dname[1] == '.' && dname[2] == '\0')))
			continue;
		unsigned name_len = strlcpy(p, dname, remainder);
		if (name_len >= remainder) {
			char save = fbuf[len];
			fbuf[len] = '\0';
			io_error |= IOERR_GENERAL;
			rprintf(FERROR_XFER,
				"filename overflows max-path len by %u: %s/%s\n",
				name_len - remainder + 1, fbuf, dname);
			fbuf[len] = save;
			continue;
		}
		if (dname[0] == '\0') {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR_XFER,
				"cannot send file with empty name in %s\n",
				full_fname(fbuf));
			continue;
		}

		send_file_name(f, flist, fbuf, NULL, flags, filter_level);
	}

	fbuf[len] = '\0';

	if (errno) {
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR_XFER, errno, "readdir(%s)", full_fname(fbuf));
	}

	closedir(d);

	if (f >= 0 && recurse && !divert_dirs) {
		int i, end = flist->used - 1;
		/* send_if_directory() bumps flist->used, so use "end". */
		for (i = start; i <= end; i++)
			send_if_directory(f, flist, flist->files[i], fbuf, len, flags);
	}
}

static void send_implied_dirs(int f, struct file_list *flist, char *fname,
			      char *start, char *limit, int flags, char name_type)
{
	static char lastpath[MAXPATHLEN] = "";
	static int lastpath_len = 0;
	static struct file_struct *lastpath_struct = NULL;
	struct file_struct *file;
	item_list *relname_list;
	relnamecache **rnpp;
	int len, need_new_dir, depth = 0;
	struct filter_list_struct save_filter_list = filter_list;

	flags = (flags | FLAG_IMPLIED_DIR) & ~(FLAG_TOP_DIR | FLAG_CONTENT_DIR);
	filter_list.head = filter_list.tail = NULL; /* Don't filter implied dirs. */

	if (inc_recurse) {
		if (lastpath_struct && F_PATHNAME(lastpath_struct) == pathname
		 && lastpath_len == limit - fname
		 && strncmp(lastpath, fname, lastpath_len) == 0)
			need_new_dir = 0;
		else
			need_new_dir = 1;
	} else {
		char *tp = fname, *lp = lastpath;
		/* Skip any initial directories in our path that we
		 * have in common with lastpath. */
		assert(start == fname);
		for ( ; ; tp++, lp++) {
			if (tp == limit) {
				if (*lp == '/' || *lp == '\0')
					goto done;
				break;
			}
			if (*lp != *tp)
				break;
			if (*tp == '/') {
				start = tp;
				depth++;
			}
		}
		need_new_dir = 1;
	}

	if (need_new_dir) {
		int save_copy_links = copy_links;
		int save_xfer_dirs = xfer_dirs;
		char *slash;

		copy_links = xfer_dirs = 1;

		*limit = '\0';

		for (slash = start; (slash = strchr(slash+1, '/')) != NULL; ) {
			*slash = '\0';
			file = send_file_name(f, flist, fname, NULL, flags, ALL_FILTERS);
			depth++;
			if (!inc_recurse && file && S_ISDIR(file->mode))
				change_local_filter_dir(fname, strlen(fname), depth);
			*slash = '/';
		}

		file = send_file_name(f, flist, fname, NULL, flags, ALL_FILTERS);
		if (inc_recurse) {
			if (file && !S_ISDIR(file->mode))
				file = NULL;
			lastpath_struct = file;
		} else if (file && S_ISDIR(file->mode))
			change_local_filter_dir(fname, strlen(fname), ++depth);

		strlcpy(lastpath, fname, sizeof lastpath);
		lastpath_len = limit - fname;

		*limit = '/';

		copy_links = save_copy_links;
		xfer_dirs = save_xfer_dirs;

		if (!inc_recurse)
			goto done;
	}

	if (!lastpath_struct)
		goto done; /* dir must have vanished */

	len = strlen(limit+1);
	memcpy(&relname_list, F_DIR_RELNAMES_P(lastpath_struct), sizeof relname_list);
	if (!relname_list) {
		if (!(relname_list = new0(item_list)))
			out_of_memory("send_implied_dirs");
		memcpy(F_DIR_RELNAMES_P(lastpath_struct), &relname_list, sizeof relname_list);
	}
	rnpp = EXPAND_ITEM_LIST(relname_list, relnamecache *, 32);
	if (!(*rnpp = (relnamecache*)new_array(char, sizeof (relnamecache) + len)))
		out_of_memory("send_implied_dirs");
	(*rnpp)->name_type = name_type;
	strlcpy((*rnpp)->fname, limit+1, len + 1);

done:
	filter_list = save_filter_list;
}

static NORETURN void fatal_unsafe_io_error(void)
{
	/* This (sadly) can only happen when pushing data because
	 * the sender does not know about what kind of delete
	 * is in effect on the receiving side when pulling. */
	rprintf(FERROR_XFER, "FATAL I/O ERROR: dying to avoid a --delete-during issue with a pre-3.0.7 receiver.\n");
	exit_cleanup(RERR_UNSUPPORTED);
}

static void send1extra(int f, struct file_struct *file, struct file_list *flist)
{
	char fbuf[MAXPATHLEN];
	item_list *relname_list;
	int len, dlen, flags = FLAG_DIVERT_DIRS | FLAG_CONTENT_DIR;
	size_t j;

	f_name(file, fbuf);
	dlen = strlen(fbuf);

	if (!change_pathname(file, NULL, 0))
		exit_cleanup(RERR_FILESELECT);

	change_local_filter_dir(fbuf, dlen, send_dir_depth);

	if (file->flags & FLAG_CONTENT_DIR) {
		if (one_file_system) {
			STRUCT_STAT st;
			if (link_stat(fbuf, &st, copy_dirlinks) != 0) {
				interpret_stat_error(fbuf, True);
				return;
			}
			filesystem_dev = st.st_dev;
		}
		send_directory(f, flist, fbuf, dlen, flags);
	}

	if (!relative_paths)
		return;

	memcpy(&relname_list, F_DIR_RELNAMES_P(file), sizeof relname_list);
	if (!relname_list)
		return;

	for (j = 0; j < relname_list->count; j++) {
		char *slash;
		relnamecache *rnp = ((relnamecache**)relname_list->items)[j];
		char name_type = rnp->name_type;

		fbuf[dlen] = '/';
		len = strlcpy(fbuf + dlen + 1, rnp->fname, sizeof fbuf - dlen - 1);
		free(rnp);
		if (len >= (int)sizeof fbuf)
			continue; /* Impossible... */

		slash = strchr(fbuf+dlen+1, '/');
		if (slash) {
			send_implied_dirs(f, flist, fbuf, fbuf+dlen+1, slash, flags, name_type);
			continue;
		}

		if (name_type != NORMAL_NAME) {
			STRUCT_STAT st;
			if (link_stat(fbuf, &st, 1) != 0) {
				interpret_stat_error(fbuf, True);
				continue;
			}
			send_file_name(f, flist, fbuf, &st, FLAG_TOP_DIR | flags, ALL_FILTERS);
		} else
			send_file_name(f, flist, fbuf, NULL, FLAG_TOP_DIR | flags, ALL_FILTERS);
	}

	free(relname_list);
}

void send_extra_file_list(int f, int at_least)
{
	struct file_list *flist;
	int64 start_write;
	uint16 prev_flags;
	int old_cnt, save_io_error = io_error;

	if (flist_eof)
		return;

	/* Keep sending data until we have the requested number of
	 * files in the upcoming file-lists. */
	old_cnt = cur_flist->used;
	for (flist = first_flist; flist != cur_flist; flist = flist->next)
		old_cnt += flist->used;
	while (file_total - old_cnt < at_least) {
		struct file_struct *file = dir_flist->sorted[send_dir_ndx];
		int dir_ndx, dstart = dir_count;
		const char *pathname = F_PATHNAME(file);
		int32 *dp;

		flist = flist_new(0, "send_extra_file_list");
		start_write = stats.total_written;

		if (unsort_ndx)
			dir_ndx = F_NDX(file);
		else
			dir_ndx = send_dir_ndx;
		write_ndx(f, NDX_FLIST_OFFSET - dir_ndx);
		flist->parent_ndx = dir_ndx;

		send1extra(f, file, flist);
		prev_flags = file->flags;
		dp = F_DIR_NODE_P(file);

		/* If there are any duplicate directory names that follow, we
		 * send all the dirs together in one file-list.  The dir_flist
		 * tree links all the child subdirs onto the last dup dir. */
		while ((dir_ndx = DIR_NEXT_SIBLING(dp)) >= 0
		    && dir_flist->sorted[dir_ndx]->flags & FLAG_DUPLICATE) {
			send_dir_ndx = dir_ndx;
			file = dir_flist->sorted[dir_ndx];
			/* Try to avoid some duplicate scanning of identical dirs. */
			if (F_PATHNAME(file) == pathname && prev_flags & FLAG_CONTENT_DIR)
				file->flags &= ~FLAG_CONTENT_DIR;
			send1extra(f, file, flist);
			prev_flags = file->flags;
			dp = F_DIR_NODE_P(file);
		}

		if (io_error == save_io_error || ignore_errors)
			write_byte(f, 0);
		else if (use_safe_inc_flist) {
			write_shortint(f, XMIT_EXTENDED_FLAGS|XMIT_IO_ERROR_ENDLIST);
			write_varint(f, io_error);
		} else {
			if (delete_during)
				fatal_unsafe_io_error();
			write_byte(f, 0);
		}

		if (need_unsorted_flist) {
			if (!(flist->sorted = new_array(struct file_struct *, flist->used)))
				out_of_memory("send_extra_file_list");
			memcpy(flist->sorted, flist->files,
			       flist->used * sizeof (struct file_struct*));
		} else
			flist->sorted = flist->files;

		flist_sort_and_clean(flist, 0);

		add_dirs_to_tree(send_dir_ndx, flist, dir_count - dstart);
		flist_done_allocating(flist);

		file_total += flist->used;
		stats.flist_size += stats.total_written - start_write;
		stats.num_files += flist->used;
		if (verbose > 3)
			output_flist(flist);

		if (DIR_FIRST_CHILD(dp) >= 0) {
			send_dir_ndx = DIR_FIRST_CHILD(dp);
			send_dir_depth++;
		} else {
			while (DIR_NEXT_SIBLING(dp) < 0) {
				if ((send_dir_ndx = DIR_PARENT(dp)) < 0) {
					write_ndx(f, NDX_FLIST_EOF);
					flist_eof = 1;
					change_local_filter_dir(NULL, 0, 0);
					goto finish;
				}
				send_dir_depth--;
				file = dir_flist->sorted[send_dir_ndx];
				dp = F_DIR_NODE_P(file);
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
	static const char *lastdir;
	static int lastdir_len = -1;
	int len, dirlen;
	STRUCT_STAT st;
	char *p, *dir;
	struct file_list *flist;
	struct timeval start_tv, end_tv;
	int64 start_write;
	int use_ff_fd = 0;
	int disable_buffering;
	int flags = recurse ? FLAG_CONTENT_DIR : 0;
	int reading_remotely = filesfrom_host != NULL;
	int rl_flags = (reading_remotely ? 0 : RL_DUMP_COMMENTS)
#ifdef ICONV_OPTION
		     | (filesfrom_convert ? RL_CONVERT : 0)
#endif
		     | (eol_nulls || reading_remotely ? RL_EOL_NULLS : 0);
	int implied_dot_dir = 0;

	rprintf(FLOG, "building file list\n");
	if (show_filelist_p())
		start_filelist_progress("building file list");
	else if (inc_recurse && verbose && !am_server)
		rprintf(FCLIENT, "sending incremental file list\n");

	start_write = stats.total_written;
	gettimeofday(&start_tv, NULL);

	if (relative_paths && protocol_version >= 30)
		implied_dirs = 1; /* We send flagged implied dirs */

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version >= 30 && !cur_flist)
		init_hard_links();
#endif

	flist = cur_flist = flist_new(0, "send_file_list");
	if (inc_recurse) {
		dir_flist = flist_new(FLIST_TEMP, "send_file_list");
		flags |= FLAG_DIVERT_DIRS;
	} else
		dir_flist = cur_flist;

	disable_buffering = io_start_buffering_out(f);
	if (filesfrom_fd >= 0) {
		if (argv[0] && !change_dir(argv[0], CD_NORMAL)) {
			rsyserr(FERROR_XFER, errno, "change_dir %s failed",
				full_fname(argv[0]));
			exit_cleanup(RERR_FILESELECT);
		}
		use_ff_fd = 1;
	}

	if (!orig_dir)
		orig_dir = strdup(curr_dir);

	while (1) {
		char fbuf[MAXPATHLEN], *fn, name_type;

		if (use_ff_fd) {
			if (read_line(filesfrom_fd, fbuf, sizeof fbuf, rl_flags) == 0)
				break;
			sanitize_path(fbuf, fbuf, "", 0, SP_KEEP_DOT_DIRS);
		} else {
			if (argc-- == 0)
				break;
			strlcpy(fbuf, *argv++, MAXPATHLEN);
			if (sanitize_paths)
				sanitize_path(fbuf, fbuf, "", 0, SP_KEEP_DOT_DIRS);
		}

		len = strlen(fbuf);
		if (relative_paths) {
			/* We clean up fbuf below. */
			name_type = NORMAL_NAME;
		} else if (!len || fbuf[len - 1] == '/') {
			if (len == 2 && fbuf[0] == '.') {
				/* Turn "./" into just "." rather than "./." */
				fbuf[--len] = '\0';
			} else {
				if (len + 1 >= MAXPATHLEN)
					overflow_exit("send_file_list");
				fbuf[len++] = '.';
				fbuf[len] = '\0';
			}
			name_type = DOTDIR_NAME;
		} else if (len > 1 && fbuf[len-1] == '.' && fbuf[len-2] == '.'
		    && (len == 2 || fbuf[len-3] == '/')) {
			if (len + 2 >= MAXPATHLEN)
				overflow_exit("send_file_list");
			fbuf[len++] = '/';
			fbuf[len++] = '.';
			fbuf[len] = '\0';
			name_type = DOTDIR_NAME;
		} else if (fbuf[len-1] == '.' && (len == 1 || fbuf[len-2] == '/'))
			name_type = DOTDIR_NAME;
		else
			name_type = NORMAL_NAME;

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
				else {
					dir = fbuf;
					clean_fname(dir, 0);
				}
				fn = p + 3;
				while (*fn == '/')
					fn++;
				if (!*fn)
					*--fn = '\0'; /* ensure room for '.' */
			} else
				fn = fbuf;
			/* A leading ./ can be used in relative mode to affect
			 * the dest dir without its name being in the path. */
			if (*fn == '.' && fn[1] == '/' && !implied_dot_dir) {
				send_file_name(f, flist, ".", NULL,
				    (flags | FLAG_IMPLIED_DIR) & ~FLAG_CONTENT_DIR,
				    ALL_FILTERS);
				implied_dot_dir = 1;
			}
			len = clean_fname(fn, CFN_KEEP_TRAILING_SLASH
					    | CFN_DROP_TRAILING_DOT_DIR);
			if (len == 1) {
				if (fn[0] == '/') {
					fn = "/.";
					len = 2;
					name_type = DOTDIR_NAME;
				} else if (fn[0] == '.')
					name_type = DOTDIR_NAME;
			} else if (fn[len-1] == '/') {
				fn[--len] = '\0';
				if (len == 1 && *fn == '.')
					name_type = DOTDIR_NAME;
				else
					name_type = SLASH_ENDING_NAME;
			}
			/* Reject a ".." dir in the active part of the path. */
			for (p = fn; (p = strstr(p, "..")) != NULL; p += 2) {
				if ((p[2] == '/' || p[2] == '\0')
				 && (p == fn || p[-1] == '/')) {
					rprintf(FERROR,
					    "found \"..\" dir in relative path: %s\n",
					    fn);
					exit_cleanup(RERR_SYNTAX);
				}
			}
		}

		if (!*fn) {
			len = 1;
			fn = ".";
			name_type = DOTDIR_NAME;
		}

		dirlen = dir ? strlen(dir) : 0;
		if (dirlen != lastdir_len || memcmp(lastdir, dir, dirlen) != 0) {
			if (!change_pathname(NULL, dir, -dirlen))
				continue;
			lastdir = pathname;
			lastdir_len = pathname_len;
		} else if (!change_pathname(NULL, lastdir, lastdir_len))
			continue;

		if (fn != fbuf)
			memmove(fbuf, fn, len + 1);

		if (link_stat(fbuf, &st, copy_dirlinks || name_type != NORMAL_NAME) != 0
		 || (name_type != DOTDIR_NAME && is_daemon_excluded(fbuf, S_ISDIR(st.st_mode)))
		 || (relative_paths && path_is_daemon_excluded(fbuf, 1))) {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR_XFER, errno, "link_stat %s failed",
				full_fname(fbuf));
			continue;
		}

		/* A dot-dir should not be excluded! */
		if (name_type != DOTDIR_NAME
		 && is_excluded(fbuf, S_ISDIR(st.st_mode) != 0, ALL_FILTERS))
			continue;

		if (S_ISDIR(st.st_mode) && !xfer_dirs) {
			rprintf(FINFO, "skipping directory %s\n", fbuf);
			continue;
		}

		if (inc_recurse && relative_paths && *fbuf) {
			if ((p = strchr(fbuf+1, '/')) != NULL) {
				if (p - fbuf == 1 && *fbuf == '.') {
					if ((fn = strchr(p+1, '/')) != NULL)
						p = fn;
				} else
					fn = p;
				send_implied_dirs(f, flist, fbuf, fbuf, p, flags, name_type);
				if (fn == p)
					continue;
			}
		} else if (implied_dirs && (p=strrchr(fbuf,'/')) && p != fbuf) {
			/* Send the implied directories at the start of the
			 * source spec, so we get their permissions right. */
			send_implied_dirs(f, flist, fbuf, fbuf, p, flags, 0);
		}

		if (one_file_system)
			filesystem_dev = st.st_dev;

		if (recurse || (xfer_dirs && name_type != NORMAL_NAME)) {
			struct file_struct *file;
			file = send_file_name(f, flist, fbuf, &st,
					      FLAG_TOP_DIR | FLAG_CONTENT_DIR | flags,
					      NO_FILTERS);
			if (!file)
				continue;
			if (inc_recurse) {
				if (name_type == DOTDIR_NAME) {
					if (send_dir_depth < 0) {
						send_dir_depth = 0;
						change_local_filter_dir(fbuf, len, send_dir_depth);
					}
					send_directory(f, flist, fbuf, len, flags);
				}
			} else
				send_if_directory(f, flist, file, fbuf, len, flags);
		} else
			send_file_name(f, flist, fbuf, &st, flags, NO_FILTERS);
	}

	gettimeofday(&end_tv, NULL);
	stats.flist_buildtime = (int64)(end_tv.tv_sec - start_tv.tv_sec) * 1000
			      + (end_tv.tv_usec - start_tv.tv_usec) / 1000;
	if (stats.flist_buildtime == 0)
		stats.flist_buildtime = 1;
	start_tv = end_tv;

	/* Indicate end of file list */
	if (io_error == 0 || ignore_errors)
		write_byte(f, 0);
	else if (use_safe_inc_flist) {
		write_shortint(f, XMIT_EXTENDED_FLAGS|XMIT_IO_ERROR_ENDLIST);
		write_varint(f, io_error);
	} else {
		if (delete_during && inc_recurse)
			fatal_unsafe_io_error();
		write_byte(f, 0);
	}

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version >= 30 && !inc_recurse)
		idev_destroy();
#endif

	if (show_filelist_p())
		finish_filelist_progress(flist);

	gettimeofday(&end_tv, NULL);
	stats.flist_xfertime = (int64)(end_tv.tv_sec - start_tv.tv_sec) * 1000
			     + (end_tv.tv_usec - start_tv.tv_usec) / 1000;

	/* When converting names, both sides keep an unsorted file-list array
	 * because the names will differ on the sending and receiving sides
	 * (both sides will use the unsorted index number for each item). */

	/* Sort the list without removing any duplicates.  This allows the
	 * receiving side to ask for whatever name it kept.  For incremental
	 * recursion mode, the sender marks duplicate dirs so that it can
	 * send them together in a single file-list. */
	if (need_unsorted_flist) {
		if (!(flist->sorted = new_array(struct file_struct *, flist->used)))
			out_of_memory("send_file_list");
		memcpy(flist->sorted, flist->files,
		       flist->used * sizeof (struct file_struct*));
	} else
		flist->sorted = flist->files;
	flist_sort_and_clean(flist, 0);
	file_total += flist->used;

	if (numeric_ids <= 0 && !inc_recurse)
		send_id_list(f);

	set_msg_fd_in(-1);

	/* send the io_error flag */
	if (protocol_version < 30)
		write_int(f, ignore_errors ? 0 : io_error);
	else if (!use_safe_inc_flist && io_error && !ignore_errors)
		send_msg_int(MSG_IO_ERROR, io_error);

	if (disable_buffering)
		io_end_buffering_out();

	stats.flist_size = stats.total_written - start_write;
	stats.num_files = flist->used;

	if (verbose > 3)
		output_flist(flist);

	if (verbose > 2)
		rprintf(FINFO, "send_file_list done\n");

	if (inc_recurse) {
		send_dir_depth = 1;
		add_dirs_to_tree(-1, flist, dir_count);
		if (!file_total || strcmp(flist->sorted[flist->low]->basename, ".") != 0)
			flist->parent_ndx = -1;
		flist_done_allocating(flist);
		if (send_dir_ndx < 0) {
			write_ndx(f, NDX_FLIST_EOF);
			flist_eof = 1;
		}
		else if (file_total == 1) {
			/* If we're creating incremental file-lists and there
			 * was just 1 item in the first file-list, send 1 more
			 * file-list to check if this is a 1-file xfer. */
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
	int save_verbose = verbose;

	if (!first_flist)
		rprintf(FLOG, "receiving file list\n");
	if (show_filelist_p())
		start_filelist_progress("receiving file list");
	else if (inc_recurse && verbose && !am_server && !first_flist)
		rprintf(FCLIENT, "receiving incremental file list\n");

	start_read = stats.total_read;

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && !first_flist)
		init_hard_links();
#endif

	flist = flist_new(0, "recv_file_list");

	if (inc_recurse) {
		if (flist->ndx_start == 1)
			dir_flist = flist_new(FLIST_TEMP, "recv_file_list");
		dstart = dir_flist->used;
	} else {
		dir_flist = flist;
		dstart = 0;
	}

	if (am_server && verbose > 2)
		verbose = 2;
	while ((flags = read_byte(f)) != 0) {
		struct file_struct *file;

		if (protocol_version >= 28 && (flags & XMIT_EXTENDED_FLAGS))
			flags |= read_byte(f) << 8;

		if (flags == (XMIT_EXTENDED_FLAGS|XMIT_IO_ERROR_ENDLIST)) {
			int err;
			if (!use_safe_inc_flist) {
				rprintf(FERROR, "Invalid flist flag: %x\n", flags);
				exit_cleanup(RERR_PROTOCOL);
			}
			err = read_varint(f);
			if (!ignore_errors)
				io_error |= err;
			break;
		}

		flist_expand(flist, 1);
		file = recv_file_entry(f, flist, flags);

		if (inc_recurse && S_ISDIR(file->mode)) {
			flist_expand(dir_flist, 1);
			dir_flist->files[dir_flist->used++] = file;
		}

		flist->files[flist->used++] = file;

		maybe_emit_filelist_progress(flist->used);

		if (verbose > 2) {
			char *name = f_name(file, NULL);
			rprintf(FINFO, "recv_file_name(%s)\n", NS(name));
		}
	}
	file_total += flist->used;
	verbose = save_verbose;

	if (verbose > 2)
		rprintf(FINFO, "received %d names\n", flist->used);

	if (show_filelist_p())
		finish_filelist_progress(flist);

	if (need_unsorted_flist) {
		/* Create an extra array of index pointers that we can sort for
		 * the generator's use (for wading through the files in sorted
		 * order and for calling flist_find()).  We keep the "files"
		 * list unsorted for our exchange of index numbers with the
		 * other side (since their names may not sort the same). */
		if (!(flist->sorted = new_array(struct file_struct *, flist->used)))
			out_of_memory("recv_file_list");
		memcpy(flist->sorted, flist->files,
		       flist->used * sizeof (struct file_struct*));
		if (inc_recurse && dir_flist->used > dstart) {
			static int dir_flist_malloced = 0;
			if (dir_flist_malloced < dir_flist->malloced) {
				dir_flist->sorted = realloc_array(dir_flist->sorted,
							struct file_struct *,
							dir_flist->malloced);
				dir_flist_malloced = dir_flist->malloced;
			}
			memcpy(dir_flist->sorted + dstart, dir_flist->files + dstart,
			       (dir_flist->used - dstart) * sizeof (struct file_struct*));
			fsort(dir_flist->sorted + dstart, dir_flist->used - dstart);
		}
	} else {
		flist->sorted = flist->files;
		if (inc_recurse && dir_flist->used > dstart) {
			dir_flist->sorted = dir_flist->files;
			fsort(dir_flist->sorted + dstart, dir_flist->used - dstart);
		}
	}

	if (inc_recurse)
		flist_done_allocating(flist);
	else if (f >= 0)
		recv_id_list(f, flist);

	flist_sort_and_clean(flist, relative_paths);

	if (protocol_version < 30) {
		/* Recv the io_error flag */
		if (ignore_errors)
			read_int(f);
		else
			io_error |= read_int(f);
	} else if (inc_recurse && flist->ndx_start == 1) {
		if (!file_total || strcmp(flist->sorted[flist->low]->basename, ".") != 0)
			flist->parent_ndx = -1;
	}

	if (verbose > 3)
		output_flist(flist);

	if (verbose > 2)
		rprintf(FINFO, "recv_file_list done\n");

	stats.flist_size += stats.total_read - start_read;
	stats.num_files += flist->used;

	return flist;
}

/* This is only used once by the receiver if the very first file-list
 * has exactly one item in it. */
void recv_additional_file_list(int f)
{
	struct file_list *flist;
	int ndx = read_ndx(f);
	if (ndx == NDX_FLIST_EOF) {
		flist_eof = 1;
		change_local_filter_dir(NULL, 0, 0);
	} else {
		ndx = NDX_FLIST_OFFSET - ndx;
		if (ndx < 0 || ndx >= dir_flist->used) {
			ndx = NDX_FLIST_OFFSET - ndx;
			rprintf(FERROR,
				"[%s] Invalid dir index: %d (%d - %d)\n",
				who_am_i(), ndx, NDX_FLIST_OFFSET,
				NDX_FLIST_OFFSET - dir_flist->used + 1);
			exit_cleanup(RERR_PROTOCOL);
		}
		if (verbose > 3) {
			rprintf(FINFO, "[%s] receiving flist for dir %d\n",
				who_am_i(), ndx);
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
		if (F_IS_ACTIVE(flist->sorted[mid]))
			mid_up = mid;
		else {
			/* Scan for the next non-empty entry using the cached
			 * distance values.  If the value isn't fully up-to-
			 * date, update it. */
			mid_up = mid + F_DEPTH(flist->sorted[mid]);
			if (!F_IS_ACTIVE(flist->sorted[mid_up])) {
				do {
				    mid_up += F_DEPTH(flist->sorted[mid_up]);
				} while (!F_IS_ACTIVE(flist->sorted[mid_up]));
				F_DEPTH(flist->sorted[mid]) = mid_up - mid;
			}
			if (mid_up > high) {
				/* If there's nothing left above us, set high to
				 * a non-empty entry below us and continue. */
				high = mid - (int)flist->sorted[mid]->len32;
				if (!F_IS_ACTIVE(flist->sorted[high])) {
					do {
					    high -= (int)flist->sorted[high]->len32;
					} while (!F_IS_ACTIVE(flist->sorted[high]));
					flist->sorted[mid]->len32 = mid - high;
				}
				continue;
			}
		}
		diff = f_name_cmp(flist->sorted[mid_up], f);
		if (diff == 0) {
			if (protocol_version < 29
			    && S_ISDIR(flist->sorted[mid_up]->mode)
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

/* Search for an identically-named item in the file list.  Differs from
 * flist_find in that an item that agrees with "f" in directory-ness is
 * preferred but one that does not is still found. */
int flist_find_ignore_dirness(struct file_list *flist, struct file_struct *f)
{
	mode_t save_mode;
	int ndx;

	/* First look for an item that agrees in directory-ness. */
	ndx = flist_find(flist, f);
	if (ndx >= 0)
		return ndx;

	/* Temporarily flip f->mode to look for an item of opposite
	 * directory-ness. */
	save_mode = f->mode;
	f->mode = S_ISDIR(f->mode) ? S_IFREG : S_IFDIR;
	ndx = flist_find(flist, f);
	f->mode = save_mode;
	return ndx;
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

	if (!(flist = new0(struct file_list)))
		out_of_memory(msg);

	if (flags & FLIST_TEMP) {
		if (!(flist->file_pool = pool_create(SMALL_EXTENT, 0,
						out_of_memory, POOL_INTERN)))
			out_of_memory(msg);
	} else {
		/* This is a doubly linked list with prev looping back to
		 * the end of the list, but the last next pointer is NULL. */
		if (!first_flist) {
			flist->file_pool = pool_create(NORMAL_EXTENT, 0,
						out_of_memory, POOL_INTERN);
			if (!flist->file_pool)
				out_of_memory(msg);

			flist->ndx_start = flist->flist_num = inc_recurse ? 1 : 0;

			first_flist = cur_flist = flist->prev = flist;
		} else {
			struct file_list *prev = first_flist->prev;

			flist->file_pool = first_flist->file_pool;

			flist->ndx_start = prev->ndx_start + prev->used + 1;
			flist->flist_num = prev->flist_num + 1;

			flist->prev = prev;
			prev->next = first_flist->prev = flist;
		}
		flist->pool_boundary = pool_boundary(flist->file_pool, 0);
		flist_cnt++;
	}

	return flist;
}

/* Free up all elements in a flist. */
void flist_free(struct file_list *flist)
{
	if (!flist->prev) {
		/* Was FLIST_TEMP dir-list. */
	} else if (flist == flist->prev) {
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
		file_total -= flist->used;
		flist_cnt--;
	}

	if (!flist->prev || !flist_cnt)
		pool_destroy(flist->file_pool);
	else
		pool_free_old(flist->file_pool, flist->pool_boundary);

	if (flist->sorted && flist->sorted != flist->files)
		free(flist->sorted);
	free(flist->files);
	free(flist);
}

/* This routine ensures we don't have any duplicate names in our file list.
 * duplicate names can cause corruption because of the pipelining. */
static void flist_sort_and_clean(struct file_list *flist, int strip_root)
{
	char fbuf[MAXPATHLEN];
	int i, prev_i;

	if (!flist)
		return;
	if (flist->used == 0) {
		flist->high = -1;
		flist->low = 0;
		return;
	}

	fsort(flist->sorted, flist->used);

	if (!am_sender || inc_recurse) {
		for (i = prev_i = 0; i < flist->used; i++) {
			if (F_IS_ACTIVE(flist->sorted[i])) {
				prev_i = i;
				break;
			}
		}
		flist->low = prev_i;
	} else {
		i = prev_i = flist->used - 1;
		flist->low = 0;
	}

	while (++i < flist->used) {
		int j;
		struct file_struct *file = flist->sorted[i];

		if (!F_IS_ACTIVE(file))
			continue;
		if (f_name_cmp(file, flist->sorted[prev_i]) == 0)
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
			int keep, drop;
			/* If one is a dir and the other is not, we want to
			 * keep the dir because it might have contents in the
			 * list.  Otherwise keep the first one. */
			if (S_ISDIR(file->mode)) {
				struct file_struct *fp = flist->sorted[j];
				if (!S_ISDIR(fp->mode))
					keep = i, drop = j;
				else {
					if (am_sender)
						file->flags |= FLAG_DUPLICATE;
					else { /* Make sure we merge our vital flags. */
						fp->flags |= file->flags & (FLAG_TOP_DIR|FLAG_CONTENT_DIR);
						fp->flags &= file->flags | ~FLAG_IMPLIED_DIR;
					}
					keep = j, drop = i;
				}
			} else
				keep = j, drop = i;

			if (!am_sender) {
				if (verbose > 1) {
					rprintf(FINFO,
					    "removing duplicate name %s from file list (%d)\n",
					    f_name(file, fbuf), drop + flist->ndx_start);
				}
				clear_file(flist->sorted[drop]);
			}

			if (keep == i) {
				if (flist->low == drop) {
					for (j = drop + 1;
					     j < i && !F_IS_ACTIVE(flist->sorted[j]);
					     j++) {}
					flist->low = j;
				}
				prev_i = i;
			}
		} else
			prev_i = i;
	}
	flist->high = prev_i;

	if (strip_root) {
		/* We need to strip off the leading slashes for relative
		 * paths, but this must be done _after_ the sorting phase. */
		for (i = flist->low; i <= flist->high; i++) {
			struct file_struct *file = flist->sorted[i];

			if (!file->dirname)
				continue;
			while (*file->dirname == '/')
				file->dirname++;
			if (!*file->dirname)
				file->dirname = NULL;
		}
	}

	if (prune_empty_dirs && !am_sender) {
		int j, prev_depth = 0;

		prev_i = 0; /* It's OK that this isn't really true. */

		for (i = flist->low; i <= flist->high; i++) {
			struct file_struct *fp, *file = flist->sorted[i];

			/* This temporarily abuses the F_DEPTH() value for a
			 * directory that is in a chain that might get pruned.
			 * We restore the old value if it gets a reprieve. */
			if (S_ISDIR(file->mode) && F_DEPTH(file)) {
				/* Dump empty dirs when coming back down. */
				for (j = prev_depth; j >= F_DEPTH(file); j--) {
					fp = flist->sorted[prev_i];
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
						fp = flist->sorted[prev_i];
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
					fp = flist->sorted[prev_i];
					if (F_DEPTH(fp) >= 0)
						break;
					prev_i = -F_DEPTH(fp)-1;
					F_DEPTH(fp) = j;
				}
			}
		}
		/* Dump all remaining empty dirs. */
		while (1) {
			struct file_struct *fp = flist->sorted[prev_i];
			if (F_DEPTH(fp) >= 0)
				break;
			prev_i = -F_DEPTH(fp)-1;
			clear_file(fp);
		}

		for (i = flist->low; i <= flist->high; i++) {
			if (F_IS_ACTIVE(flist->sorted[i]))
				break;
		}
		flist->low = i;
		for (i = flist->high; i >= flist->low; i--) {
			if (F_IS_ACTIVE(flist->sorted[i]))
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

	rprintf(FINFO, "[%s] flist start=%d, used=%d, low=%d, high=%d\n",
		who, flist->ndx_start, flist->used, flist->low, flist->high);
	for (i = 0; i < flist->used; i++) {
		file = flist->files[i];
		if ((am_root || am_sender) && uid_ndx) {
			snprintf(uidbuf, sizeof uidbuf, " uid=%u",
				 F_OWNER(file));
		} else
			*uidbuf = '\0';
		if (gid_ndx) {
			static char parens[] = "(\0)\0\0\0";
			char *pp = parens + (file->flags & FLAG_SKIP_GROUP ? 0 : 3);
			snprintf(gidbuf, sizeof gidbuf, " gid=%s%u%s",
				 pp, F_GROUP(file), pp + 2);
		} else
			*gidbuf = '\0';
		if (!am_sender)
			snprintf(depthbuf, sizeof depthbuf, "%d", F_DEPTH(file));
		if (F_IS_ACTIVE(file)) {
			root = am_sender ? NS(F_PATHNAME(file)) : depthbuf;
			if ((dir = file->dirname) == NULL)
				dir = slash = "";
			else
				slash = "/";
			name = file->basename;
			trail = S_ISDIR(file->mode) ? "/" : "";
		} else
			root = dir = slash = name = trail = "";
		rprintf(FINFO,
			"[%s] i=%d %s %s%s%s%s mode=0%o len=%.0f%s%s flags=%x\n",
			who, i + flist->ndx_start,
			root, dir, slash, name, trail,
			(int)file->mode, (double)F_LENGTH(file),
			uidbuf, gidbuf, file->flags);
	}
}

enum fnc_state { s_DIR, s_SLASH, s_BASE, s_TRAILING };
enum fnc_type { t_PATH, t_ITEM };

static int found_prefix;

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
int f_name_cmp(const struct file_struct *f1, const struct file_struct *f2)
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
				found_prefix = 1;
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

/* Returns 1 if f1's filename has all of f2's filename as a prefix.  This does
 * not match if f2's basename is not an exact match of a path element in f1.
 * E.g. /path/foo is not a prefix of /path/foobar/baz, but /path/foobar is. */
int f_name_has_prefix(const struct file_struct *f1, const struct file_struct *f2)
{
	found_prefix = 0;
	f_name_cmp(f1, f2);
	return found_prefix;
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
char *f_name(const struct file_struct *f, char *fbuf)
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
struct file_list *get_dirlist(char *dirname, int dlen, int flags)
{
	struct file_list *dirlist;
	char dirbuf[MAXPATHLEN];
	int save_recurse = recurse;
	int save_xfer_dirs = xfer_dirs;
	int save_prune_empty_dirs = prune_empty_dirs;
	int senddir_fd = flags & GDL_IGNORE_FILTER_RULES ? -2 : -1;

	if (dlen < 0) {
		dlen = strlcpy(dirbuf, dirname, MAXPATHLEN);
		if (dlen >= MAXPATHLEN)
			return NULL;
		dirname = dirbuf;
	}

	dirlist = flist_new(FLIST_TEMP, "get_dirlist");

	recurse = 0;
	xfer_dirs = 1;
	send_directory(senddir_fd, dirlist, dirname, dlen, FLAG_CONTENT_DIR);
	xfer_dirs = save_xfer_dirs;
	recurse = save_recurse;
	if (do_progress)
		flist_count_offset += dirlist->used;

	prune_empty_dirs = 0;
	dirlist->sorted = dirlist->files;
	flist_sort_and_clean(dirlist, 0);
	prune_empty_dirs = save_prune_empty_dirs;

	if (verbose > 3)
		output_flist(dirlist);

	return dirlist;
}
