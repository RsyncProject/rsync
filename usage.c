/*
 * Some usage & version related functions.
 *
 * Copyright (C) 2002-2022 Wayne Davison
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
#include "version.h"
#include "latest-year.h"
#include "git-version.h"
#include "default-cvsignore.h"
#include "itypes.h"

extern struct name_num_obj valid_checksums, valid_compressions, valid_auth_checksums;

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
	BOOL as_json = f == FNONE ? 1 : 0; /* We use 1 == first attribute, 2 == need closing array */
	char line_buf[75], item_buf[32];
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

#ifndef SUPPORT_LINKS
		"no "
#endif
			"symlinks",

#ifndef CAN_SET_SYMLINK_TIMES
		"no "
#endif
			"symtimes",

#ifndef SUPPORT_HARD_LINKS
		"no "
#endif
			"hardlinks",

#ifndef CAN_HARDLINK_SPECIAL
		"no "
#endif
			"hardlink-specials",

#ifndef CAN_HARDLINK_SYMLINK
		"no "
#endif
			"hardlink-symlinks",

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

#ifdef RSYNC_USE_SECLUDED_ARGS
		"default "
#else
		"optional "
#endif
			"secluded-args",

#ifndef ICONV_OPTION
		"no "
#endif
			"iconv",

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

#ifndef USE_ROLL_SIMD
		"no "
#endif
			"SIMD-roll",

#ifndef USE_ROLL_ASM
		"no "
#endif
			"asm-roll",

#ifndef USE_OPENSSL
		"no "
#endif
			"openssl-crypto",

#ifndef USE_MD5_ASM
		"no "
#endif
			"asm-MD5",

		NULL
	};

	for (line_len = 0, j = 0; ; j++) {
		char *str = info_flags[j], *next_nfo = str ? info_flags[j+1] : NULL;
		int need_comma = next_nfo && *next_nfo != '*' ? 1 : 0;
		int item_len;
		if (!str || *str == '*')
			item_len = 1000;
		else if (as_json) {
			char *space = strchr(str, ' ');
			int is_no = space && strncmp(str, "no ", 3) == 0;
			int is_bits = space && isDigit(str);
			char *quot = space && !is_no && !is_bits ? "\"" : "";
			char *item = space ? space + 1 : str;
			char *val = !space ? "true" : is_no ? "false" : str;
			int val_len = !space ? 4 : is_no ? 5 : space - str;
			if (is_bits && (space = strchr(val, '-')) != NULL)
			    val_len = space - str;
			item_len = snprintf(item_buf, sizeof item_buf,
					   " \"%s%s\": %s%.*s%s%s", item, is_bits ? "bits" : "",
					   quot, val_len, val, quot, need_comma ? "," : "");
			if (is_bits)
				item_buf[strlen(item)+2-1] = '_'; /* Turn the 's' into a '_' */
			for (space = item; (space = strpbrk(space, " -")) != NULL; space++)
				item_buf[space - item + 2] = '_';
		} else
			item_len = snprintf(item_buf, sizeof item_buf, " %s%s", str, need_comma ? "," : "");
		if (line_len && line_len + item_len >= (int)sizeof line_buf) {
			if (as_json)
				printf("   %s\n", line_buf);
			else
				rprintf(f, "   %s\n", line_buf);
			line_len = 0;
		}
		if (!str)
			break;
		if (*str == '*') {
			if (as_json) {
				if (as_json == 2)
					printf("  }");
				else
					as_json = 2;
				printf(",\n  \"%c%s\": {\n", toLower(str+1), str+2);
			} else
				rprintf(f, "%s:\n", str+1);
		} else {
			strlcpy(line_buf + line_len, item_buf, sizeof line_buf - line_len);
			line_len += item_len;
		}
	}
	if (as_json == 2)
		printf("  }");
}

static void output_nno_list(enum logcode f, const char *name, struct name_num_obj *nno)
{
	char namebuf[64], tmpbuf[256];
	char *tok, *next_tok, *comma = ",";
	char *cp;

	/* Using '(' ensures that we get a trailing "none" but also includes aliases. */
	get_default_nno_list(nno, tmpbuf, sizeof tmpbuf - 1, '(');
	if (f != FNONE) {
		rprintf(f, "%s:\n", name);
		rprintf(f, "    %s\n", tmpbuf);
		return;
	}

	strlcpy(namebuf, name, sizeof namebuf);
	for (cp = namebuf; *cp; cp++) {
		if (*cp == ' ')
			*cp = '_';
		else if (isUpper(cp))
			*cp = toLower(cp);
	}

	printf(",\n  \"%s\": [\n   ", namebuf);

	for (tok = strtok(tmpbuf, " "); tok; tok = next_tok) {
		next_tok = strtok(NULL, " ");
		if (*tok != '(') /* Ignore the alises in the JSON output */
			printf(" \"%s\"%s", tok, comma + (next_tok ? 0 : 1));
	}

	printf("\n  ]");
}

/* A request of f == FNONE wants json on stdout. */
void print_rsync_version(enum logcode f)
{
	char copyright[] = "(C) 1996-" LATEST_YEAR " by Andrew Tridgell, Wayne Davison, and others.";
	char url[] = "https://rsync.samba.org/";
	BOOL first_line = 1;

#define json_line(name, value) \
	do { \
		printf("%c\n  \"%s\": \"%s\"", first_line ? '{' : ',', name, value); \
		first_line = 0; \
	} while (0)

	if (f == FNONE) {
		char verbuf[32];
		json_line("program", RSYNC_NAME);
		json_line("version", rsync_version());
		(void)snprintf(verbuf, sizeof verbuf, "%d.%d", PROTOCOL_VERSION, SUBPROTOCOL_VERSION);
		json_line("protocol", verbuf);
		json_line("copyright", copyright);
		json_line("url", url);
	} else {
#if SUBPROTOCOL_VERSION != 0
		char *subprotocol = istring(".PR%d", SUBPROTOCOL_VERSION);
#else
		char *subprotocol = "";
#endif
		rprintf(f, "%s  version %s  protocol version %d%s\n",
			RSYNC_NAME, rsync_version(), PROTOCOL_VERSION, subprotocol);
		rprintf(f, "Copyright %s\n", copyright);
		rprintf(f, "Web site: %s\n", url);
	}

	print_info_flags(f);

	init_checksum_choices();

	output_nno_list(f, "Checksum list", &valid_checksums);
	output_nno_list(f, "Compress list", &valid_compressions);
	output_nno_list(f, "Daemon auth list", &valid_auth_checksums);

	if (f == FNONE) {
		json_line("license", "GPLv3");
		json_line("caveat", "rsync comes with ABSOLUTELY NO WARRANTY");
		printf("\n}\n");
		fflush(stdout);
		return;
	}

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
  rprintf(F,"Please see the rsync(1) and rsyncd.conf(5) manpages for full documentation.\n");
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
  rprintf(F,"daemon-specific rsync options.  See also the rsyncd.conf(5) manpage.\n");
}

const char *rsync_version(void)
{
	char *ver;
#ifdef RSYNC_GITVER
	ver = RSYNC_GITVER;
#else
	ver = RSYNC_VERSION;
#endif
	return *ver == 'v' ? ver+1 : ver;
}

const char *default_cvsignore(void)
{
	return DEFAULT_CVSIGNORE;
}
