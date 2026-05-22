/*
 * Deletion routines used in rsync.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2024 Wayne Davison
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

extern int am_root;
extern int make_backups;
extern int max_delete;
extern int force_change;
extern char *backup_dir;
extern char *backup_suffix;
extern int backup_suffix_len;
extern struct stats stats;

int ignore_perishable = 0;
int non_perishable_cnt = 0;
int skipped_deletes = 0;

static inline int is_backup_file(char *fn)
{
	int k = strlen(fn) - backup_suffix_len;
	return k > 0 && strcmp(fn+k, backup_suffix) == 0;
}

/* The directory is about to be deleted: if DEL_RECURSE is given, delete all
 * its contents, otherwise just checks for content.  Returns DR_SUCCESS or
 * DR_NOT_EMPTY.  Note that fname must point to a MAXPATHLEN buffer!  (The
 * buffer is used for recursion, but returned unchanged.)
 */
static enum delret delete_dir_contents(char *fname, uint16 flags)
{
	struct file_list *dirlist;
	enum delret ret;
	unsigned remainder;
	void *save_filters;
	int j, dlen;
	char *p;

	if (DEBUG_GTE(DEL, 3)) {
		rprintf(FINFO, "delete_dir_contents(%s) flags=%d\n",
			fname, flags);
	}

	dlen = strlen(fname);
	save_filters = push_local_filters(fname, dlen);

	non_perishable_cnt = 0;
	dirlist = get_dirlist(fname, dlen, 0);
	ret = non_perishable_cnt ? DR_NOT_EMPTY : DR_SUCCESS;

	if (!dirlist->used)
		goto done;

	if (!(flags & DEL_RECURSE)) {
		ret = DR_NOT_EMPTY;
		goto done;
	}

	p = fname + dlen;
	if (dlen != 1 || *fname != '/')
		*p++ = '/';
	remainder = MAXPATHLEN - (p - fname);

	/* We do our own recursion, so make delete_item() non-recursive. */
	flags = (flags & ~(DEL_RECURSE|DEL_MAKE_ROOM|DEL_NO_UID_WRITE))
	      | DEL_DIR_IS_EMPTY;

	for (j = dirlist->used; j--; ) {
		struct file_struct *fp = dirlist->files[j];

		if (fp->flags & FLAG_MOUNT_DIR && S_ISDIR(fp->mode)) {
			if (DEBUG_GTE(DEL, 1)) {
				rprintf(FINFO,
					"mount point, %s, pins parent directory\n",
					f_name(fp, NULL));
			}
			ret = DR_NOT_EMPTY;
			continue;
		}

		strlcpy(p, fp->basename, remainder);
#ifdef SUPPORT_FORCE_CHANGE
		/* For SUB-DIRECTORIES only: clear the immutable bits so the
		 * recursive delete_dir_contents below can unlink entries
		 * inside (the dir's +i blocks all add/remove operations on
		 * its children).  Track so we can undo on failure.
		 *
		 * Non-dirs are deliberately NOT pre-cleared here: the
		 * underlying do_unlink_at / do_rmdir_at force_change recovery
		 * uses fd-based fchflags, and that fd survives a rename or
		 * hardlink performed by make_backup() -- so the recovery
		 * correctly preserves the immutable flag on the inode after
		 * it lands at its backup location.  A path-based pre-clear
		 * here would lose the flag because our undo only knows the
		 * original path, which is gone after make_backup moves the
		 * inode into the backup tree. */
		int entry_unmuted = 0;
		uint32 entry_saved_flags = 0;
		if (S_ISDIR(fp->mode) && force_change
		 && (F_FFLAGS(fp) & force_change)) {
			if (make_mutable(fname, fp->mode, F_FFLAGS(fp), force_change) > 0) {
				entry_unmuted = 1;
				entry_saved_flags = F_FFLAGS(fp);
			}
		}
#endif
		if (!(fp->mode & S_IWUSR) && !am_root && fp->flags & FLAG_OWNED_BY_US)
			do_chmod_at(fname, fp->mode | S_IWUSR);
		/* Save stack by recursing to ourself directly. */
		if (S_ISDIR(fp->mode)) {
			if (delete_dir_contents(fname, flags | DEL_RECURSE) != DR_SUCCESS)
				ret = DR_NOT_EMPTY;
		}
		enum delret item_ret = delete_item(fname, fp->mode, flags);
		if (item_ret != DR_SUCCESS)
			ret = DR_NOT_EMPTY;
#ifdef SUPPORT_FORCE_CHANGE
		/* Restore the entry's flags if the delete didn't actually take
		 * place.  DR_SUCCESS means the inode is gone, no need to
		 * restore; everything else (DR_NOT_EMPTY, DR_AT_LIMIT,
		 * DR_FAILURE) leaves the file in place. */
		if (entry_unmuted && item_ret != DR_SUCCESS)
			undo_make_mutable(fname, entry_saved_flags);
#endif
	}

	fname[dlen] = '\0';

  done:
	flist_free(dirlist);
	pop_local_filters(save_filters);

	if (ret == DR_NOT_EMPTY) {
		rprintf(FINFO, "cannot delete non-empty directory: %s\n",
			fname);
	}
	return ret;
}

/* Delete a file or directory.  If DEL_RECURSE is set in the flags, this will
 * delete recursively.
 *
 * Note that fbuf must point to a MAXPATHLEN buffer if the mode indicates it's
 * a directory! (The buffer is used for recursion, but returned unchanged.)
 */
enum delret delete_item(char *fbuf, uint16 mode, uint16 flags)
{
	enum delret ret;
	char *what;
	int ok;
#ifdef SUPPORT_FORCE_CHANGE
	/* Track whether we cleared the dir's immutable bits so we can
	 * restore them if the directory ends up NOT being removed (delete
	 * skipped, contents non-empty, rmdir failed).  Without this the
	 * receiver would be left with a less-protected directory than it
	 * started with. */
	int dir_unmuted = 0;
	uint32 dir_saved_flags = 0;
#endif

	if (DEBUG_GTE(DEL, 2)) {
		rprintf(FINFO, "delete_item(%s) mode=%o flags=%d\n",
			fbuf, (int)mode, (int)flags);
	}

	if (flags & DEL_NO_UID_WRITE)
		do_chmod_at(fbuf, mode | S_IWUSR);

	if (S_ISDIR(mode) && !(flags & DEL_DIR_IS_EMPTY)) {
		/* This only happens on the first call to delete_item() since
		 * delete_dir_contents() always calls us w/DEL_DIR_IS_EMPTY. */
#ifdef SUPPORT_FORCE_CHANGE
		if (force_change) {
			STRUCT_STAT st;
			if (x_lstat(fbuf, &st, NULL) == 0) {
				uint32 ff = rsync_lgetflags(fbuf, st.st_mode, &st);
				if (ff != NO_FFLAGS && make_mutable(fbuf, st.st_mode, ff, force_change) > 0) {
					dir_unmuted = 1;
					dir_saved_flags = ff;
				}
			}
		}
#endif
		ignore_perishable = 1;
		/* If DEL_RECURSE is not set, this just reports emptiness. */
		ret = delete_dir_contents(fbuf, flags);
		ignore_perishable = 0;
		if (ret == DR_NOT_EMPTY || ret == DR_AT_LIMIT)
			goto check_ret;
		/* OK: try to delete the directory. */
	}

	if (!(flags & DEL_MAKE_ROOM) && max_delete >= 0 && stats.deleted_files >= max_delete) {
		skipped_deletes++;
		ret = DR_AT_LIMIT;
		goto check_ret;
	}

	if (S_ISDIR(mode)) {
		what = "rmdir";
		ok = do_rmdir_at(fbuf) == 0;
	} else {
		if (make_backups > 0 && !(flags & DEL_FOR_BACKUP) && (backup_dir || !is_backup_file(fbuf))) {
			what = "make_backup";
			ok = make_backup(fbuf, True);
			if (ok == 2) {
				what = "unlink";
				ok = robust_unlink(fbuf) == 0;
			}
		} else {
			what = "unlink";
			ok = robust_unlink(fbuf) == 0;
		}
	}

	if (ok) {
		if (!(flags & DEL_MAKE_ROOM)) {
			log_delete(fbuf, mode);
			stats.deleted_files++;
			if (S_ISREG(mode)) {
				/* Nothing more to count */
			} else if (S_ISDIR(mode))
				stats.deleted_dirs++;
#ifdef SUPPORT_LINKS
			else if (S_ISLNK(mode))
				stats.deleted_symlinks++;
#endif
			else if (IS_DEVICE(mode))
				stats.deleted_devices++;
			else
				stats.deleted_specials++;
		}
		ret = DR_SUCCESS;
	} else {
		if (S_ISDIR(mode) && errno == ENOTEMPTY) {
			rprintf(FINFO, "cannot delete non-empty directory: %s\n",
				fbuf);
			ret = DR_NOT_EMPTY;
		} else if (errno != ENOENT) {
			rsyserr(FERROR_XFER, errno, "delete_file: %s(%s) failed",
				what, fbuf);
			ret = DR_FAILURE;
		} else
			ret = DR_SUCCESS;
	}

  check_ret:
#ifdef SUPPORT_FORCE_CHANGE
	/* If we made the directory mutable but it's still present (delete
	 * skipped, contents non-empty, rmdir EPERM after recovery, etc.),
	 * restore its original flags so we don't leave the receiver with
	 * weaker protection than it started with. */
	if (dir_unmuted && ret != DR_SUCCESS)
		undo_make_mutable(fbuf, dir_saved_flags);
#endif
	if (ret != DR_SUCCESS && flags & DEL_MAKE_ROOM) {
		const char *desc;
		switch (flags & DEL_MAKE_ROOM) {
		case DEL_FOR_FILE: desc = "regular file"; break;
		case DEL_FOR_DIR: desc = "directory"; break;
		case DEL_FOR_SYMLINK: desc = "symlink"; break;
		case DEL_FOR_DEVICE: desc = "device file"; break;
		case DEL_FOR_SPECIAL: desc = "special file"; break;
		default: exit_cleanup(RERR_UNSUPPORTED); /* IMPOSSIBLE */
		}
		rprintf(FERROR_XFER, "could not make way for %s %s: %s\n",
			flags & DEL_FOR_BACKUP ? "backup" : "new",
			desc, fbuf);
	}
	return ret;
}

uint16 get_del_for_flag(uint16 mode)
{
	if (S_ISREG(mode))
		return DEL_FOR_FILE;
	if (S_ISDIR(mode))
		return DEL_FOR_DIR;
	if (S_ISLNK(mode))
		return DEL_FOR_SYMLINK;
	if (IS_DEVICE(mode))
		return DEL_FOR_DEVICE;
	if (IS_SPECIAL(mode))
		return DEL_FOR_SPECIAL;
	exit_cleanup(RERR_UNSUPPORTED); /* IMPOSSIBLE */
}
