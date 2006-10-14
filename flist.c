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

extern int verbose;
extern int list_only;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int am_sender;
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
extern int prune_empty_dirs;
extern int copy_links;
extern int copy_unsafe_links;
extern int protocol_version;
extern int sanitize_paths;
extern struct stats stats;
extern struct file_list *the_file_list;

extern char curr_dir[MAXPATHLEN];

extern struct chmod_mode_struct *chmod_modes;

extern struct filter_list_struct filter_list;
extern struct filter_list_struct server_filter_list;

int io_error;
int checksum_len;
dev_t filesystem_dev; /* used to implement -x */
unsigned int file_struct_len;

static char empty_sum[MD4_SUM_LENGTH];
static int flist_count_offset;

static void clean_flist(struct file_list *flist, int strip_root, int no_dups);
static void output_flist(struct file_list *flist);

void init_flist(void)
{
	struct file_struct f;

	/* Figure out how big the file_struct is without trailing padding */
	file_struct_len = offsetof(struct file_struct, flags) + sizeof f.flags;
	checksum_len = protocol_version < 21 ? 2 : MD4_SUM_LENGTH;
}

static int show_filelist_p(void)
{
	return verbose && xfer_dirs && !am_server;
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

	if (!f->basename) {
		/* this can happen if duplicate names were removed */
		return;
	}

	permstring(permbuf, f->mode);

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(f->mode)) {
		rprintf(FINFO, "%s %11.0f %s %s -> %s\n",
			permbuf,
			(double)f->length, timestring(f->modtime),
			f_name(f, NULL), f->u.link);
	} else
#endif
	{
		rprintf(FINFO, "%s %11.0f %s %s\n",
			permbuf,
			(double)f->length, timestring(f->modtime),
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

static void send_directory(int f, struct file_list *flist,
			   char *fbuf, int len);

static char *flist_dir;
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

static void send_file_entry(struct file_struct *file, int f)
{
	unsigned short flags;
	static time_t modtime;
	static mode_t mode;
	static int64 dev;
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static char lastname[MAXPATHLEN];
	char fname[MAXPATHLEN];
	int l1, l2;

	if (f < 0)
		return;

	if (!file) {
		write_byte(f, 0);
		modtime = 0, mode = 0;
		dev = 0, rdev = MAKEDEV(0, 0);
		rdev_major = 0;
		uid = 0, gid = 0;
		*lastname = '\0';
		return;
	}

	f_name(file, fname);

	flags = file->flags & XMIT_TOP_DIR;

	if (file->mode == mode)
		flags |= XMIT_SAME_MODE;
	else
		mode = file->mode;
	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode))) {
		if (protocol_version < 28) {
			if (file->u.rdev == rdev)
				flags |= XMIT_SAME_RDEV_pre28;
			else
				rdev = file->u.rdev;
		} else {
			rdev = file->u.rdev;
			if ((uint32)major(rdev) == rdev_major)
				flags |= XMIT_SAME_RDEV_MAJOR;
			else
				rdev_major = major(rdev);
			if ((uint32)minor(rdev) <= 0xFFu)
				flags |= XMIT_RDEV_MINOR_IS_SMALL;
		}
	} else if (protocol_version < 28)
		rdev = MAKEDEV(0, 0);
	if (file->uid == uid)
		flags |= XMIT_SAME_UID;
	else
		uid = file->uid;
	if (file->gid == gid)
		flags |= XMIT_SAME_GID;
	else
		gid = file->gid;
	if (file->modtime == modtime)
		flags |= XMIT_SAME_TIME;
	else
		modtime = file->modtime;

#ifdef SUPPORT_HARD_LINKS
	if (file->link_u.idev) {
		if (file->F_DEV == dev) {
			if (protocol_version >= 28)
				flags |= XMIT_SAME_DEV;
		} else
			dev = file->F_DEV;
		flags |= XMIT_HAS_IDEV_DATA;
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
			write_byte(f, flags);
			write_byte(f, flags >> 8);
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

	write_longint(f, file->length);
	if (!(flags & XMIT_SAME_TIME))
		write_int(f, modtime);
	if (!(flags & XMIT_SAME_MODE))
		write_int(f, to_wire_mode(mode));
	if (preserve_uid && !(flags & XMIT_SAME_UID)) {
		if (!numeric_ids)
			add_uid(uid);
		write_int(f, uid);
	}
	if (preserve_gid && !(flags & XMIT_SAME_GID)) {
		if (!numeric_ids)
			add_gid(gid);
		write_int(f, gid);
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
		int len = strlen(file->u.link);
		write_int(f, len);
		write_buf(f, file->u.link, len);
	}
#endif

#ifdef SUPPORT_HARD_LINKS
	if (file->link_u.idev) {
		if (protocol_version < 26) {
			/* 32-bit dev_t and ino_t */
			write_int(f, dev);
			write_int(f, file->F_INODE);
		} else {
			/* 64-bit dev_t and ino_t */
			if (!(flags & XMIT_SAME_DEV))
				write_longint(f, dev);
			write_longint(f, file->F_INODE);
		}
	}
#endif

	if (always_checksum && (S_ISREG(mode) || protocol_version < 28)) {
		char *sum;
		if (S_ISREG(mode))
			sum = file->u.sum;
		else {
			/* Prior to 28, we sent a useless set of nulls. */
			sum = empty_sum;
		}
		write_buf(f, sum, checksum_len);
	}

	strlcpy(lastname, fname, MAXPATHLEN);
}

static struct file_struct *receive_file_entry(struct file_list *flist,
					      unsigned short flags, int f)
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
	int alloc_len, basename_len, dirname_len, linkname_len, sum_len;
	OFF_T file_length;
	char *basename, *dirname, *bp;
	struct file_struct *file;

	if (!flist) {
		modtime = 0, mode = 0;
		dev = 0, rdev = MAKEDEV(0, 0);
		rdev_major = 0;
		uid = 0, gid = 0;
		*lastname = '\0';
		lastdir_len = -1;
		in_del_hier = 0;
		return NULL;
	}

	if (flags & XMIT_SAME_NAME)
		l1 = read_byte(f);

	if (flags & XMIT_LONG_NAME)
		l2 = read_int(f);
	else
		l2 = read_byte(f);

	if (l2 >= MAXPATHLEN - l1) {
		rprintf(FERROR,
			"overflow: flags=0x%x l1=%d l2=%d lastname=%s\n",
			flags, l1, l2, lastname);
		overflow_exit("receive_file_entry");
	}

	strlcpy(thisname, lastname, l1 + 1);
	read_sbuf(f, &thisname[l1], l2);
	thisname[l1 + l2] = 0;

	strlcpy(lastname, thisname, MAXPATHLEN);

	clean_fname(thisname, 0);

	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0, NULL);

	if ((basename = strrchr(thisname, '/')) != NULL) {
		dirname_len = ++basename - thisname; /* counts future '\0' */
		if (lastdir_len == dirname_len - 1
		    && strncmp(thisname, lastdir, lastdir_len) == 0) {
			dirname = lastdir;
			dirname_len = 0; /* indicates no copy is needed */
		} else
			dirname = thisname;
	} else {
		basename = thisname;
		dirname = NULL;
		dirname_len = 0;
	}
	basename_len = strlen(basename) + 1; /* count the '\0' */

	file_length = read_longint(f);
	if (!(flags & XMIT_SAME_TIME))
		modtime = (time_t)read_int(f);
	if (!(flags & XMIT_SAME_MODE))
		mode = from_wire_mode(read_int(f));

	if (chmod_modes && !S_ISLNK(mode))
		mode = tweak_mode(mode, chmod_modes);

	if (preserve_uid && !(flags & XMIT_SAME_UID))
		uid = (uid_t)read_int(f);
	if (preserve_gid && !(flags & XMIT_SAME_GID))
		gid = (gid_t)read_int(f);

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
	} else if (protocol_version < 28)
		rdev = MAKEDEV(0, 0);

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(mode)) {
		linkname_len = read_int(f) + 1; /* count the '\0' */
		if (linkname_len <= 0 || linkname_len > MAXPATHLEN) {
			rprintf(FERROR, "overflow: linkname_len=%d\n",
				linkname_len - 1);
			overflow_exit("receive_file_entry");
		}
	}
	else
#endif
		linkname_len = 0;

	sum_len = always_checksum && S_ISREG(mode) ? MD4_SUM_LENGTH : 0;

	alloc_len = file_struct_len + dirname_len + basename_len
		  + linkname_len + sum_len;
	bp = pool_alloc(flist->file_pool, alloc_len, "receive_file_entry");

	file = (struct file_struct *)bp;
	memset(bp, 0, file_struct_len);
	bp += file_struct_len;

	file->modtime = modtime;
	file->length = file_length;
	file->mode = mode;
	file->uid = uid;
	file->gid = gid;

	if (dirname_len) {
		file->dirname = lastdir = bp;
		lastdir_len = dirname_len - 1;
		memcpy(bp, dirname, dirname_len - 1);
		bp += dirname_len;
		bp[-1] = '\0';
		lastdir_depth = count_dir_elements(lastdir);
		file->dir.depth = lastdir_depth + 1;
	} else if (dirname) {
		file->dirname = dirname; /* we're reusing lastname */
		file->dir.depth = lastdir_depth + 1;
	} else
		file->dir.depth = 1;

	if (S_ISDIR(mode)) {
		if (basename_len == 1+1 && *basename == '.') /* +1 for '\0' */
			file->dir.depth--;
		if (flags & XMIT_TOP_DIR) {
			in_del_hier = recurse;
			del_hier_name_len = file->dir.depth == 0 ? 0 : l1 + l2;
			if (relative_paths && del_hier_name_len > 2
			    && lastname[del_hier_name_len-1] == '.'
			    && lastname[del_hier_name_len-2] == '/')
				del_hier_name_len -= 2;
			file->flags |= FLAG_TOP_DIR | FLAG_DEL_HERE;
		} else if (in_del_hier) {
			if (!relative_paths || !del_hier_name_len
			 || (l1 >= del_hier_name_len
			  && lastname[del_hier_name_len] == '/'))
				file->flags |= FLAG_DEL_HERE;
			else
				in_del_hier = 0;
		}
	}

	file->basename = bp;
	memcpy(bp, basename, basename_len);
	bp += basename_len;

	if ((preserve_devices && IS_DEVICE(mode))
	 || (preserve_specials && IS_SPECIAL(mode)))
		file->u.rdev = rdev;

#ifdef SUPPORT_LINKS
	if (linkname_len) {
		file->u.link = bp;
		read_sbuf(f, bp, linkname_len - 1);
		if (sanitize_paths)
			sanitize_path(bp, bp, "", lastdir_depth, NULL);
		bp += linkname_len;
	}
#endif

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version < 28 && S_ISREG(mode))
		flags |= XMIT_HAS_IDEV_DATA;
	if (flags & XMIT_HAS_IDEV_DATA) {
		int64 inode;
		if (protocol_version < 26) {
			dev = read_int(f);
			inode = read_int(f);
		} else {
			if (!(flags & XMIT_SAME_DEV))
				dev = read_longint(f);
			inode = read_longint(f);
		}
		if (flist->hlink_pool) {
			file->link_u.idev = pool_talloc(flist->hlink_pool,
			    struct idev, 1, "inode_table");
			file->F_INODE = inode;
			file->F_DEV = dev;
		}
	}
#endif

	if (always_checksum && (sum_len || protocol_version < 28)) {
		char *sum;
		if (sum_len) {
			file->u.sum = sum = bp;
			/*bp += sum_len;*/
		} else {
			/* Prior to 28, we get a useless set of nulls. */
			sum = empty_sum;
		}
		read_buf(f, sum, checksum_len);
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
struct file_struct *make_file(char *fname, struct file_list *flist,
			      STRUCT_STAT *stp, unsigned short flags,
			      int filter_level)
{
	static char *lastdir;
	static int lastdir_len = -1;
	struct file_struct *file;
	STRUCT_STAT st;
	char sum[SUM_LENGTH];
	char thisname[MAXPATHLEN];
	char linkname[MAXPATHLEN];
	int alloc_len, basename_len, dirname_len, linkname_len, sum_len;
	char *basename, *dirname, *bp;

	if (!flist || !flist->count)	/* Ignore lastdir when invalid. */
		lastdir_len = -1;

	if (strlcpy(thisname, fname, sizeof thisname)
	    >= sizeof thisname - flist_dir_len) {
		rprintf(FINFO, "skipping overly long name: %s\n", fname);
		return NULL;
	}
	clean_fname(thisname, 0);
	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0, NULL);

	memset(sum, 0, SUM_LENGTH);

	if (stp && S_ISDIR(stp->st_mode)) {
		st = *stp; /* Needed for "symlink/." with --relative. */
		*linkname = '\0'; /* make IBM code checker happy */
	} else if (readlink_stat(thisname, &st, linkname) != 0) {
		int save_errno = errno;
		/* See if file is excluded before reporting an error. */
		if (filter_level != NO_FILTERS
		    && is_excluded(thisname, 0, filter_level))
			return NULL;
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

	/* We only care about directories because we need to avoid recursing
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
		flags |= FLAG_MOUNT_POINT;
	}

	if (is_excluded(thisname, S_ISDIR(st.st_mode) != 0, filter_level))
		return NULL;

	if (lp_ignore_nonreadable(module_id)) {
#ifdef SUPPORT_LINKS
		if (!S_ISLNK(st.st_mode))
#endif
			if (access(thisname, R_OK) != 0)
				return NULL;
	}

  skip_filters:

	if (verbose > 2) {
		rprintf(FINFO, "[%s] make_file(%s,*,%d)\n",
			who_am_i(), thisname, filter_level);
	}

	if ((basename = strrchr(thisname, '/')) != NULL) {
		dirname_len = ++basename - thisname; /* counts future '\0' */
		if (lastdir_len == dirname_len - 1
		    && strncmp(thisname, lastdir, lastdir_len) == 0) {
			dirname = lastdir;
			dirname_len = 0; /* indicates no copy is needed */
		} else
			dirname = thisname;
	} else {
		basename = thisname;
		dirname = NULL;
		dirname_len = 0;
	}
	basename_len = strlen(basename) + 1; /* count the '\0' */

#ifdef SUPPORT_LINKS
	linkname_len = S_ISLNK(st.st_mode) ? strlen(linkname) + 1 : 0;
#else
	linkname_len = 0;
#endif

	sum_len = always_checksum && am_sender && S_ISREG(st.st_mode)
	        ? MD4_SUM_LENGTH : 0;

	alloc_len = file_struct_len + dirname_len + basename_len
		  + linkname_len + sum_len;
	if (flist)
		bp = pool_alloc(flist->file_pool, alloc_len, "make_file");
	else {
		if (!(bp = new_array(char, alloc_len)))
			out_of_memory("make_file");
	}

	file = (struct file_struct *)bp;
	memset(bp, 0, file_struct_len);
	bp += file_struct_len;

	file->flags = flags;
	file->modtime = st.st_mtime;
	file->length = st.st_size;
	file->mode = st.st_mode;
	file->uid = st.st_uid;
	file->gid = st.st_gid;

#ifdef SUPPORT_HARD_LINKS
	if (flist && flist->hlink_pool) {
		if (protocol_version < 28) {
			if (S_ISREG(st.st_mode))
				file->link_u.idev = pool_talloc(
				    flist->hlink_pool, struct idev, 1,
				    "inode_table");
		} else {
			if (!S_ISDIR(st.st_mode) && st.st_nlink > 1)
				file->link_u.idev = pool_talloc(
				    flist->hlink_pool, struct idev, 1,
				    "inode_table");
		}
	}
	if (file->link_u.idev) {
		file->F_DEV = st.st_dev;
		file->F_INODE = st.st_ino;
	}
#endif

	if (dirname_len) {
		file->dirname = lastdir = bp;
		lastdir_len = dirname_len - 1;
		memcpy(bp, dirname, dirname_len - 1);
		bp += dirname_len;
		bp[-1] = '\0';
	} else if (dirname)
		file->dirname = dirname;

	file->basename = bp;
	memcpy(bp, basename, basename_len);
	bp += basename_len;

#ifdef HAVE_STRUCT_STAT_ST_RDEV
	if ((preserve_devices && IS_DEVICE(st.st_mode))
	 || (preserve_specials && IS_SPECIAL(st.st_mode)))
		file->u.rdev = st.st_rdev;
#endif

#ifdef SUPPORT_LINKS
	if (linkname_len) {
		file->u.link = bp;
		memcpy(bp, linkname, linkname_len);
		bp += linkname_len;
	}
#endif

	if (sum_len) {
		file->u.sum = bp;
		file_checksum(thisname, bp, st.st_size);
		/*bp += sum_len;*/
	}

	file->dir.root = flist_dir;

	/* This code is only used by the receiver when it is building
	 * a list of files for a delete pass. */
	if (keep_dirlinks && linkname_len && flist) {
		STRUCT_STAT st2;
		int save_mode = file->mode;
		file->mode = S_IFDIR; /* Find a directory with our name. */
		if (flist_find(the_file_list, file) >= 0
		    && do_stat(thisname, &st2) == 0 && S_ISDIR(st2.st_mode)) {
			file->modtime = st2.st_mtime;
			file->length = st2.st_size;
			file->mode = st2.st_mode;
			file->uid = st2.st_uid;
			file->gid = st2.st_gid;
			file->u.link = NULL;
		} else
			file->mode = save_mode;
	}

	if (S_ISREG(st.st_mode) || S_ISLNK(st.st_mode))
		stats.total_size += st.st_size;

	return file;
}

static struct file_struct *send_file_name(int f, struct file_list *flist,
					  char *fname, STRUCT_STAT *stp,
					  unsigned short flags)
{
	struct file_struct *file;

	file = make_file(fname, flist, stp, flags,
			 f == -2 ? SERVER_FILTERS : ALL_FILTERS);
	if (!file)
		return NULL;

	if (chmod_modes && !S_ISLNK(file->mode))
		file->mode = tweak_mode(file->mode, chmod_modes);

	maybe_emit_filelist_progress(flist->count + flist_count_offset);

	flist_expand(flist);

	if (file->basename[0]) {
		flist->files[flist->count++] = file;
		send_file_entry(file, f);
	}
	return file;
}

static void send_if_directory(int f, struct file_list *flist,
			      struct file_struct *file,
			      char *fbuf, unsigned int ol)
{
	char is_dot_dir = fbuf[ol-1] == '.' && (ol == 1 || fbuf[ol-2] == '/');

	if (S_ISDIR(file->mode)
	    && !(file->flags & FLAG_MOUNT_POINT) && f_name(file, fbuf)) {
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
		send_directory(f, flist, fbuf, len);
		pop_local_filters(save_filters);
		fbuf[ol] = '\0';
		if (is_dot_dir)
			fbuf[ol-1] = '.';
	}
}

/* This function is normally called by the sender, but the receiving side also
 * calls it from get_dirlist() with f set to -1 so that we just construct the
 * file list in memory without sending it over the wire.  Also, get_dirlist()
 * might call this with f set to -2, which also indicates that local filter
 * rules should be ignored. */
static void send_directory(int f, struct file_list *flist,
			   char *fbuf, int len)
{
	struct dirent *di;
	unsigned remainder;
	char *p;
	DIR *d;
	int start = flist->count;

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

		send_file_name(f, flist, fbuf, NULL, 0);
	}

	fbuf[len] = '\0';

	if (errno) {
		io_error |= IOERR_GENERAL;
		rsyserr(FERROR, errno, "readdir(%s)", full_fname(fbuf));
	}

	closedir(d);

	if (recurse) {
		int i, end = flist->count - 1;
		for (i = start; i <= end; i++)
			send_if_directory(f, flist, flist->files[i], fbuf, len);
	}
}

struct file_list *send_file_list(int f, int argc, char *argv[])
{
	int len;
	STRUCT_STAT st;
	char *p, *dir, olddir[sizeof curr_dir];
	char lastpath[MAXPATHLEN] = "";
	struct file_list *flist;
	struct timeval start_tv, end_tv;
	int64 start_write;
	int use_ff_fd = 0;

	rprintf(FLOG, "building file list\n");
	if (show_filelist_p())
		start_filelist_progress("building file list");

	start_write = stats.total_written;
	gettimeofday(&start_tv, NULL);

	flist = flist_new(WITH_HLINK, "send_file_list");

	io_start_buffering_out();
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
		olddir[0] = '\0';

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
			static char *lastdir;
			static int lastdir_len;

			strlcpy(olddir, curr_dir, sizeof olddir);

			if (!push_dir(dir, 0)) {
				io_error |= IOERR_GENERAL;
				rsyserr(FERROR, errno, "push_dir %s failed",
					full_fname(dir));
				continue;
			}

			if (lastdir && strcmp(lastdir, dir) == 0) {
				flist_dir = lastdir;
				flist_dir_len = lastdir_len;
			} else {
				flist_dir = lastdir = strdup(dir);
				flist_dir_len = lastdir_len = strlen(dir);
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
				copy_links |= copy_unsafe_links;
				xfer_dirs = 1;
				while ((slash = strchr(slash+1, '/')) != 0) {
					*slash = '\0';
					send_file_name(f, flist, fbuf, NULL, 0);
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
			struct file_struct *file;
			file = send_file_name(f, flist, fbuf, &st, FLAG_TOP_DIR);
			if (file)
				send_if_directory(f, flist, file, fbuf, len);
		} else
			send_file_name(f, flist, fbuf, &st, 0);

		if (olddir[0]) {
			flist_dir = NULL;
			flist_dir_len = 0;
			if (!pop_dir(olddir)) {
				rsyserr(FERROR, errno, "pop_dir %s failed",
					full_fname(olddir));
				exit_cleanup(RERR_FILESELECT);
			}
		}
	}

	gettimeofday(&end_tv, NULL);
	stats.flist_buildtime = (int64)(end_tv.tv_sec - start_tv.tv_sec) * 1000
			      + (end_tv.tv_usec - start_tv.tv_usec) / 1000;
	if (stats.flist_buildtime == 0)
		stats.flist_buildtime = 1;
	start_tv = end_tv;

	send_file_entry(NULL, f);

	if (show_filelist_p())
		finish_filelist_progress(flist);

	gettimeofday(&end_tv, NULL);
	stats.flist_xfertime = (int64)(end_tv.tv_sec - start_tv.tv_sec) * 1000
			     + (end_tv.tv_usec - start_tv.tv_usec) / 1000;

	if (flist->hlink_pool) {
		pool_destroy(flist->hlink_pool);
		flist->hlink_pool = NULL;
	}

	/* Sort the list without removing any duplicates.  This allows the
	 * receiving side to ask for any name they like, which gives us the
	 * flexibility to change the way we unduplicate names in the future
	 * without causing a compatibility problem with older versions. */
	clean_flist(flist, 0, 0);

	send_uid_list(f);

	/* send the io_error flag */
	write_int(f, lp_ignore_errors(module_id) ? 0 : io_error);

	io_end_buffering();
	stats.flist_size = stats.total_written - start_write;
	stats.num_files = flist->count;

	if (verbose > 3)
		output_flist(flist);

	if (verbose > 2)
		rprintf(FINFO, "send_file_list done\n");

	return flist;
}

struct file_list *recv_file_list(int f)
{
	struct file_list *flist;
	unsigned short flags;
	int64 start_read;

	rprintf(FLOG, "receiving file list\n");
	if (show_filelist_p())
		start_filelist_progress("receiving file list");

	start_read = stats.total_read;

	flist = flist_new(WITH_HLINK, "recv_file_list");

	flist->count = 0;
	flist->malloced = 1000;
	flist->files = new_array(struct file_struct *, flist->malloced);
	if (!flist->files)
		goto oom;

	while ((flags = read_byte(f)) != 0) {
		struct file_struct *file;

		flist_expand(flist);

		if (protocol_version >= 28 && (flags & XMIT_EXTENDED_FLAGS))
			flags |= read_byte(f) << 8;
		file = receive_file_entry(flist, flags, f);

		if (S_ISREG(file->mode) || S_ISLNK(file->mode))
			stats.total_size += file->length;

		flist->files[flist->count++] = file;

		maybe_emit_filelist_progress(flist->count);

		if (verbose > 2) {
			rprintf(FINFO, "recv_file_name(%s)\n",
				f_name(file, NULL));
		}
	}
	receive_file_entry(NULL, 0, 0); /* Signal that we're done. */

	if (verbose > 2)
		rprintf(FINFO, "received %d names\n", flist->count);

	if (show_filelist_p())
		finish_filelist_progress(flist);

	clean_flist(flist, relative_paths, 1);

	if (f >= 0) {
		recv_uid_list(f, flist);

		/* Recv the io_error flag */
		if (lp_ignore_errors(module_id) || ignore_errors)
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

	stats.flist_size = stats.total_read - start_read;
	stats.num_files = flist->count;

	return flist;

  oom:
	out_of_memory("recv_file_list");
	return NULL;		/* not reached */
}

static int file_compare(struct file_struct **file1, struct file_struct **file2)
{
	return f_name_cmp(*file1, *file2);
}

/* Search for an identically-named item in the file list.  Note that the
 * items must agree in their directory-ness, or no match is returned. */
int flist_find(struct file_list *flist, struct file_struct *f)
{
	int low = flist->low, high = flist->high;
	int diff, mid, mid_up;

	while (low <= high) {
		mid = (low + high) / 2;
		if (flist->files[mid]->basename)
			mid_up = mid;
		else {
			/* Scan for the next non-empty entry using the cached
			 * distance values.  If the value isn't fully up-to-
			 * date, update it. */
			mid_up = mid + flist->files[mid]->dir.depth;
			if (!flist->files[mid_up]->basename) {
				do {
				    mid_up += flist->files[mid_up]->dir.depth;
				} while (!flist->files[mid_up]->basename);
				flist->files[mid]->dir.depth = mid_up - mid;
			}
			if (mid_up > high) {
				/* If there's nothing left above us, set high to
				 * a non-empty entry below us and continue. */
				high = mid - flist->files[mid]->length;
				if (!flist->files[high]->basename) {
					do {
					    high -= flist->files[high]->length;
					} while (!flist->files[high]->basename);
					flist->files[mid]->length = mid - high;
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
void clear_file(struct file_struct *file, struct file_list *flist)
{
	if (flist->hlink_pool && file->link_u.idev)
		pool_free(flist->hlink_pool, 0, file->link_u.idev);
	memset(file, 0, file_struct_len);
	/* In an empty entry, dir.depth is an offset to the next non-empty
	 * entry.  Likewise for length in the opposite direction.  We assume
	 * that we're alone for now since flist_find() will adjust the counts
	 * it runs into that aren't up-to-date. */
	file->length = file->dir.depth = 1;
}

/*
 * allocate a new file list
 */
struct file_list *flist_new(int with_hlink, char *msg)
{
	struct file_list *flist;

	flist = new(struct file_list);
	if (!flist)
		out_of_memory(msg);

	memset(flist, 0, sizeof (struct file_list));

	if (!(flist->file_pool = pool_create(FILE_EXTENT, 0,
	    out_of_memory, POOL_INTERN)))
		out_of_memory(msg);

#ifdef SUPPORT_HARD_LINKS
	if (with_hlink && preserve_hard_links) {
		if (!(flist->hlink_pool = pool_create(HLINK_EXTENT,
		    sizeof (struct idev), out_of_memory, POOL_INTERN)))
			out_of_memory(msg);
	}
#endif

	return flist;
}

/*
 * free up all elements in a flist
 */
void flist_free(struct file_list *flist)
{
	pool_destroy(flist->file_pool);
	pool_destroy(flist->hlink_pool);
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
		if (flist->files[i]->basename) {
			prev_i = i;
			break;
		}
	}
	flist->low = prev_i;
	while (++i < flist->count) {
		int j;
		struct file_struct *file = flist->files[i];

		if (!file->basename)
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
			} else
				keep = j, drop = i;
			if (verbose > 1 && !am_server) {
				rprintf(FINFO,
					"removing duplicate name %s from file list (%d)\n",
					f_name(file, fbuf), drop);
			}
			/* Make sure we don't lose track of a user-specified
			 * top directory. */
			flist->files[keep]->flags |= flist->files[drop]->flags
						   & (FLAG_TOP_DIR|FLAG_DEL_HERE);

			clear_file(flist->files[drop], flist);

			if (keep == i) {
				if (flist->low == drop) {
					for (j = drop + 1;
					     j < i && !flist->files[j]->basename;
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

			/* This temporarily abuses the dir.depth value for a
			 * directory that is in a chain that might get pruned.
			 * We restore the old value if it gets a reprieve. */
			if (S_ISDIR(file->mode) && file->dir.depth) {
				/* Dump empty dirs when coming back down. */
				for (j = prev_depth; j >= file->dir.depth; j--) {
					fp = flist->files[prev_i];
					if (fp->dir.depth >= 0)
						break;
					prev_i = -fp->dir.depth-1;
					clear_file(fp, flist);
				}
				prev_depth = file->dir.depth;
				if (is_excluded(f_name(file, fbuf), 1,
						       ALL_FILTERS)) {
					/* Keep dirs through this dir. */
					for (j = prev_depth-1; ; j--) {
						fp = flist->files[prev_i];
						if (fp->dir.depth >= 0)
							break;
						prev_i = -fp->dir.depth-1;
						fp->dir.depth = j;
					}
				} else
					file->dir.depth = -prev_i-1;
				prev_i = i;
			} else {
				/* Keep dirs through this non-dir. */
				for (j = prev_depth; ; j--) {
					fp = flist->files[prev_i];
					if (fp->dir.depth >= 0)
						break;
					prev_i = -fp->dir.depth-1;
					fp->dir.depth = j;
				}
			}
		}
		/* Dump empty all remaining empty dirs. */
		while (1) {
			struct file_struct *fp = flist->files[prev_i];
			if (fp->dir.depth >= 0)
				break;
			prev_i = -fp->dir.depth-1;
			clear_file(fp, flist);
		}

		for (i = flist->low; i <= flist->high; i++) {
			if (flist->files[i]->basename)
				break;
		}
		flist->low = i;
		for (i = flist->high; i >= flist->low; i--) {
			if (flist->files[i]->basename)
				break;
		}
		flist->high = i;
	}
}

static void output_flist(struct file_list *flist)
{
	char uidbuf[16], gidbuf[16], depthbuf[16];
	struct file_struct *file;
	const char *who = who_am_i();
	int i;

	for (i = 0; i < flist->count; i++) {
		file = flist->files[i];
		if ((am_root || am_sender) && preserve_uid)
			snprintf(uidbuf, sizeof uidbuf, " uid=%ld", (long)file->uid);
		else
			*uidbuf = '\0';
		if (preserve_gid && file->gid != GID_NONE)
			snprintf(gidbuf, sizeof gidbuf, " gid=%ld", (long)file->gid);
		else
			*gidbuf = '\0';
		if (!am_sender)
			snprintf(depthbuf, sizeof depthbuf, "%d", file->dir.depth);
		rprintf(FINFO, "[%s] i=%d %s %s%s%s%s mode=0%o len=%.0f%s%s flags=%x\n",
			who, i, am_sender ? NS(file->dir.root) : depthbuf,
			file->dirname ? file->dirname : "",
			file->dirname ? "/" : "", NS(file->basename),
			S_ISDIR(file->mode) ? "/" : "", (int)file->mode,
			(double)file->length, uidbuf, gidbuf, file->flags);
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

	if (!f1 || !f1->basename) {
		if (!f2 || !f2->basename)
			return 0;
		return -1;
	}
	if (!f2 || !f2->basename)
		return 1;

	c1 = (uchar*)f1->dirname;
	c2 = (uchar*)f2->dirname;
	if (c1 == c2)
		c1 = c2 = NULL;
	if (!c1) {
		type1 = S_ISDIR(f1->mode) ? t_path : t_ITEM;
		c1 = (uchar*)f1->basename;
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
		c2 = (uchar*)f2->basename;
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
				c1 = (uchar*)f1->basename;
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
				c2 = (uchar*)f2->basename;
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

/* Return a copy of the full filename of a flist entry, using the indicated
 * buffer or one of 5 static buffers if fbuf is NULL.  No size-checking is
 * done because we checked the size when creating the file_struct entry.
 */
char *f_name(struct file_struct *f, char *fbuf)
{
	if (!f || !f->basename)
		return NULL;

	if (!fbuf) {
		static char names[5][MAXPATHLEN];
		static unsigned int n;

		n = (n + 1) % (sizeof names / sizeof names[0]);

		fbuf = names[n];
	}

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
struct file_list *get_dirlist(char *dirname, int dlen,
			      int ignore_filter_rules)
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

	dirlist = flist_new(WITHOUT_HLINK, "get_dirlist");

	recurse = 0;
	xfer_dirs = 1;
	send_directory(ignore_filter_rules ? -2 : -1, dirlist, dirname, dlen);
	xfer_dirs = save_xfer_dirs;
	recurse = save_recurse;
	if (do_progress)
		flist_count_offset += dirlist->count;

	clean_flist(dirlist, 0, 0);

	if (verbose > 3)
		output_flist(dirlist);

	return dirlist;
}
