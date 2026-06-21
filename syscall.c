/*
 * Syscall wrappers to ensure that nothing gets done in dry_run mode
 * and to handle system peculiarities.
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2002 Martin Pool
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

#ifdef HAVE_SYS_UN_H
#include <sys/un.h>	/* for the socket+bind() fallback in vfs_mknod() */
#endif
#ifdef HAVE_SYS_ATTR_H
#include <sys/attr.h>
#endif

#if defined HAVE_SYS_FALLOCATE && !defined HAVE_FALLOCATE
#include <sys/syscall.h>
#endif

#ifdef __linux__
#include <sys/syscall.h>	/* SYS_fchmodat2 / SYS_fallocate raw-syscall wrappers */
#endif

#include "ifuncs.h"
#include "vfs/vfs_internal.h"





#ifndef S_BLKSIZE
# if defined hpux || defined __hpux__ || defined __hpux
#  define S_BLKSIZE 1024
# elif defined _AIX && defined _I386
#  define S_BLKSIZE 4096
# else
#  define S_BLKSIZE 512
# endif
#endif














/* The logical current directory (maintained by change_dir() in util1.c).
 * Defined here -- rather than in util1.c -- so the test helpers that link
 * syscall.o but not util1.o (tls, trimslash) get the definition without a
 * weak-symbol fallback, which is not portable to PE/COFF targets (Cygwin). */










