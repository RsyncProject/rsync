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

int do_unlink(char *fname)
{
	if (dry_run) return 0;
	return unlink(fname);
}

int do_symlink(char *fname1, char *fname2)
{
	if (dry_run) return 0;
	return symlink(fname1, fname2);
}

int do_link(char *fname1, char *fname2)
{
	if (dry_run) return 0;
	return link(fname1, fname2);
}

int do_lchown(const char *path, uid_t owner, gid_t group)
{
	if (dry_run) return 0;
	return lchown(path, owner, group);
}

int do_mknod(char *pathname, mode_t mode, dev_t dev)
{
	if (dry_run) return 0;
	return mknod(pathname, mode, dev);
}

int do_rmdir(char *pathname)
{
	if (dry_run) return 0;
	return rmdir(pathname);
}

int do_open(char *pathname, int flags, mode_t mode)
{
	if (dry_run) return -1;
	return open(pathname, flags, mode);
}

int do_chmod(const char *path, mode_t mode)
{
	if (dry_run) return 0;
	return chmod(path, mode);
}
