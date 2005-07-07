/*
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>

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

/** @file flist.c
 * Generate and receive file lists
 *
 * @sa http://lists.samba.org/pipermail/rsync/2000-June/002351.html
 *
 **/

#include "rsync.h"

extern int verbose;
extern int dry_run;
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
extern int keep_dirlinks;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_perms;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int relative_paths;
extern int implied_dirs;
extern int copy_links;
extern int copy_unsafe_links;
extern int protocol_version;
extern int sanitize_paths;
extern int orig_umask;
extern struct stats stats;
extern struct file_list *the_file_list;

extern char curr_dir[MAXPATHLEN];

extern struct filter_list_struct filter_list;
extern struct filter_list_struct server_filter_list;

int io_error;
dev_t filesystem_dev; /* used to implement -x */

static char empty_sum[MD4_SUM_LENGTH];
static int flist_count_offset;
static unsigned int file_struct_len;
static struct file_list *sorting_flist;

static void clean_flist(struct file_list *flist, int strip_root, int no_dups);
static void output_flist(struct file_list *flist);

void init_flist(void)
{
	struct file_struct f;

	/* Figure out how big the file_struct is without trailing padding */
	file_struct_len = offsetof(struct file_struct, flags) + sizeof f.flags;
}


static int show_filelist_p(void)
{
	return verbose && xfer_dirs && !am_server;
}

static void start_filelist_progress(char *kind)
{
	rprintf(FINFO, "%s ... ", kind);
	if (verbose > 1 || do_progress)
		rprintf(FINFO, "\n");
	rflush(FINFO);
}


static void emit_filelist_progress(int count)
{
	rprintf(FINFO, " %d files...\r", count);
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
	char perms[11];

	if (!f->basename) {
		/* this can happen if duplicate names were removed */
		return;
	}

	permstring(perms, f->mode);

#ifdef SUPPORT_LINKS
	if (preserve_links && S_ISLNK(f->mode)) {
		rprintf(FINFO, "%s %11.0f %s %s -> %s\n",
			perms,
			(double)f->length, timestring(f->modtime),
			safe_fname(f_name(f)), safe_fname(f->u.link));
	} else
#endif
	{
		rprintf(FINFO, "%s %11.0f %s %s\n",
			perms,
			(double)f->length, timestring(f->modtime),
			safe_fname(f_name(f)));
	}
}


/**
 * Stat either a symlink or its referent, depending on the settings of
 * copy_links, copy_unsafe_links, etc.
 *
 * @retval -1 on error
 *
 * @retval 0 for success
 *
 * @post If @p path is a symlink, then @p linkbuf (of size @c
 * MAXPATHLEN) contains the symlink target.
 *
 * @post @p buffer contains information about the link or the
 * referrent as appropriate, if they exist.
 **/
static int readlink_stat(const char *path, STRUCT_STAT *buffer, char *linkbuf)
{
#ifdef SUPPORT_LINKS
	if (copy_links)
		return do_stat(path, buffer);
	if (link_stat(path, buffer, 0) < 0)
		return -1;
	if (S_ISLNK(buffer->st_mode)) {
		int l = readlink((char *)path, linkbuf, MAXPATHLEN - 1);
		if (l == -1)
			return -1;
		linkbuf[l] = 0;
		if (copy_unsafe_links && unsafe_symlink(linkbuf, path)) {
			if (verbose > 1) {
				rprintf(FINFO,"copying unsafe symlink \"%s\" -> \"%s\"\n",
					safe_fname(path), safe_fname(linkbuf));
			}
			return do_stat(path, buffer);
		}
	}
	return 0;
#else
	return do_stat(path, buffer);
#endif
}

int link_stat(const char *path, STRUCT_STAT *buffer, int follow_dirlinks)
{
#ifdef SUPPORT_LINKS
	if (copy_links)
		return do_stat(path, buffer);
	if (do_lstat(path, buffer) < 0)
		return -1;
	if (follow_dirlinks && S_ISLNK(buffer->st_mode)) {
		STRUCT_STAT st;
		if (do_stat(path, &st) == 0 && S_ISDIR(st.st_mode))
			*buffer = st;
	}
	return 0;
#else
	return do_stat(path, buffer);
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
	if (S_ISLNK(mode) && (_S_IFLNK != 0120000))
		return (mode & ~(_S_IFMT)) | 0120000;
#endif
	return (int)mode;
}

static mode_t from_wire_mode(int mode)
{
	if ((mode & (_S_IFMT)) == 0120000 && (_S_IFLNK != 0120000))
		return (mode & ~(_S_IFMT)) | _S_IFLNK;
	return (mode_t)mode;
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
		rprintf(FINFO, "[%s] expand file_list to %.0f bytes, did%s move\n",
		    who_am_i(),
		    (double)sizeof flist->files[0] * flist->malloced,
		    (new_ptr == flist->files) ? " not" : "");
	}

	flist->files = new_ptr;

	if (!flist->files)
		out_of_memory("flist_expand");
}

void send_file_entry(struct file_struct *file, int f, unsigned short base_flags)
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
		dev = 0, rdev = makedev(0, 0);
		rdev_major = 0;
		uid = 0, gid = 0;
		*lastname = '\0';
		return;
	}

	io_write_phase = "send_file_entry";

	f_name_to(file, fname);

	flags = base_flags;

	if (file->mode == mode)
		flags |= XMIT_SAME_MODE;
	else
		mode = file->mode;
	if (preserve_devices) {
		if (protocol_version < 28) {
			if (IS_DEVICE(mode)) {
				if (file->u.rdev == rdev)
					flags |= XMIT_SAME_RDEV_pre28;
				else
					rdev = file->u.rdev;
			} else
				rdev = makedev(0, 0);
		} else if (IS_DEVICE(mode)) {
			rdev = file->u.rdev;
			if ((uint32)major(rdev) == rdev_major)
				flags |= XMIT_SAME_RDEV_MAJOR;
			else
				rdev_major = major(rdev);
			if ((uint32)minor(rdev) <= 0xFFu)
				flags |= XMIT_RDEV_MINOR_IS_SMALL;
		}
	}
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
		if (!(flags & 0xFF) && !S_ISDIR(mode))
			flags |= XMIT_TOP_DIR;
		if (!(flags & 0xFF))
			flags |= XMIT_LONG_NAME;
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
	if (preserve_devices && IS_DEVICE(mode)) {
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
	if (flags & XMIT_HAS_IDEV_DATA) {
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

	if (always_checksum) {
		char *sum;
		if (S_ISREG(mode))
			sum = file->u.sum;
		else if (protocol_version < 28) {
			/* Prior to 28, we sent a useless set of nulls. */
			sum = empty_sum;
		} else
			sum = NULL;
		if (sum) {
			write_buf(f, sum,
			    protocol_version < 21 ? 2 : MD4_SUM_LENGTH);
		}
	}

	strlcpy(lastname, fname, MAXPATHLEN);

	io_write_phase = "unknown";
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
		dev = 0, rdev = makedev(0, 0);
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
			flags, l1, l2, safe_fname(lastname));
		overflow_exit("receive_file_entry");
	}

	strlcpy(thisname, lastname, l1 + 1);
	read_sbuf(f, &thisname[l1], l2);
	thisname[l1 + l2] = 0;

	strlcpy(lastname, thisname, MAXPATHLEN);

	clean_fname(thisname, 0);

	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0);

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

	if (preserve_uid && !(flags & XMIT_SAME_UID))
		uid = (uid_t)read_int(f);
	if (preserve_gid && !(flags & XMIT_SAME_GID))
		gid = (gid_t)read_int(f);

	if (preserve_devices) {
		if (protocol_version < 28) {
			if (IS_DEVICE(mode)) {
				if (!(flags & XMIT_SAME_RDEV_pre28))
					rdev = (dev_t)read_int(f);
			} else
				rdev = makedev(0, 0);
		} else if (IS_DEVICE(mode)) {
			uint32 rdev_minor;
			if (!(flags & XMIT_SAME_RDEV_MAJOR))
				rdev_major = read_int(f);
			if (flags & XMIT_RDEV_MINOR_IS_SMALL)
				rdev_minor = read_byte(f);
			else
				rdev_minor = read_int(f);
			rdev = makedev(rdev_major, rdev_minor);
		}
	}

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

	file->flags = 0;
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
			in_del_hier = 1;
			del_hier_name_len = file->dir.depth == 0 ? 0 : l1 + l2;
			if (relative_paths && del_hier_name_len > 2
			    && basename_len == 1+1 && *basename == '.')
				del_hier_name_len -= 2;
			file->flags |= FLAG_TOP_DIR | FLAG_DEL_HERE;
		} else if (in_del_hier) {
			if (!relative_paths || !del_hier_name_len
			 || (l1 >= del_hier_name_len
			  && thisname[del_hier_name_len] == '/'))
				file->flags |= FLAG_DEL_HERE;
			else
				in_del_hier = 0;
		}
	}

	file->basename = bp;
	memcpy(bp, basename, basename_len);
	bp += basename_len;

	if (preserve_devices && IS_DEVICE(mode))
		file->u.rdev = rdev;

#ifdef SUPPORT_LINKS
	if (linkname_len) {
		file->u.link = bp;
		read_sbuf(f, bp, linkname_len - 1);
		if (sanitize_paths)
			sanitize_path(bp, bp, "", lastdir_depth);
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

	if (always_checksum) {
		char *sum;
		if (sum_len) {
			file->u.sum = sum = bp;
			/*bp += sum_len;*/
		} else if (protocol_version < 28) {
			/* Prior to 28, we get a useless set of nulls. */
			sum = empty_sum;
		} else
			sum = NULL;
		if (sum) {
			read_buf(f, sum,
			    protocol_version < 21 ? 2 : MD4_SUM_LENGTH);
		}
	}

	if (!preserve_perms) {
		/* set an appropriate set of permissions based on original
		 * permissions and umask. This emulates what GNU cp does */
		file->mode &= ~orig_umask;
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
	unsigned short flags = 0;

	if (!flist || !flist->count)	/* Ignore lastdir when invalid. */
		lastdir_len = -1;

	if (strlcpy(thisname, fname, sizeof thisname)
	    >= sizeof thisname - flist_dir_len) {
		rprintf(FINFO, "skipping overly long name: %s\n",
			safe_fname(fname));
		return NULL;
	}
	clean_fname(thisname, 0);
	if (sanitize_paths)
		sanitize_path(thisname, thisname, "", 0);

	memset(sum, 0, SUM_LENGTH);

	if (readlink_stat(thisname, &st, linkname) != 0) {
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
		rprintf(FINFO, "skipping directory %s\n", safe_fname(thisname));
		return NULL;
	}

	/* We only care about directories because we need to avoid recursing
	 * into a mount-point directory, not to avoid copying a symlinked
	 * file if -L (or similar) was specified. */
	if (one_file_system && st.st_dev != filesystem_dev
	    && S_ISDIR(st.st_mode))
		flags |= FLAG_MOUNT_POINT;

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
			who_am_i(), safe_fname(thisname), filter_level);
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

	sum_len = always_checksum && S_ISREG(st.st_mode) ? MD4_SUM_LENGTH : 0;

	alloc_len = file_struct_len + dirname_len + basename_len
	    + linkname_len + sum_len;
	if (flist) {
		bp = pool_alloc(flist->file_pool, alloc_len,
		    "receive_file_entry");
	} else {
		if (!(bp = new_array(char, alloc_len)))
			out_of_memory("receive_file_entry");
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
	if (preserve_devices && IS_DEVICE(st.st_mode))
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
		file->mode = S_IFDIR; /* find a directory w/our name */
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
					  char *fname, unsigned short base_flags)
{
	struct file_struct *file;

	file = make_file(fname, flist, f == -2 ? SERVER_FILTERS : ALL_FILTERS);
	if (!file)
		return NULL;

	maybe_emit_filelist_progress(flist->count + flist_count_offset);

	flist_expand(flist);

	if (file->basename[0]) {
		flist->files[flist->count++] = file;
		send_file_entry(file, f, base_flags);
	}
	return file;
}

static void send_if_directory(int f, struct file_list *flist,
			      struct file_struct *file)
{
	char fbuf[MAXPATHLEN];

	if (S_ISDIR(file->mode)
	    && !(file->flags & FLAG_MOUNT_POINT) && f_name_to(file, fbuf)) {
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
		if (strlcpy(p, dname, remainder) < remainder)
			send_file_name(f, flist, fbuf, 0);
		else {
			io_error |= IOERR_GENERAL;
			rprintf(FINFO,
				"cannot send long-named file %s\n",
				full_fname(fbuf));
		}
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
			send_if_directory(f, flist, flist->files[i]);
	}
}


struct file_list *send_file_list(int f, int argc, char *argv[])
{
	int l;
	STRUCT_STAT st;
	char *p, *dir, olddir[sizeof curr_dir];
	char lastpath[MAXPATHLEN] = "";
	struct file_list *flist;
	struct timeval start_tv, end_tv;
	int64 start_write;
	int use_ff_fd = 0;

	if (show_filelist_p())
		start_filelist_progress("building file list");

	start_write = stats.total_written;
	gettimeofday(&start_tv, NULL);

	flist = flist_new(WITH_HLINK, "send_file_list");

	io_start_buffering_out();
	if (filesfrom_fd >= 0) {
		if (argv[0] && !push_dir(argv[0])) {
			rsyserr(FERROR, errno, "push_dir %s failed",
				full_fname(argv[0]));
			exit_cleanup(RERR_FILESELECT);
		}
		use_ff_fd = 1;
	}

	while (1) {
		struct file_struct *file;
		char fname2[MAXPATHLEN];
		char *fname = fname2;
		int is_dot_dir;

		if (use_ff_fd) {
			if (read_filesfrom_line(filesfrom_fd, fname) == 0)
				break;
			sanitize_path(fname, fname, "", 0);
		} else {
			if (argc-- == 0)
				break;
			strlcpy(fname, *argv++, MAXPATHLEN);
			if (sanitize_paths)
				sanitize_path(fname, fname, "", 0);
		}

		l = strlen(fname);
		if (!l || fname[l - 1] == '/') {
			if (l == 2 && fname[0] == '.') {
				/* Turn "./" into just "." rather than "./." */
				fname[1] = '\0';
			} else {
				if (l + 1 >= MAXPATHLEN)
					overflow_exit("send_file_list");
				fname[l++] = '.';
				fname[l] = '\0';
			}
			is_dot_dir = 1;
		} else if (l > 1 && fname[l-1] == '.' && fname[l-2] == '.'
		    && (l == 2 || fname[l-3] == '/')) {
			if (l + 2 >= MAXPATHLEN)
				overflow_exit("send_file_list");
			fname[l++] = '/';
			fname[l++] = '.';
			fname[l] = '\0';
			is_dot_dir = 1;
		} else {
			is_dot_dir = fname[l-1] == '.'
				   && (l == 1 || fname[l-2] == '/');
		}

		if (link_stat(fname, &st, keep_dirlinks) != 0) {
			io_error |= IOERR_GENERAL;
			rsyserr(FERROR, errno, "link_stat %s failed",
				full_fname(fname));
			continue;
		}

		if (S_ISDIR(st.st_mode) && !xfer_dirs) {
			rprintf(FINFO, "skipping directory %s\n",
				safe_fname(fname));
			continue;
		}

		dir = NULL;
		olddir[0] = '\0';

		if (!relative_paths) {
			p = strrchr(fname, '/');
			if (p) {
				*p = 0;
				if (p == fname)
					dir = "/";
				else
					dir = fname;
				fname = p + 1;
			}
		} else if (implied_dirs && (p=strrchr(fname,'/')) && p != fname) {
			/* this ensures we send the intermediate directories,
			   thus getting their permissions right */
			char *lp = lastpath, *fn = fname, *slash = fname;
			*p = 0;
			/* Skip any initial directories in our path that we
			 * have in common with lastpath. */
			while (*fn && *lp == *fn) {
				if (*fn == '/')
					slash = fn;
				lp++, fn++;
			}
			*p = '/';
			if (fn != p || (*lp && *lp != '/')) {
				int save_copy_links = copy_links;
				int save_xfer_dirs = xfer_dirs;
				copy_links = copy_unsafe_links;
				xfer_dirs = 1;
				while ((slash = strchr(slash+1, '/')) != 0) {
					*slash = 0;
					send_file_name(f, flist, fname, 0);
					*slash = '/';
				}
				copy_links = save_copy_links;
				xfer_dirs = save_xfer_dirs;
				*p = 0;
				strlcpy(lastpath, fname, sizeof lastpath);
				*p = '/';
			}
		}

		if (!*fname)
			fname = ".";

		if (dir && *dir) {
			static char *lastdir;
			static int lastdir_len;

			strcpy(olddir, curr_dir); /* can't overflow */

			if (!push_dir(dir)) {
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

		if (one_file_system)
			filesystem_dev = st.st_dev;

		if ((file = send_file_name(f, flist, fname, XMIT_TOP_DIR))) {
			if (recurse || (xfer_dirs && is_dot_dir))
				send_if_directory(f, flist, file);
		}

		if (olddir[0]) {
			flist_dir = NULL;
			flist_dir_len = 0;
			if (!pop_dir(olddir)) {
				rsyserr(FERROR, errno, "pop_dir %s failed",
					full_fname(dir));
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

	send_file_entry(NULL, f, 0);

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

	/* Now send the uid/gid list. This was introduced in
	 * protocol version 15 */
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

		if (S_ISREG(file->mode))
			stats.total_size += file->length;

		flist->files[flist->count++] = file;

		maybe_emit_filelist_progress(flist->count);

		if (verbose > 2) {
			rprintf(FINFO, "recv_file_name(%s)\n",
				safe_fname(f_name(file)));
		}
	}
	receive_file_entry(NULL, 0, 0); /* Signal that we're done. */

	if (verbose > 2)
		rprintf(FINFO, "received %d names\n", flist->count);

	if (show_filelist_p())
		finish_filelist_progress(flist);

	clean_flist(flist, relative_paths, 1);

	if (f >= 0) {
		/* Now send the uid/gid list. This was introduced in
		 * protocol version 15 */
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
	int ret, mid, mid_up;

	while (low <= high) {
		mid = (low + high) / 2;
		for (mid_up = mid; !flist->files[mid_up]->basename; mid_up++) {}
		if (mid_up <= high)
			ret = f_name_cmp(flist->files[mid_up], f);
		else
			ret = 1;
		if (ret == 0) {
			if (protocol_version < 29
			    && S_ISDIR(flist->files[mid_up]->mode)
			    != S_ISDIR(f->mode))
				return -1;
			return mid_up;
		}
		if (ret > 0)
			high = mid - 1;
		else
			low = mid_up + 1;
	}
	return -1;
}


/*
 * Free up any resources a file_struct has allocated
 * and clear the file.
 */
void clear_file(int i, struct file_list *flist)
{
	if (flist->hlink_pool && flist->files[i]->link_u.idev)
		pool_free(flist->hlink_pool, 0, flist->files[i]->link_u.idev);
	memset(flist->files[i], 0, file_struct_len);
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
	int i, prev_i = 0;

	if (!flist)
		return;
	if (flist->count == 0) {
		flist->high = -1;
		return;
	}

	sorting_flist = flist;
	qsort(flist->files, flist->count,
	    sizeof flist->files[0], (int (*)())file_compare);
	sorting_flist = NULL;

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
					safe_fname(f_name(file)), drop);
			}
			/* Make sure that if we unduplicate '.', that we don't
			 * lose track of a user-specified top directory. */
			flist->files[keep]->flags |= flist->files[drop]->flags
						   & (FLAG_TOP_DIR|FLAG_DEL_HERE);

			clear_file(drop, flist);

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
			if (*file->dirname == '/') {
				char *s = file->dirname + 1;
				while (*s == '/') s++;
				memmove(file->dirname, s, strlen(s) + 1);
			}

			if (!*file->dirname)
				file->dirname = NULL;
		}
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
			sprintf(uidbuf, " uid=%ld", (long)file->uid);
		else
			*uidbuf = '\0';
		if (preserve_gid && file->gid != GID_NONE)
			sprintf(gidbuf, " gid=%ld", (long)file->gid);
		else
			*gidbuf = '\0';
		if (!am_sender)
			sprintf(depthbuf, "%d", file->dir.depth);
		rprintf(FINFO, "[%s] i=%d %s %s%s%s%s mode=0%o len=%.0f%s%s flags=%x\n",
			who, i, am_sender ? NS(file->dir.root) : depthbuf,
			file->dirname ? safe_fname(file->dirname) : "",
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
	} else if (!*c1) {
		type1 = t_path;
		state1 = s_SLASH;
		c1 = (uchar*)"/";
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
	} else if (!*c2) {
		type2 = t_path;
		state2 = s_SLASH;
		c2 = (uchar*)"/";
	} else {
		type2 = t_path;
		state2 = s_DIR;
	}

	if (type1 != type2)
		return type1 == t_PATH ? 1 : -1;

	while (1) {
		if ((dif = (int)*c1++ - (int)*c2++) != 0)
			break;
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
	}

	return dif;
}


/* Return a copy of the full filename of a flist entry, using the indicated
 * buffer.  No size-checking is done because we checked the size when creating
 * the file_struct entry.
 */
char *f_name_to(struct file_struct *f, char *fbuf)
{
	if (!f || !f->basename)
		return NULL;

	if (f->dirname) {
		int len = strlen(f->dirname);
		memcpy(fbuf, f->dirname, len);
		fbuf[len] = '/';
		strcpy(fbuf + len + 1, f->basename);
	} else
		strcpy(fbuf, f->basename);
	return fbuf;
}


/* Like f_name_to(), but we rotate through 5 static buffers of our own. */
char *f_name(struct file_struct *f)
{
	static char names[5][MAXPATHLEN];
	static unsigned int n;

	n = (n + 1) % (sizeof names / sizeof names[0]);

	return f_name_to(f, names[n]);
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

	if (dlen < 0) {
		dlen = strlcpy(dirbuf, dirname, MAXPATHLEN);
		if (dlen >= MAXPATHLEN)
			return NULL;
		dirname = dirbuf;
	}

	dirlist = flist_new(WITHOUT_HLINK, "get_dirlist");

	recurse = 0;
	send_directory(ignore_filter_rules ? -2 : -1, dirlist, dirname, dlen);
	recurse = save_recurse;
	if (do_progress)
		flist_count_offset += dirlist->count;

	clean_flist(dirlist, 0, 0);

	if (verbose > 3)
		output_flist(dirlist);

	return dirlist;
}
