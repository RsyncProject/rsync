/* 
   Copyright (C) Andrew Tridgell 1998
   
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

/*
  syscall wrappers to ensure that nothing gets done in dry_run mode
  */

#include "rsync.h"

extern int dry_run;
extern int read_only;
extern int list_only;

#define CHECK_RO if (read_only || list_only) {errno = EROFS; return -1;}

int do_unlink(char *fname)
{
	if (dry_run) return 0;
	CHECK_RO
	return unlink(fname);
}

int do_symlink(char *fname1, char *fname2)
{
	if (dry_run) return 0;
	CHECK_RO
	return symlink(fname1, fname2);
}

#if HAVE_LINK
int do_link(char *fname1, char *fname2)
{
	if (dry_run) return 0;
	CHECK_RO
	return link(fname1, fname2);
}
#endif

int do_lchown(const char *path, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	CHECK_RO
	return lchown(path, owner, group);
}

#if HAVE_MKNOD
int do_mknod(char *pathname, mode_t mode, dev_t dev)
{
	if (dry_run) return 0;
	CHECK_RO
	return mknod(pathname, mode, dev);
}
#endif

int do_rmdir(char *pathname)
{
	if (dry_run) return 0;
	CHECK_RO
	return rmdir(pathname);
}

int do_open(char *pathname, int flags, mode_t mode)
{
	if (dry_run) return -1;
#ifdef O_BINARY
	/* for Windows */
	flags |= O_BINARY;
#endif
	CHECK_RO
	return open(pathname, flags, mode);
}

#if HAVE_CHMOD
int do_chmod(const char *path, mode_t mode)
{
	if (dry_run) return 0;
	CHECK_RO
	return chmod(path, mode);
}
#endif

int do_rename(char *fname1, char *fname2)
{
	if (dry_run) return 0;
	CHECK_RO
	return rename(fname1, fname2);
}

int do_mkdir(char *fname, mode_t mode)
{
	if (dry_run) return 0;
	CHECK_RO
	return mkdir(fname, mode);
}

char *do_mktemp(char *template)
{
	if (dry_run) return NULL;
	if (read_only) {errno = EROFS; return NULL;}
	return mktemp(template);
}

int do_stat(const char *fname, STRUCT_STAT *st)
{
#if HAVE_OFF64_T
	return stat64(fname, st);
#else
	return stat(fname, st);
#endif
}

#if SUPPORT_LINKS
int do_lstat(const char *fname, STRUCT_STAT *st)
{
#if HAVE_OFF64_T
	return lstat64(fname, st);
#else
	return lstat(fname, st);
#endif
}
#endif

int do_fstat(int fd, STRUCT_STAT *st)
{
#if HAVE_OFF64_T
	return fstat64(fd, st);
#else
	return fstat(fd, st);
#endif
}

OFF_T do_lseek(int fd, OFF_T offset, int whence)
{
#if HAVE_OFF64_T
	off64_t lseek64();
	return lseek64(fd, offset, whence);
#else
	return lseek(fd, offset, whence);
#endif
}

#ifdef USE_MMAP
void *do_mmap(void *start, int len, int prot, int flags, int fd, OFF_T offset)
{
#if HAVE_OFF64_T
	return mmap64(start, len, prot, flags, fd, offset);
#else
	return mmap(start, len, prot, flags, fd, offset);
#endif
}
#endif

char *d_name(struct dirent *di)
{
#if HAVE_BROKEN_READDIR
	return (di->d_name - 2);
#else
	return di->d_name;
#endif
}
