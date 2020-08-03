/*
 * Some usage & version related functions.
 *
 * Copyright (C) 2002-2020 Wayne Davison
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
#include "latest-year.h"
#include "git-version.h"
#include "default-cvsignore.h"

extern struct name_num_obj valid_checksums;
extern struct name_num_obj valid_compressions;

static char *istring(const char *fmt, int val)
{
	char *str;
	if (asprintf(&str, fmt, val) < 0)
		out_of_memory("istring");
	return str;
}

static void print_info_flags(enum logcode f)
{
	STRUCT_STAT *dumstat;
	char line_buf[75];
	int line_len, j;
	char *info_flags[] = {

	"*Capabilities",

		istring("%d-bit files", (int)(sizeof (OFF_T) * 8)),
		istring("%d-bit inums", (int)(sizeof dumstat->st_ino * 8)), /* Don't check ino_t! */
		istring("%d-bit timestamps", (int)(sizeof (time_t) * 8)),
		istring("%d-bit long ints", (int)(sizeof (int64) * 8)),

#ifndef HAVE_SOCKETPAIR
		"no "
#endif
			"socketpairs",

#ifndef SUPPORT_HARD_LINKS
		"no "
#endif
			"hardlinks",

#ifndef CAN_HARDLINK_SPECIAL
		"no "
#endif
			"hardlink-specials",

#ifndef SUPPORT_LINKS
		"no "
#endif
			"symlinks",

#ifndef INET6
		"no "
#endif
			"IPv6",

#ifndef SUPPORT_ATIMES
		"no "
#endif
			"atimes",

		"batchfiles",

#ifndef HAVE_FTRUNCATE
		"no "
#endif
			"inplace",

#ifndef HAVE_FTRUNCATE
		"no "
#endif
			"append",

#ifndef SUPPORT_ACLS
		"no "
#endif
			"ACLs",

#ifndef SUPPORT_XATTRS
		"no "
#endif
			"xattrs",

#ifdef RSYNC_USE_PROTECTED_ARGS
		"default "
#else
		"optional "
#endif
			"protect-args",

#ifndef ICONV_OPTION
		"no "
#endif
			"iconv",

#ifndef CAN_SET_SYMLINK_TIMES
		"no "
#endif
			"symtimes",

#ifndef SUPPORT_PREALLOCATION
		"no "
#endif
			"prealloc",

#ifndef HAVE_MKTIME
		"no "
#endif
			"stop-at",

#ifndef SUPPORT_CRTIMES
		"no "
#endif
			"crtimes",

	"*Optimizations",

#ifndef HAVE_SIMD
		"no "
#endif
			"SIMD",

#ifndef HAVE_ASM
		"no "
#endif
			"asm",

#ifndef USE_OPENSSL
		"no "
#endif
			"openssl-crypto",

		NULL
	};

	for (line_len = 0, j = 0; ; j++) {
		char *str = info_flags[j], *next_nfo = str ? info_flags[j+1] : NULL;
		int str_len = str && *str != '*' ? strlen(str) : 1000;
		int need_comma = next_nfo && *next_nfo != '*' ? 1 : 0;
		if (line_len && line_len + 1 + str_len + need_comma >= (int)sizeof line_buf) {
			rprintf(f, "   %s\n", line_buf);
			line_len = 0;
		}
		if (!str)
			break;
		if (*str == '*') {
			rprintf(f, "%s:\n", str+1);
			continue;
		}
		line_len += snprintf(line_buf+line_len, sizeof line_buf - line_len, " %s%s", str, need_comma ? "," : "");
	}
}

void print_rsync_version(enum logcode f)
{
	char tmpbuf[256], *subprotocol = "";

#if SUBPROTOCOL_VERSION != 0
	subprotocol = istring(".PR%d", SUBPROTOCOL_VERSION);
#endif
	rprintf(f, "%s  version %s  protocol version %d%s\n",
		RSYNC_NAME, rsync_version(), PROTOCOL_VERSION, subprotocol);

	rprintf(f, "Copyright (C) 1996-" LATEST_YEAR " by Andrew Tridgell, Wayne Davison, and others.\n");
	rprintf(f, "Web site: https://rsync.samba.org/\n");

	print_info_flags(f);

	rprintf(f, "Checksum list:\n");
	get_default_nno_list(&valid_checksums, tmpbuf, sizeof tmpbuf, '(');
	rprintf(f, "    %s\n", tmpbuf);

	rprintf(f, "Compress list:\n");
	get_default_nno_list(&valid_compressions, tmpbuf, sizeof tmpbuf, '(');
	rprintf(f, "    %s\n", tmpbuf);

#ifdef MAINTAINER_MODE
	rprintf(f, "Panic Action: \"%s\"\n", get_panic_action());
#endif

#if SIZEOF_INT64 < 8
	rprintf(f, "WARNING: no 64-bit integers on this platform!\n");
#endif
	if (sizeof (int64) != SIZEOF_INT64) {
		rprintf(f,
			"WARNING: size mismatch in SIZEOF_INT64 define (%d != %d)\n",
			(int) SIZEOF_INT64, (int) sizeof (int64));
	}

	rprintf(f,"\n");
	rprintf(f,"rsync comes with ABSOLUTELY NO WARRANTY.  This is free software, and you\n");
	rprintf(f,"are welcome to redistribute it under certain conditions.  See the GNU\n");
	rprintf(f,"General Public Licence for details.\n");
}

void usage(enum logcode F)
{
  print_rsync_version(F);

  rprintf(F,"\n");
  rprintf(F,"rsync is a file transfer program capable of efficient remote update\n");
  rprintf(F,"via a fast differencing algorithm.\n");

  rprintf(F,"\n");
  rprintf(F,"Usage: rsync [OPTION]... SRC [SRC]... DEST\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... [USER@]HOST:DEST\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... [USER@]HOST::DEST\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... rsync://[USER@]HOST[:PORT]/DEST\n");
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST:SRC [DEST]\n");
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST::SRC [DEST]\n");
  rprintf(F,"  or   rsync [OPTION]... rsync://[USER@]HOST[:PORT]/SRC [DEST]\n");
  rprintf(F,"The ':' usages connect via remote shell, while '::' & 'rsync://' usages connect\n");
  rprintf(F,"to an rsync daemon, and require SRC or DEST to start with a module name.\n");
  rprintf(F,"\n");
  rprintf(F,"Options\n");
#include "help-rsync.h"
  rprintf(F,"\n");
  rprintf(F,"Use \"rsync --daemon --help\" to see the daemon-mode command-line options.\n");
  rprintf(F,"Please see the rsync(1) and rsyncd.conf(5) man pages for full documentation.\n");
  rprintf(F,"See https://rsync.samba.org/ for updates, bug reports, and answers\n");
}

void daemon_usage(enum logcode F)
{
  print_rsync_version(F);

  rprintf(F,"\n");
  rprintf(F,"Usage: rsync --daemon [OPTION]...\n");
#include "help-rsyncd.h"
  rprintf(F,"\n");
  rprintf(F,"If you were not trying to invoke rsync as a daemon, avoid using any of the\n");
  rprintf(F,"daemon-specific rsync options.  See also the rsyncd.conf(5) man page.\n");
}

const char *rsync_version(void)
{
	return RSYNC_GITVER;
}

const char *default_cvsignore(void)
{
	return DEFAULT_CVSIGNORE;
}
