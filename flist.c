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

extern struct stats stats;

extern int verbose;
extern int do_progress;
extern int am_root;
extern int am_server;
extern int am_daemon;
extern int always_checksum;
extern int module_id;
extern int ignore_errors;
extern int numeric_ids;

extern int cvs_exclude;

extern int recurse;
extern char curr_dir[MAXPATHLEN];
extern char *files_from;
extern int filesfrom_fd;

extern int one_file_system;
extern int preserve_links;
extern int preserve_hard_links;
extern int preserve_perms;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_times;
extern int relative_paths;
extern int implied_dirs;
extern int copy_links;
extern int copy_unsafe_links;
extern int protocol_version;
extern int sanitize_paths;

extern int read_batch;
extern int write_batch;

extern struct exclude_list_struct exclude_list;
extern struct exclude_list_struct server_exclude_list;
extern struct exclude_list_struct local_exclude_list;

int io_error;

static char empty_sum[MD4_SUM_LENGTH];
static unsigned int file_struct_len;

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
	return verbose && (recurse || files_from) && !am_server;
}

static void start_filelist_progress(char *kind)
{
	rprintf(FINFO, "%s ... ", kind);
	if ((verbose > 1) || do_progress)
		rprintf(FINFO, "\n");
	rflush(FINFO);
}


static void emit_filelist_progress(const struct file_list *flist)
{
	rprintf(FINFO, " %d files...\r", flist->count);
}


static void maybe_emit_filelist_progress(const struct file_list *flist)
{
	if (do_progress && show_filelist_p() && ((flist->count % 100) == 0))
		emit_filelist_progress(flist);
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

	if (!f->basename)
		/* this can happen if duplicate names were removed */
		return;

	permstring(perms, f->mode);

#if SUPPORT_LINKS
	if (preserve_links && S_ISLNK(f->mode)) {
		rprintf(FINFO, "%s %11.0f %s %s -> %s\n",
			perms,
			(double) f->length, timestring(f->modtime),
			f_name(f), f->u.link);
	} else
#endif
		rprintf(FINFO, "%s %11.0f %s %s\n",
			perms,
			(double) f->length, timestring(f->modtime),
			f_name(f));
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
int readlink_stat(const char *path, STRUCT_STAT *buffer, char *linkbuf)
{
#if SUPPORT_LINKS
	if (copy_links)
		return do_stat(path, buffer);
	if (do_lstat(path, buffer) == -1)
		return -1;
	if (S_ISLNK(buffer->st_mode)) {
		int l = readlink((char *) path, linkbuf, MAXPATHLEN - 1);
		if (l == -1)
			return -1;
		linkbuf[l] = 0;
		if (copy_unsafe_links && unsafe_symlink(linkbuf, path)) {
			if (verbose > 1) {
				rprintf(FINFO,"copying unsafe symlink \"%s\" -> \"%s\"\n",
					path, linkbuf);
			}
			return do_stat(path, buffer);
		}
	}
	return 0;
#else
	return do_stat(path, buffer);
#endif
}

int link_stat(const char *path, STRUCT_STAT * buffer)
{
#if SUPPORT_LINKS
	if (copy_links)
		return do_stat(path, buffer);
	return do_lstat(path, buffer);
#else
	return do_stat(path, buffer);
#endif
}

/*
 * This function is used to check if a file should be included/excluded
 * from the list of files based on its name and type etc.  The value of
 * exclude_level is set to either SERVER_EXCLUDES or ALL_EXCLUDES.
 */
static int check_exclude_file(char *fname, int is_dir, int exclude_level)
{
	int rc;

#if 0 /* This currently never happens, so avoid a useless compare. */
	if (exclude_level == NO_EXCLUDES)
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
	if (server_exclude_list.head
	    && check_exclude(&server_exclude_list, fname, is_dir) < 0)
		return 1;
	if (exclude_level != ALL_EXCLUDES)
		return 0;
	if (exclude_list.head
	    && (rc = check_exclude(&exclude_list, fname, is_dir)) != 0)
		return rc < 0;
	if (local_exclude_list.head
	    && check_exclude(&local_exclude_list, fname, is_dir) < 0)
		return 1;
	return 0;
}

/* used by the one_file_system code */
static dev_t filesystem_dev;

static void set_filesystem(char *fname)
{
	STRUCT_STAT st;
	if (link_stat(fname, &st) != 0)
		return;
	filesystem_dev = st.st_dev;
}


static int to_wire_mode(mode_t mode)
{
#if SUPPORT_LINKS
	if (S_ISLNK(mode) && (_S_IFLNK != 0120000))
		return (mode & ~(_S_IFMT)) | 0120000;
#endif
	return (int) mode;
}

static mode_t from_wire_mode(int mode)
{
	if ((mode & (_S_IFMT)) == 0120000 && (_S_IFLNK != 0120000))
		return (mode & ~(_S_IFMT)) | _S_IFLNK;
	return (mode_t) mode;
}


static void send_directory(int f, struct file_list *flist, char *dir);

static char *flist_dir;
static int flist_dir_len;


/**
 * Make sure @p flist is big enough to hold at least @p flist->count
 * entries.
 **/
void flist_expand(struct file_list *flist)
{
	void *new_ptr;

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

	if (flist->files) {
		new_ptr = realloc_array(flist->files,
		    struct file_struct *, flist->malloced);
	} else {
		new_ptr = new_array(struct file_struct *, flist->malloced);
	}

	if (verbose >= 2) {
		rprintf(FINFO, "[%s] expand file_list to %.0f bytes, did%s move\n",
		    who_am_i(),
		    (double) sizeof flist->files[0] * flist->malloced,
		    (new_ptr == flist->files) ? " not" : "");
	}

	flist->files = (struct file_struct **) new_ptr;

	if (!flist->files)
		out_of_memory("flist_expand");
}

void send_file_entry(struct file_struct *file, int f, unsigned short base_flags)
{
	unsigned short flags;
	static time_t modtime;
	static mode_t mode;
	static uint64 dev;
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static char lastname[MAXPATHLEN];
	char *fname, fbuf[MAXPATHLEN];
	int l1, l2;

	if (f == -1)
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

	fname = f_name_to(file, fbuf);

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

#if SUPPORT_HARD_LINKS
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

#if SUPPORT_LINKS
	if (preserve_links && S_ISLNK(mode)) {
		int len = strlen(file->u.link);
		write_int(f, len);
		write_buf(f, file->u.link, len);
	}
#endif

#if SUPPORT_HARD_LINKS
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



void receive_file_entry(struct file_struct **fptr, unsigned short flags,
    struct file_list *flist, int f)
{
	static time_t modtime;
	static mode_t mode;
	static uint64 dev;
	static dev_t rdev;
	static uint32 rdev_major;
	static uid_t uid;
	static gid_t gid;
	static char lastname[MAXPATHLEN], *lastdir;
	static int lastdir_len = -1;
	char thisname[MAXPATHLEN];
	unsigned int l1 = 0, l2 = 0;
	int alloc_len, basename_len, dirname_len, linkname_len, sum_len;
	OFF_T file_length;
	char *basename, *dirname, *bp;
	struct file_struct *file;

	if (!fptr) {
		modtime = 0, mode = 0;
		dev = 0, rdev = makedev(0, 0);
		rdev_major = 0;
		uid = 0, gid = 0;
		*lastname = '\0';
		return;
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
		overflow("receive_file_entry");
	}

	strlcpy(thisname, lastname, l1 + 1);
	read_sbuf(f, &thisname[l1], l2);
	thisname[l1 + l2] = 0;

	strlcpy(lastname, thisname, MAXPATHLEN);

	clean_fname(thisname);

	if (sanitize_paths)
		sanitize_path(thisname, NULL);

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

#if SUPPORT_LINKS
	if (preserve_links && S_ISLNK(mode)) {
		linkname_len = read_int(f) + 1; /* count the '\0' */
		if (linkname_len <= 0 || linkname_len > MAXPATHLEN) {
			rprintf(FERROR, "overflow: linkname_len=%d\n",
				linkname_len - 1);
			overflow("receive_file_entry");
		}
	}
	else
#endif
		linkname_len = 0;

	sum_len = always_checksum && S_ISREG(mode) ? MD4_SUM_LENGTH : 0;

	alloc_len = file_struct_len + dirname_len + basename_len
		  + linkname_len + sum_len;
	bp = pool_alloc(flist->file_pool, alloc_len, "receive_file_entry");

	file = *fptr = (struct file_struct *)bp;
	memset(bp, 0, file_struct_len);
	bp += file_struct_len;

	file->flags = flags & XMIT_TOP_DIR ? FLAG_TOP_DIR : 0;
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
	} else if (dirname)
		file->dirname = dirname;

	file->basename = bp;
	memcpy(bp, basename, basename_len);
	bp += basename_len;

	if (preserve_devices && IS_DEVICE(mode))
		file->u.rdev = rdev;

#if SUPPORT_LINKS
	if (linkname_len) {
		file->u.link = bp;
		read_sbuf(f, bp, linkname_len - 1);
		if (sanitize_paths)
			sanitize_path(bp, lastdir);
		bp += linkname_len;
	}
#endif

#if SUPPORT_HARD_LINKS
	if (preserve_hard_links && protocol_version < 28 && S_ISREG(mode))
		flags |= XMIT_HAS_IDEV_DATA;
	if (flags & XMIT_HAS_IDEV_DATA) {
		uint64 inode;
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
		extern int orig_umask;
		/* set an appropriate set of permissions based on original
		 * permissions and umask. This emulates what GNU cp does */
		file->mode &= ~orig_umask;
	}
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
struct file_struct *make_file(char *fname,
    struct file_list *flist, int exclude_level)
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

	if (!flist)	/* lastdir isn't valid if flist is NULL */
		lastdir_len = -1;

	if (strlcpy(thisname, fname, sizeof thisname)
	    >= sizeof thisname - flist_dir_len) {
		rprintf(FINFO, "skipping overly long name: %s\n", fname);
		return NULL;
	}
	clean_fname(thisname);
	if (sanitize_paths)
		sanitize_path(thisname, NULL);

	memset(sum, 0, SUM_LENGTH);

	if (readlink_stat(thisname, &st, linkname) != 0) {
		int save_errno = errno;
		if (errno == ENOENT) {
			enum logcode c = am_daemon && protocol_version < 28
			    ? FERROR : FINFO;
			/* either symlink pointing nowhere or file that
			 * was removed during rsync run; see if excluded
			 * before reporting an error */
			if (exclude_level != NO_EXCLUDES
			    && check_exclude_file(thisname, 0, exclude_level)) {
				/* file is excluded anyway, ignore silently */
				return NULL;
			}
			io_error |= IOERR_VANISHED;
			rprintf(c, "file has vanished: %s\n",
			    full_fname(thisname));
		}
		else {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR, "readlink %s failed: %s\n",
			    full_fname(thisname), strerror(save_errno));
		}
		return NULL;
	}

	/* backup.c calls us with exclude_level set to NO_EXCLUDES. */
	if (exclude_level == NO_EXCLUDES)
		goto skip_excludes;

	if (S_ISDIR(st.st_mode) && !recurse && !files_from) {
		rprintf(FINFO, "skipping directory %s\n", thisname);
		return NULL;
	}

	/* We only care about directories because we need to avoid recursing
	 * into a mount-point directory, not to avoid copying a symlinked
	 * file if -L (or similar) was specified. */
	if (one_file_system && st.st_dev != filesystem_dev
	    && S_ISDIR(st.st_mode))
		flags |= FLAG_MOUNT_POINT;

	if (check_exclude_file(thisname, S_ISDIR(st.st_mode) != 0, exclude_level))
		return NULL;

	if (lp_ignore_nonreadable(module_id) && access(thisname, R_OK) != 0)
		return NULL;

skip_excludes:

	if (verbose > 2) {
		rprintf(FINFO, "[%s] make_file(%s,*,%d)\n",
			who_am_i(), thisname, exclude_level);
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

#if SUPPORT_LINKS
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

#if SUPPORT_HARD_LINKS
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

#if SUPPORT_LINKS
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

	file->basedir = flist_dir;

	if (!S_ISDIR(st.st_mode))
		stats.total_size += st.st_size;

	return file;
}


void send_file_name(int f, struct file_list *flist, char *fname,
		    int recursive, unsigned short base_flags)
{
	struct file_struct *file;
	char fbuf[MAXPATHLEN];
	extern int delete_excluded;

	/* f is set to -1 when calculating deletion file list */
	file = make_file(fname, flist,
	    f == -1 && delete_excluded? SERVER_EXCLUDES : ALL_EXCLUDES);

	if (!file)
		return;

	maybe_emit_filelist_progress(flist);

	flist_expand(flist);

	if (write_batch)
		file->flags |= FLAG_TOP_DIR;

	if (file->basename[0]) {
		flist->files[flist->count++] = file;
		send_file_entry(file, f, base_flags);
	}

	if (recursive && S_ISDIR(file->mode)
	    && !(file->flags & FLAG_MOUNT_POINT)) {
		struct exclude_list_struct last_list = local_exclude_list;
		local_exclude_list.head = local_exclude_list.tail = NULL;
		send_directory(f, flist, f_name_to(file, fbuf));
		free_exclude_list(&local_exclude_list);
		local_exclude_list = last_list;
	}
}


static void send_directory(int f, struct file_list *flist, char *dir)
{
	DIR *d;
	struct dirent *di;
	char fname[MAXPATHLEN];
	unsigned int offset;
	char *p;

	d = opendir(dir);
	if (!d) {
		io_error |= IOERR_GENERAL;
		rprintf(FERROR, "opendir %s failed: %s\n",
			full_fname(dir), strerror(errno));
		return;
	}

	offset = strlcpy(fname, dir, MAXPATHLEN);
	p = fname + offset;
	if (offset >= MAXPATHLEN || p[-1] != '/') {
		if (offset >= MAXPATHLEN - 1) {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR, "skipping long-named directory: %s\n",
				full_fname(fname));
			closedir(d);
			return;
		}
		*p++ = '/';
		offset++;
	}

	if (cvs_exclude) {
		if (strlcpy(p, ".cvsignore", MAXPATHLEN - offset)
		    < MAXPATHLEN - offset) {
			add_exclude_file(&local_exclude_list, fname,
					 XFLG_WORD_SPLIT | XFLG_WORDS_ONLY);
		} else {
			io_error |= IOERR_GENERAL;
			rprintf(FINFO,
				"cannot cvs-exclude in long-named directory %s\n",
				full_fname(fname));
		}
	}

	for (errno = 0, di = readdir(d); di; errno = 0, di = readdir(d)) {
		char *dname = d_name(di);
		if (dname[0] == '.' && (dname[1] == '\0'
		    || (dname[1] == '.' && dname[2] == '\0')))
			continue;
		if (strlcpy(p, dname, MAXPATHLEN - offset) < MAXPATHLEN - offset)
			send_file_name(f, flist, fname, recurse, 0);
		else {
			io_error |= IOERR_GENERAL;
			rprintf(FINFO,
				"cannot send long-named file %s\n",
				full_fname(fname));
		}
	}
	if (errno) {
		io_error |= IOERR_GENERAL;
		rprintf(FERROR, "readdir(%s): (%d) %s\n",
			dir, errno, strerror(errno));
	}

	closedir(d);
}


/**
 * The delete_files() function in receiver.c sets f to -1 so that we just
 * construct the file list in memory without sending it over the wire.  It
 * also has the side-effect of ignoring user-excludes if delete_excluded
 * is set (so that the delete list includes user-excluded files).
 **/
struct file_list *send_file_list(int f, int argc, char *argv[])
{
	int l;
	STRUCT_STAT st;
	char *p, *dir, olddir[sizeof curr_dir];
	char lastpath[MAXPATHLEN] = "";
	struct file_list *flist;
	int64 start_write;
	int use_ff_fd = 0;

	if (show_filelist_p() && f != -1)
		start_filelist_progress("building file list");

	start_write = stats.total_written;

	flist = flist_new(f == -1 ? WITHOUT_HLINK : WITH_HLINK,
	    "send_file_list");

	if (f != -1) {
		io_start_buffering_out(f);
		if (filesfrom_fd >= 0) {
			if (argv[0] && !push_dir(argv[0])) {
				rprintf(FERROR, "push_dir %s failed: %s\n",
					full_fname(argv[0]), strerror(errno));
				exit_cleanup(RERR_FILESELECT);
			}
			use_ff_fd = 1;
		}
	}

	while (1) {
		char fname2[MAXPATHLEN];
		char *fname = fname2;

		if (use_ff_fd) {
			if (read_filesfrom_line(filesfrom_fd, fname) == 0)
				break;
			sanitize_path(fname, NULL);
		} else {
			if (argc-- == 0)
				break;
			strlcpy(fname, *argv++, MAXPATHLEN);
			if (sanitize_paths)
				sanitize_path(fname, NULL);
		}

		l = strlen(fname);
		if (fname[l - 1] == '/') {
			if (l == 2 && fname[0] == '.') {
				/* Turn "./" into just "." rather than "./." */
				fname[1] = '\0';
			} else if (l < MAXPATHLEN) {
				fname[l++] = '.';
				fname[l] = '\0';
			}
		}

		if (link_stat(fname, &st) != 0) {
			if (f != -1) {
				io_error |= IOERR_GENERAL;
				rprintf(FERROR, "link_stat %s failed: %s\n",
					full_fname(fname), strerror(errno));
			}
			continue;
		}

		if (S_ISDIR(st.st_mode) && !recurse && !files_from) {
			rprintf(FINFO, "skipping directory %s\n", fname);
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
		} else if (f != -1 && implied_dirs && (p=strrchr(fname,'/')) && p != fname) {
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
				int copy_links_saved = copy_links;
				int recurse_saved = recurse;
				copy_links = copy_unsafe_links;
				/* set recurse to 1 to prevent make_file
				 * from ignoring directory, but still
				 * turn off the recursive parameter to
				 * send_file_name */
				recurse = 1;
				while ((slash = strchr(slash+1, '/')) != 0) {
					*slash = 0;
					send_file_name(f, flist, fname, 0, 0);
					*slash = '/';
				}
				copy_links = copy_links_saved;
				recurse = recurse_saved;
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
				rprintf(FERROR, "push_dir %s failed: %s\n",
					full_fname(dir), strerror(errno));
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
			set_filesystem(fname);

		send_file_name(f, flist, fname, recurse, XMIT_TOP_DIR);

		if (olddir[0]) {
			flist_dir = NULL;
			flist_dir_len = 0;
			if (!pop_dir(olddir)) {
				rprintf(FERROR, "pop_dir %s failed: %s\n",
					full_fname(dir), strerror(errno));
				exit_cleanup(RERR_FILESELECT);
			}
		}
	}

	if (f != -1) {
		send_file_entry(NULL, f, 0);

		if (show_filelist_p())
			finish_filelist_progress(flist);
	}

	if (flist->hlink_pool) {
		pool_destroy(flist->hlink_pool);
		flist->hlink_pool = NULL;
	}

	clean_flist(flist, 0, 0);

	if (f != -1) {
		/* Now send the uid/gid list. This was introduced in
		 * protocol version 15 */
		send_uid_list(f);

		/* send the io_error flag */
		write_int(f, lp_ignore_errors(module_id) ? 0 : io_error);

		io_end_buffering();
		stats.flist_size = stats.total_written - start_write;
		stats.num_files = flist->count;
		if (write_batch)
			write_batch_flist_info(flist->count, flist->files);
	}

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
	extern int list_only;

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
		int i = flist->count;

		flist_expand(flist);

		if (protocol_version >= 28 && (flags & XMIT_EXTENDED_FLAGS))
			flags |= read_byte(f) << 8;
		receive_file_entry(&flist->files[i], flags, flist, f);

		if (S_ISREG(flist->files[i]->mode))
			stats.total_size += flist->files[i]->length;

		flist->count++;

		maybe_emit_filelist_progress(flist);

		if (verbose > 2) {
			rprintf(FINFO, "recv_file_name(%s)\n",
				f_name(flist->files[i]));
		}
	}
	receive_file_entry(NULL, 0, NULL, 0); /* Signal that we're done. */

	if (verbose > 2)
		rprintf(FINFO, "received %d names\n", flist->count);

	if (show_filelist_p())
		finish_filelist_progress(flist);

	clean_flist(flist, relative_paths, 1);

	if (f != -1) {
		/* Now send the uid/gid list. This was introduced in
		 * protocol version 15 */
		recv_uid_list(f, flist);

		if (!read_batch) {
			/* Recv the io_error flag */
			if (lp_ignore_errors(module_id) || ignore_errors)
				read_int(f);
			else
				io_error |= read_int(f);
		}
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


int file_compare(struct file_struct **file1, struct file_struct **file2)
{
	struct file_struct *f1 = *file1;
	struct file_struct *f2 = *file2;

	if (!f1->basename && !f2->basename)
		return 0;
	if (!f1->basename)
		return -1;
	if (!f2->basename)
		return 1;
	if (f1->dirname == f2->dirname)
		return u_strcmp(f1->basename, f2->basename);
	return f_name_cmp(f1, f2);
}


int flist_find(struct file_list *flist, struct file_struct *f)
{
	int low = 0, high = flist->count - 1;

	while (high >= 0 && !flist->files[high]->basename) high--;

	if (high < 0)
		return -1;

	while (low != high) {
		int mid = (low + high) / 2;
		int ret = file_compare(&flist->files[flist_up(flist, mid)],&f);
		if (ret == 0)
			return flist_up(flist, mid);
		if (ret > 0)
			high = mid;
		else
			low = mid + 1;
	}

	if (file_compare(&flist->files[flist_up(flist, low)], &f) == 0)
		return flist_up(flist, low);
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

#if SUPPORT_HARD_LINKS
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

	if (!flist || flist->count == 0)
		return;

	qsort(flist->files, flist->count,
	    sizeof flist->files[0], (int (*)()) file_compare);

	for (i = no_dups? 0 : flist->count; i < flist->count; i++) {
		if (flist->files[i]->basename) {
			prev_i = i;
			break;
		}
	}
	while (++i < flist->count) {
		if (!flist->files[i]->basename)
			continue;
		if (f_name_cmp(flist->files[i], flist->files[prev_i]) == 0) {
			if (verbose > 1 && !am_server) {
				rprintf(FINFO,
					"removing duplicate name %s from file list %d\n",
					f_name(flist->files[i]), i);
			}
			/* Make sure that if we unduplicate '.', that we don't
			 * lose track of a user-specified starting point (or
			 * else deletions will mysteriously fail with -R). */
			if (flist->files[i]->flags & FLAG_TOP_DIR)
				flist->files[prev_i]->flags |= FLAG_TOP_DIR;

			clear_file(i, flist);
		} else
			prev_i = i;
	}

	if (strip_root) {
		/* we need to strip off the root directory in the case
		   of relative paths, but this must be done _after_
		   the sorting phase */
		for (i = 0; i < flist->count; i++) {
			if (flist->files[i]->dirname &&
			    flist->files[i]->dirname[0] == '/') {
				memmove(&flist->files[i]->dirname[0],
					&flist->files[i]->dirname[1],
					strlen(flist->files[i]->dirname));
			}

			if (flist->files[i]->dirname &&
			    !flist->files[i]->dirname[0]) {
				flist->files[i]->dirname = NULL;
			}
		}
	}
}

static void output_flist(struct file_list *flist)
{
	char uidbuf[16], gidbuf[16];
	struct file_struct *file;
	int i;

	for (i = 0; i < flist->count; i++) {
		file = flist->files[i];
		if (am_root && preserve_uid)
			sprintf(uidbuf, " uid=%ld", (long)file->uid);
		else
			*uidbuf = '\0';
		if (preserve_gid && file->gid != GID_NONE)
			sprintf(gidbuf, " gid=%ld", (long)file->gid);
		else
			*gidbuf = '\0';
		rprintf(FINFO, "[%s] i=%d %s %s %s mode=0%o len=%.0f%s%s\n",
			who_am_i(), i, NS(file->basedir), NS(file->dirname),
			NS(file->basename), (int) file->mode,
			(double) file->length, uidbuf, gidbuf);
	}
}


enum fnc_state { fnc_DIR, fnc_SLASH, fnc_BASE };

/* Compare the names of two file_struct entities, just like strcmp()
 * would do if it were operating on the joined strings.  We assume
 * that there are no 0-length strings.
 */
int f_name_cmp(struct file_struct *f1, struct file_struct *f2)
{
	int dif;
	const uchar *c1, *c2;
	enum fnc_state state1, state2;

	if (!f1 || !f1->basename) {
		if (!f2 || !f2->basename)
			return 0;
		return -1;
	}
	if (!f2 || !f2->basename)
		return 1;

	if (!(c1 = (uchar*)f1->dirname)) {
		state1 = fnc_BASE;
		c1 = (uchar*)f1->basename;
	} else if (!*c1) {
		state1 = fnc_SLASH;
		c1 = (uchar*)"/";
	} else
		state1 = fnc_DIR;
	if (!(c2 = (uchar*)f2->dirname)) {
		state2 = fnc_BASE;
		c2 = (uchar*)f2->basename;
	} else if (!*c2) {
		state2 = fnc_SLASH;
		c2 = (uchar*)"/";
	} else
		state2 = fnc_DIR;

	while (1) {
		if ((dif = (int)*c1 - (int)*c2) != 0)
			break;
		if (!*++c1) {
			switch (state1) {
			case fnc_DIR:
				state1 = fnc_SLASH;
				c1 = (uchar*)"/";
				break;
			case fnc_SLASH:
				state1 = fnc_BASE;
				c1 = (uchar*)f1->basename;
				break;
			case fnc_BASE:
				break;
			}
		}
		if (!*++c2) {
			switch (state2) {
			case fnc_DIR:
				state2 = fnc_SLASH;
				c2 = (uchar*)"/";
				break;
			case fnc_SLASH:
				state2 = fnc_BASE;
				c2 = (uchar*)f2->basename;
				break;
			case fnc_BASE:
				if (!*c1)
					return 0;
				break;
			}
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


/* Like f_name_to(), but we rotate through 5 static buffers of our own.
 */
char *f_name(struct file_struct *f)
{
	static char names[5][MAXPATHLEN];
	static unsigned int n;

	n = (n + 1) % (sizeof names / sizeof names[0]);

	return f_name_to(f, names[n]);
}
