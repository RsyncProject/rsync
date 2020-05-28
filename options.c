/*
 * Command-line (and received via daemon-socket) option parsing.
 *
 * Copyright (C) 1998-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2000, 2001, 2002 Martin Pool <mbp@samba.org>
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
#include "itypes.h"
#include "latest-year.h"
#include <popt.h>

extern int module_id;
extern int local_server;
extern int sanitize_paths;
extern int daemon_over_rsh;
extern unsigned int module_dirlen;
extern struct name_num_obj valid_checksums;
extern struct name_num_obj valid_compressions;
extern filter_rule_list filter_list;
extern filter_rule_list daemon_filter_list;

int make_backups = 0;

/**
 * If 1, send the whole file as literal data rather than trying to
 * create an incremental diff.
 *
 * If -1, then look at whether we're local or remote and go by that.
 *
 * @sa disable_deltas_p()
 **/
int whole_file = -1;

int append_mode = 0;
int keep_dirlinks = 0;
int copy_dirlinks = 0;
int copy_links = 0;
int write_devices = 0;
int preserve_links = 0;
int preserve_hard_links = 0;
int preserve_acls = 0;
int preserve_xattrs = 0;
int preserve_perms = 0;
int preserve_executability = 0;
int preserve_devices = 0;
int preserve_specials = 0;
int preserve_uid = 0;
int preserve_gid = 0;
int preserve_times = 0;
int preserve_atimes = 0;
int update_only = 0;
int open_noatime = 0;
int cvs_exclude = 0;
int dry_run = 0;
int do_xfers = 1;
int ignore_times = 0;
int delete_mode = 0;
int delete_during = 0;
int delete_before = 0;
int delete_after = 0;
int delete_excluded = 0;
int remove_source_files = 0;
int one_file_system = 0;
int protocol_version = PROTOCOL_VERSION;
int sparse_files = 0;
int preallocate_files = 0;
int do_compression = 0;
int do_compression_level = CLVL_NOT_SPECIFIED;
int am_root = 0; /* 0 = normal, 1 = root, 2 = --super, -1 = --fake-super */
int am_server = 0;
int am_sender = 0;
int am_starting_up = 1;
int relative_paths = -1;
int implied_dirs = 1;
int missing_args = 0; /* 0 = FERROR_XFER, 1 = ignore, 2 = delete */
int numeric_ids = 0;
int msgs2stderr = 0;
int allow_8bit_chars = 0;
int force_delete = 0;
int io_timeout = 0;
int prune_empty_dirs = 0;
int use_qsort = 0;
char *files_from = NULL;
int filesfrom_fd = -1;
char *filesfrom_host = NULL;
int eol_nulls = 0;
int protect_args = -1;
int human_readable = 1;
int recurse = 0;
int allow_inc_recurse = 1;
int xfer_dirs = -1;
int am_daemon = 0;
int connect_timeout = 0;
int keep_partial = 0;
int safe_symlinks = 0;
int copy_unsafe_links = 0;
int munge_symlinks = 0;
int size_only = 0;
int daemon_bwlimit = 0;
int bwlimit = 0;
int fuzzy_basis = 0;
size_t bwlimit_writemax = 0;
int ignore_existing = 0;
int ignore_non_existing = 0;
int need_messages_from_generator = 0;
int max_delete = INT_MIN;
OFF_T max_size = -1;
OFF_T min_size = -1;
int ignore_errors = 0;
int modify_window = 0;
int blocking_io = -1;
int checksum_seed = 0;
int inplace = 0;
int delay_updates = 0;
long block_size = 0; /* "long" because popt can't set an int32. */
char *skip_compress = NULL;
char *copy_as = NULL;
item_list dparam_list = EMPTY_ITEM_LIST;

/** Network address family. **/
int default_af_hint
#ifdef INET6
	= 0;		/* Any protocol */
#else
	= AF_INET;	/* Must use IPv4 */
# ifdef AF_INET6
#  undef AF_INET6
# endif
# define AF_INET6 AF_INET /* make -6 option a no-op */
#endif

/** Do not go into the background when run as --daemon.  Good
 * for debugging and required for running as a service on W32,
 * or under Unix process-monitors. **/
int no_detach
#if defined _WIN32 || defined __WIN32__
	= 1;
#else
	= 0;
#endif

int write_batch = 0;
int read_batch = 0;
int backup_dir_len = 0;
int backup_suffix_len;
unsigned int backup_dir_remainder;

char *backup_suffix = NULL;
char *tmpdir = NULL;
char *partial_dir = NULL;
char *basis_dir[MAX_BASIS_DIRS+1];
char *config_file = NULL;
char *shell_cmd = NULL;
char *logfile_name = NULL;
char *logfile_format = NULL;
char *stdout_format = NULL;
char *password_file = NULL;
char *rsync_path = RSYNC_PATH;
char *backup_dir = NULL;
char backup_dir_buf[MAXPATHLEN];
char *sockopts = NULL;
char *usermap = NULL;
char *groupmap = NULL;
int rsync_port = 0;
int compare_dest = 0;
int copy_dest = 0;
int link_dest = 0;
int basis_dir_cnt = 0;
char *dest_option = NULL;

static int remote_option_alloc = 0;
int remote_option_cnt = 0;
const char **remote_options = NULL;
const char *checksum_choice = NULL;
const char *compress_choice = NULL;

int quiet = 0;
int output_motd = 1;
int log_before_transfer = 0;
int stdout_format_has_i = 0;
int stdout_format_has_o_or_i = 0;
int logfile_format_has_i = 0;
int logfile_format_has_o_or_i = 0;
int always_checksum = 0;
int list_only = 0;

#define MAX_BATCH_NAME_LEN 256	/* Must be less than MAXPATHLEN-13 */
char *batch_name = NULL;

int need_unsorted_flist = 0;
#ifdef ICONV_OPTION
char *iconv_opt = ICONV_OPTION;
#endif

struct chmod_mode_struct *chmod_modes = NULL;

static const char *debug_verbosity[] = {
	/*0*/ NULL,
	/*1*/ NULL,
	/*2*/ "BIND,CMD,CONNECT,DEL,DELTASUM,DUP,FILTER,FLIST,ICONV",
	/*3*/ "ACL,BACKUP,CONNECT2,DELTASUM2,DEL2,EXIT,FILTER2,FLIST2,FUZZY,GENR,OWN,RECV,SEND,TIME",
	/*4*/ "CMD2,DELTASUM3,DEL3,EXIT2,FLIST3,ICONV2,OWN2,PROTO,TIME2",
	/*5*/ "CHDIR,DELTASUM4,FLIST4,FUZZY2,HASH,HLINK",
};

#define MAX_VERBOSITY ((int)(sizeof debug_verbosity / sizeof debug_verbosity[0]) - 1)

static const char *info_verbosity[1+MAX_VERBOSITY] = {
	/*0*/ NULL,
	/*1*/ "COPY,DEL,FLIST,MISC,NAME,STATS,SYMSAFE",
	/*2*/ "BACKUP,MISC2,MOUNT,NAME2,REMOVE,SKIP",
};

#define MAX_OUT_LEVEL 4 /* The largest N allowed for any flagN word. */

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

#define DEFAULT_PRIORITY 0 	/* Default/implied/--verbose set values. */
#define HELP_PRIORITY 1		/* The help output uses this level. */
#define USER_PRIORITY 2		/* User-specified via --info or --debug */
#define LIMIT_PRIORITY 3	/* Overriding priority when limiting values. */

#define W_CLI (1<<0)	/* client side */
#define W_SRV (1<<1)	/* server side */
#define W_SND (1<<2)	/* sending side */
#define W_REC (1<<3)	/* receiving side */

struct output_struct {
	char *name;	/* The name of the info/debug flag. */
	char *help;	/* The description of the info/debug flag. */
	uchar namelen;  /* The length of the name string. */
	uchar flag;	/* The flag's value, for consistency check. */
	uchar where;	/* Bits indicating where the flag is used. */
	uchar priority; /* See *_PRIORITY defines. */
};

#define INFO_WORD(flag, where, help) { #flag, help, sizeof #flag - 1, INFO_##flag, where, 0 }

static struct output_struct info_words[COUNT_INFO+1] = {
	INFO_WORD(BACKUP, W_REC, "Mention files backed up"),
	INFO_WORD(COPY, W_REC, "Mention files copied locally on the receiving side"),
	INFO_WORD(DEL, W_REC, "Mention deletions on the receiving side"),
	INFO_WORD(FLIST, W_CLI, "Mention file-list receiving/sending (levels 1-2)"),
	INFO_WORD(MISC, W_SND|W_REC, "Mention miscellaneous information (levels 1-2)"),
	INFO_WORD(MOUNT, W_SND|W_REC, "Mention mounts that were found or skipped"),
	INFO_WORD(NAME, W_SND|W_REC, "Mention 1) updated file/dir names, 2) unchanged names"),
	INFO_WORD(PROGRESS, W_CLI, "Mention 1) per-file progress or 2) total transfer progress"),
	INFO_WORD(REMOVE, W_SND, "Mention files removed on the sending side"),
	INFO_WORD(SKIP, W_REC, "Mention files that are skipped due to options used"),
	INFO_WORD(STATS, W_CLI|W_SRV, "Mention statistics at end of run (levels 1-3)"),
	INFO_WORD(SYMSAFE, W_SND|W_REC, "Mention symlinks that are unsafe"),
	{ NULL, "--info", 0, 0, 0, 0 }
};

#define DEBUG_WORD(flag, where, help) { #flag, help, sizeof #flag - 1, DEBUG_##flag, where, 0 }

static struct output_struct debug_words[COUNT_DEBUG+1] = {
	DEBUG_WORD(ACL, W_SND|W_REC, "Debug extra ACL info"),
	DEBUG_WORD(BACKUP, W_REC, "Debug backup actions (levels 1-2)"),
	DEBUG_WORD(BIND, W_CLI, "Debug socket bind actions"),
	DEBUG_WORD(CHDIR, W_CLI|W_SRV, "Debug when the current directory changes"),
	DEBUG_WORD(CONNECT, W_CLI, "Debug connection events (levels 1-2)"),
	DEBUG_WORD(CMD, W_CLI, "Debug commands+options that are issued (levels 1-2)"),
	DEBUG_WORD(DEL, W_REC, "Debug delete actions (levels 1-3)"),
	DEBUG_WORD(DELTASUM, W_SND|W_REC, "Debug delta-transfer checksumming (levels 1-4)"),
	DEBUG_WORD(DUP, W_REC, "Debug weeding of duplicate names"),
	DEBUG_WORD(EXIT, W_CLI|W_SRV, "Debug exit events (levels 1-3)"),
	DEBUG_WORD(FILTER, W_SND|W_REC, "Debug filter actions (levels 1-2)"),
	DEBUG_WORD(FLIST, W_SND|W_REC, "Debug file-list operations (levels 1-4)"),
	DEBUG_WORD(FUZZY, W_REC, "Debug fuzzy scoring (levels 1-2)"),
	DEBUG_WORD(GENR, W_REC, "Debug generator functions"),
	DEBUG_WORD(HASH, W_SND|W_REC, "Debug hashtable code"),
	DEBUG_WORD(HLINK, W_SND|W_REC, "Debug hard-link actions (levels 1-3)"),
	DEBUG_WORD(ICONV, W_CLI|W_SRV, "Debug iconv character conversions (levels 1-2)"),
	DEBUG_WORD(IO, W_CLI|W_SRV, "Debug I/O routines (levels 1-4)"),
	DEBUG_WORD(NSTR, W_CLI|W_SRV, "Debug negotiation strings"),
	DEBUG_WORD(OWN, W_REC, "Debug ownership changes in users & groups (levels 1-2)"),
	DEBUG_WORD(PROTO, W_CLI|W_SRV, "Debug protocol information"),
	DEBUG_WORD(RECV, W_REC, "Debug receiver functions"),
	DEBUG_WORD(SEND, W_SND, "Debug sender functions"),
	DEBUG_WORD(TIME, W_REC, "Debug setting of modified times (levels 1-2)"),
	{ NULL, "--debug", 0, 0, 0, 0 }
};

static int verbose = 0;
static int do_stats = 0;
static int do_progress = 0;
static int daemon_opt;   /* sets am_daemon after option error-reporting */
static int omit_dir_times = 0;
static int omit_link_times = 0;
static int F_option_cnt = 0;
static int modify_window_set;
static int itemize_changes = 0;
static int refused_delete, refused_archive_part, refused_compress;
static int refused_partial, refused_progress, refused_delete_before;
static int refused_delete_during;
static int refused_inplace, refused_no_iconv;
static BOOL usermap_via_chown, groupmap_via_chown;
#ifdef HAVE_SETVBUF
static char *outbuf_mode;
#endif
static char *bwlimit_arg, *max_size_arg, *min_size_arg;
static char tmp_partialdir[] = ".~tmp~";

/** Local address to bind.  As a character string because it's
 * interpreted by the IPv6 layer: should be a numeric IP4 or IP6
 * address, or a hostname. **/
char *bind_address;

static void output_item_help(struct output_struct *words);

/* This constructs a string that represents all the options set for either
 * the --info or --debug setting, skipping any implied options (by -v, etc.).
 * This is used both when conveying the user's options to the server, and
 * when the help output wants to tell the user what options are implied. */
static char *make_output_option(struct output_struct *words, short *levels, uchar where)
{
	char *str = words == info_words ? "--info=" : "--debug=";
	int j, counts[MAX_OUT_LEVEL+1], pos, skipped = 0, len = 0, max = 0, lev = 0;
	int word_count = words == info_words ? COUNT_INFO : COUNT_DEBUG;
	char *buf;

	memset(counts, 0, sizeof counts);

	for (j = 0; words[j].name; j++) {
		if (words[j].flag != j) {
			rprintf(FERROR, "rsync: internal error on %s%s: %d != %d\n",
				words == info_words ? "INFO_" : "DEBUG_",
				words[j].name, words[j].flag, j);
			exit_cleanup(RERR_UNSUPPORTED);
		}
		if (!(words[j].where & where))
			continue;
		if (words[j].priority == DEFAULT_PRIORITY) {
			/* Implied items don't need to be mentioned. */
			skipped++;
			continue;
		}
		len += len ? 1 : strlen(str);
		len += strlen(words[j].name);
		len += levels[j] == 1 ? 0 : 1;

		if (words[j].priority == HELP_PRIORITY)
			continue; /* no abbreviating for help */

		assert(levels[j] <= MAX_OUT_LEVEL);
		if (++counts[levels[j]] > max) {
			/* Determine which level has the most items. */
			lev = levels[j];
			max = counts[lev];
		}
	}

	/* Sanity check the COUNT_* define against the length of the table. */
	if (j != word_count) {
		rprintf(FERROR, "rsync: internal error: %s is wrong! (%d != %d)\n",
			words == info_words ? "COUNT_INFO" : "COUNT_DEBUG",
			j, word_count);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	if (!len)
		return NULL;

	len++;
	if (!(buf = new_array(char, len)))
		out_of_memory("make_output_option");
	pos = 0;

	if (skipped || max < 5)
		lev = -1;
	else {
		if (lev == 0)
			pos += snprintf(buf, len, "%sNONE", str);
		else if (lev == 1)
			pos += snprintf(buf, len, "%sALL", str);
		else
			pos += snprintf(buf, len, "%sALL%d", str, lev);
	}

	for (j = 0; words[j].name && pos < len; j++) {
		if (words[j].priority == DEFAULT_PRIORITY || levels[j] == lev || !(words[j].where & where))
			continue;
		if (pos)
			buf[pos++] = ',';
		else
			pos += strlcpy(buf+pos, str, len-pos);
		if (pos < len)
			pos += strlcpy(buf+pos, words[j].name, len-pos);
		/* Level 1 is implied by the name alone. */
		if (levels[j] != 1 && pos < len)
			buf[pos++] = '0' + levels[j];
	}

	buf[pos] = '\0';

	return buf;
}

static void parse_output_words(struct output_struct *words, short *levels,
			       const char *str, uchar priority)
{
	const char *s;
	int j, len, lev;

	for ( ; str; str = s) {
		if ((s = strchr(str, ',')) != NULL)
			len = s++ - str;
		else
			len = strlen(str);
		if (!len)
			continue;
		if (!isDigit(str)) {
			while (len && isDigit(str+len-1))
				len--;
		}
		lev = isDigit(str+len) ? atoi(str+len) : 1;
		if (lev > MAX_OUT_LEVEL)
			lev = MAX_OUT_LEVEL;
		if (len == 4 && strncasecmp(str, "help", 4) == 0) {
			output_item_help(words);
			exit_cleanup(0);
		}
		if (len == 4 && strncasecmp(str, "none", 4) == 0)
			len = lev = 0;
		else if (len == 3 && strncasecmp(str, "all", 3) == 0)
			len = 0;
		for (j = 0; words[j].name; j++) {
			if (!len
			 || (len == words[j].namelen && strncasecmp(str, words[j].name, len) == 0)) {
				if (priority >= words[j].priority) {
					words[j].priority = priority;
					levels[j] = lev;
				}
				if (len)
					break;
			}
		}
		if (len && !words[j].name && !am_server) {
			rprintf(FERROR, "Unknown %s item: \"%.*s\"\n",
				words[j].help, len, str);
			exit_cleanup(RERR_SYNTAX);
		}
	}
}

/* Tell the user what all the info or debug flags mean. */
static void output_item_help(struct output_struct *words)
{
	short *levels = words == info_words ? info_levels : debug_levels;
	const char **verbosity = words == info_words ? info_verbosity : debug_verbosity;
	char buf[128], *opt, *fmt = "%-10s %s\n";
	int j;

	reset_output_levels();

	rprintf(FINFO, "Use OPT or OPT1 for level 1 output, OPT2 for level 2, etc.; OPT0 silences.\n");
	rprintf(FINFO, "\n");
	for (j = 0; words[j].name; j++)
		rprintf(FINFO, fmt, words[j].name, words[j].help);
	rprintf(FINFO, "\n");

	snprintf(buf, sizeof buf, "Set all %s options (e.g. all%d)",
		 words[j].help, MAX_OUT_LEVEL);
	rprintf(FINFO, fmt, "ALL", buf);

	snprintf(buf, sizeof buf, "Silence all %s options (same as all0)",
		 words[j].help);
	rprintf(FINFO, fmt, "NONE", buf);

	rprintf(FINFO, fmt, "HELP", "Output this help message");
	rprintf(FINFO, "\n");
	rprintf(FINFO, "Options added for each increase in verbose level:\n");

	for (j = 1; j <= MAX_VERBOSITY; j++) {
		parse_output_words(words, levels, verbosity[j], HELP_PRIORITY);
		opt = make_output_option(words, levels, W_CLI|W_SRV|W_SND|W_REC);
		if (opt) {
			rprintf(FINFO, "%d) %s\n", j, strchr(opt, '=')+1);
			free(opt);
		}
		reset_output_levels();
	}
}

/* The --verbose option now sets info+debug flags. */
static void set_output_verbosity(int level, uchar priority)
{
	int j;

	if (level > MAX_VERBOSITY)
		level = MAX_VERBOSITY;

	for (j = 1; j <= level; j++) {
		parse_output_words(info_words, info_levels, info_verbosity[j], priority);
		parse_output_words(debug_words, debug_levels, debug_verbosity[j], priority);
	}
}

/* Limit the info+debug flag levels given a verbose-option level limit. */
void limit_output_verbosity(int level)
{
	short info_limits[COUNT_INFO], debug_limits[COUNT_DEBUG];
	int j;

	if (level > MAX_VERBOSITY)
		return;

	memset(info_limits, 0, sizeof info_limits);
	memset(debug_limits, 0, sizeof debug_limits);

	/* Compute the level limits in the above arrays. */
	for (j = 1; j <= level; j++) {
		parse_output_words(info_words, info_limits, info_verbosity[j], LIMIT_PRIORITY);
		parse_output_words(debug_words, debug_limits, debug_verbosity[j], LIMIT_PRIORITY);
	}

	for (j = 0; j < COUNT_INFO; j++) {
		if (info_levels[j] > info_limits[j])
			info_levels[j] = info_limits[j];
	}

	for (j = 0; j < COUNT_DEBUG; j++) {
		if (debug_levels[j] > debug_limits[j])
			debug_levels[j] = debug_limits[j];
	}
}

void reset_output_levels(void)
{
	int j;

	memset(info_levels, 0, sizeof info_levels);
	memset(debug_levels, 0, sizeof debug_levels);

	for (j = 0; j < COUNT_INFO; j++)
		info_words[j].priority = DEFAULT_PRIORITY;

	for (j = 0; j < COUNT_DEBUG; j++)
		debug_words[j].priority = DEFAULT_PRIORITY;
}

void negate_output_levels(void)
{
	int j;

	for (j = 0; j < COUNT_INFO; j++)
		info_levels[j] *= -1;

	for (j = 0; j < COUNT_DEBUG; j++)
		debug_levels[j] *= -1;
}

static char *istring(const char *fmt, int val)
{
	char *str;
	if (asprintf(&str, fmt, val) < 0)
		out_of_memory("istring");
	return str;
}

static void print_capabilities(enum logcode f)
{
	STRUCT_STAT *dumstat;
	char line_buf[75];
	int line_len, j;
	char *capabilities[] = {
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

#ifndef SUPPORT_LINKS
		"no "
#endif
			"symlinks",

#ifndef INET6
		"no "
#endif
			"IPv6",

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

#ifndef HAVE_SIMD
		"no "
#endif
			"SIMD",

		NULL
	};

	for (line_len = 0, j = 0; ; j++) {
		char *cap = capabilities[j];
		int cap_len = cap ? strlen(cap) : 1000;
		int need_comma = cap && capabilities[j+1] != NULL ? 1 : 0;
		if (line_len + 1 + cap_len + need_comma >= (int)sizeof line_buf) {
			rprintf(f, "   %s\n", line_buf);
			line_len = 0;
		}
		if (!cap)
			break;
		line_len += snprintf(line_buf+line_len, sizeof line_buf - line_len, " %s%s", cap, need_comma ? "," : "");
	}
}

static void print_rsync_version(enum logcode f)
{
	char tmpbuf[256], *subprotocol = "";

#if SUBPROTOCOL_VERSION != 0
	subprotocol = istring(".PR%d", SUBPROTOCOL_VERSION);
#endif
	rprintf(f, "%s  version %s  protocol version %d%s\n",
		RSYNC_NAME, RSYNC_VERSION, PROTOCOL_VERSION, subprotocol);

	rprintf(f, "Copyright (C) 1996-" LATEST_YEAR " by Andrew Tridgell, Wayne Davison, and others.\n");
	rprintf(f, "Web site: http://rsync.samba.org/\n");

	rprintf(f, "Capabilities:\n");
	print_capabilities(f);

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
  rprintf(F," -v, --verbose               increase verbosity\n");
  rprintf(F,"     --info=FLAGS            fine-grained informational verbosity\n");
  rprintf(F,"     --debug=FLAGS           fine-grained debug verbosity\n");
  rprintf(F,"     --msgs2stderr           special output handling for debugging\n");
  rprintf(F," -q, --quiet                 suppress non-error messages\n");
  rprintf(F,"     --no-motd               suppress daemon-mode MOTD (see manpage caveat)\n");
  rprintf(F," -c, --checksum              skip based on checksum, not mod-time & size\n");
  rprintf(F," -a, --archive               archive mode; equals -rlptgoD (no -H,-A,-X)\n");
  rprintf(F,"     --no-OPTION             turn off an implied OPTION (e.g. --no-D)\n");
  rprintf(F," -r, --recursive             recurse into directories\n");
  rprintf(F," -R, --relative              use relative path names\n");
  rprintf(F,"     --no-implied-dirs       don't send implied dirs with --relative\n");
  rprintf(F," -b, --backup                make backups (see --suffix & --backup-dir)\n");
  rprintf(F,"     --backup-dir=DIR        make backups into hierarchy based in DIR\n");
  rprintf(F,"     --suffix=SUFFIX         set backup suffix (default %s w/o --backup-dir)\n",BACKUP_SUFFIX);
  rprintf(F," -u, --update                skip files that are newer on the receiver\n");
  rprintf(F,"     --inplace               update destination files in-place (SEE MAN PAGE)\n");
  rprintf(F,"     --append                append data onto shorter files\n");
  rprintf(F,"     --append-verify         like --append, but with old data in file checksum\n");
  rprintf(F," -d, --dirs                  transfer directories without recursing\n");
  rprintf(F," -l, --links                 copy symlinks as symlinks\n");
  rprintf(F," -L, --copy-links            transform symlink into referent file/dir\n");
  rprintf(F,"     --copy-unsafe-links     only \"unsafe\" symlinks are transformed\n");
  rprintf(F,"     --safe-links            ignore symlinks that point outside the source tree\n");
  rprintf(F,"     --munge-links           munge symlinks to make them safer (but unusable)\n");
  rprintf(F," -k, --copy-dirlinks         transform symlink to a dir into referent dir\n");
  rprintf(F," -K, --keep-dirlinks         treat symlinked dir on receiver as dir\n");
  rprintf(F," -H, --hard-links            preserve hard links\n");
  rprintf(F," -p, --perms                 preserve permissions\n");
  rprintf(F," -E, --executability         preserve the file's executability\n");
  rprintf(F,"     --chmod=CHMOD           affect file and/or directory permissions\n");
#ifdef SUPPORT_ACLS
  rprintf(F," -A, --acls                  preserve ACLs (implies --perms)\n");
#endif
#ifdef SUPPORT_XATTRS
  rprintf(F," -X, --xattrs                preserve extended attributes\n");
#endif
  rprintf(F," -o, --owner                 preserve owner (super-user only)\n");
  rprintf(F," -g, --group                 preserve group\n");
  rprintf(F,"     --devices               preserve device files (super-user only)\n");
  rprintf(F,"     --specials              preserve special files\n");
  rprintf(F," -D                          same as --devices --specials\n");
  rprintf(F," -t, --times                 preserve modification times\n");
  rprintf(F," -U, --atimes                preserve access (last-used) times\n");
  rprintf(F,"     --open-noatime          avoid changing the atime on opened files\n");
  rprintf(F," -O, --omit-dir-times        omit directories from --times\n");
  rprintf(F," -J, --omit-link-times       omit symlinks from --times\n");
  rprintf(F,"     --super                 receiver attempts super-user activities\n");
#ifdef SUPPORT_XATTRS
  rprintf(F,"     --fake-super            store/recover privileged attrs using xattrs\n");
#endif
  rprintf(F," -S, --sparse                turn sequences of nulls into sparse blocks\n");
#ifdef SUPPORT_PREALLOCATION
  rprintf(F,"     --preallocate           allocate dest files before writing them\n");
#else
  rprintf(F,"     --preallocate           pre-allocate dest files on remote receiver\n");
#endif
  rprintf(F,"     --write-devices         write to devices as files (implies --inplace)\n");
  rprintf(F," -n, --dry-run               perform a trial run with no changes made\n");
  rprintf(F," -W, --whole-file            copy files whole (without delta-xfer algorithm)\n");
  rprintf(F,"     --checksum-choice=STR   choose the checksum algorithms\n");
  rprintf(F," -x, --one-file-system       don't cross filesystem boundaries\n");
  rprintf(F," -B, --block-size=SIZE       force a fixed checksum block-size\n");
  rprintf(F," -e, --rsh=COMMAND           specify the remote shell to use\n");
  rprintf(F,"     --rsync-path=PROGRAM    specify the rsync to run on the remote machine\n");
  rprintf(F,"     --existing              skip creating new files on receiver\n");
  rprintf(F,"     --ignore-existing       skip updating files that already exist on receiver\n");
  rprintf(F,"     --remove-source-files   sender removes synchronized files (non-dirs)\n");
  rprintf(F,"     --del                   an alias for --delete-during\n");
  rprintf(F,"     --delete                delete extraneous files from destination dirs\n");
  rprintf(F,"     --delete-before         receiver deletes before transfer, not during\n");
  rprintf(F,"     --delete-during         receiver deletes during the transfer\n");
  rprintf(F,"     --delete-delay          find deletions during, delete after\n");
  rprintf(F,"     --delete-after          receiver deletes after transfer, not during\n");
  rprintf(F,"     --delete-excluded       also delete excluded files from destination dirs\n");
  rprintf(F,"     --ignore-missing-args   ignore missing source args without error\n");
  rprintf(F,"     --delete-missing-args   delete missing source args from destination\n");
  rprintf(F,"     --ignore-errors         delete even if there are I/O errors\n");
  rprintf(F,"     --force                 force deletion of directories even if not empty\n");
  rprintf(F,"     --max-delete=NUM        don't delete more than NUM files\n");
  rprintf(F,"     --max-size=SIZE         don't transfer any file larger than SIZE\n");
  rprintf(F,"     --min-size=SIZE         don't transfer any file smaller than SIZE\n");
  rprintf(F,"     --partial               keep partially transferred files\n");
  rprintf(F,"     --partial-dir=DIR       put a partially transferred file into DIR\n");
  rprintf(F,"     --delay-updates         put all updated files into place at transfer's end\n");
  rprintf(F," -m, --prune-empty-dirs      prune empty directory chains from the file-list\n");
  rprintf(F,"     --numeric-ids           don't map uid/gid values by user/group name\n");
  rprintf(F,"     --usermap=STRING        custom username mapping\n");
  rprintf(F,"     --groupmap=STRING       custom groupname mapping\n");
  rprintf(F,"     --chown=USER:GROUP      simple username/groupname mapping\n");
  rprintf(F,"     --timeout=SECONDS       set I/O timeout in seconds\n");
  rprintf(F,"     --contimeout=SECONDS    set daemon connection timeout in seconds\n");
  rprintf(F," -I, --ignore-times          don't skip files that match in size and mod-time\n");
  rprintf(F," -M, --remote-option=OPTION  send OPTION to the remote side only\n");
  rprintf(F,"     --size-only             skip files that match in size\n");
  rprintf(F," -@, --modify-window=NUM     set the accuracy for mod-time comparisons\n");
  rprintf(F," -T, --temp-dir=DIR          create temporary files in directory DIR\n");
  rprintf(F," -y, --fuzzy                 find similar file for basis if no dest file\n");
  rprintf(F,"     --compare-dest=DIR      also compare destination files relative to DIR\n");
  rprintf(F,"     --copy-dest=DIR         ... and include copies of unchanged files\n");
  rprintf(F,"     --link-dest=DIR         hardlink to files in DIR when unchanged\n");
  rprintf(F," -z, --compress              compress file data during the transfer\n");
  rprintf(F,"     --compress-level=NUM    explicitly set compression level\n");
  rprintf(F,"     --skip-compress=LIST    skip compressing files with a suffix in LIST\n");
  rprintf(F," -C, --cvs-exclude           auto-ignore files the same way CVS does\n");
  rprintf(F," -f, --filter=RULE           add a file-filtering RULE\n");
  rprintf(F," -F                          same as --filter='dir-merge /.rsync-filter'\n");
  rprintf(F,"                             repeated: --filter='- .rsync-filter'\n");
  rprintf(F,"     --exclude=PATTERN       exclude files matching PATTERN\n");
  rprintf(F,"     --exclude-from=FILE     read exclude patterns from FILE\n");
  rprintf(F,"     --include=PATTERN       don't exclude files matching PATTERN\n");
  rprintf(F,"     --include-from=FILE     read include patterns from FILE\n");
  rprintf(F,"     --files-from=FILE       read list of source-file names from FILE\n");
  rprintf(F," -0, --from0                 all *-from/filter files are delimited by 0s\n");
  rprintf(F," -s, --protect-args          no space-splitting; only wildcard special-chars\n");
  rprintf(F,"     --copy-as=USER[:GROUP]  specify user & optional group for the copy\n");
  rprintf(F,"     --address=ADDRESS       bind address for outgoing socket to daemon\n");
  rprintf(F,"     --port=PORT             specify double-colon alternate port number\n");
  rprintf(F,"     --sockopts=OPTIONS      specify custom TCP options\n");
  rprintf(F,"     --blocking-io           use blocking I/O for the remote shell\n");
  rprintf(F,"     --stats                 give some file-transfer stats\n");
  rprintf(F," -8, --8-bit-output          leave high-bit chars unescaped in output\n");
  rprintf(F," -h, --human-readable        output numbers in a human-readable format\n");
  rprintf(F,"     --progress              show progress during transfer\n");
  rprintf(F," -P                          same as --partial --progress\n");
  rprintf(F," -i, --itemize-changes       output a change-summary for all updates\n");
  rprintf(F,"     --out-format=FORMAT     output updates using the specified FORMAT\n");
  rprintf(F,"     --log-file=FILE         log what we're doing to the specified FILE\n");
  rprintf(F,"     --log-file-format=FMT   log updates using the specified FMT\n");
  rprintf(F,"     --password-file=FILE    read daemon-access password from FILE\n");
  rprintf(F,"     --list-only             list the files instead of copying them\n");
  rprintf(F,"     --bwlimit=RATE          limit socket I/O bandwidth\n");
#ifdef HAVE_SETVBUF
  rprintf(F,"     --outbuf=N|L|B          set output buffering to None, Line, or Block\n");
#endif
  rprintf(F,"     --write-batch=FILE      write a batched update to FILE\n");
  rprintf(F,"     --only-write-batch=FILE like --write-batch but w/o updating destination\n");
  rprintf(F,"     --read-batch=FILE       read a batched update from FILE\n");
  rprintf(F,"     --protocol=NUM          force an older protocol version to be used\n");
#ifdef ICONV_OPTION
  rprintf(F,"     --iconv=CONVERT_SPEC    request charset conversion of filenames\n");
#endif
  rprintf(F,"     --checksum-seed=NUM     set block/file checksum seed (advanced)\n");
  rprintf(F," -4, --ipv4                  prefer IPv4\n");
  rprintf(F," -6, --ipv6                  prefer IPv6\n");
  rprintf(F,"     --version               print version number\n");
  rprintf(F,"(-h) --help                  show this help (-h is --help only if used alone)\n");

  rprintf(F,"\n");
  rprintf(F,"Use \"rsync --daemon --help\" to see the daemon-mode command-line options.\n");
  rprintf(F,"Please see the rsync(1) and rsyncd.conf(5) man pages for full documentation.\n");
  rprintf(F,"See http://rsync.samba.org/ for updates, bug reports, and answers\n");
}

enum {OPT_VERSION = 1000, OPT_DAEMON, OPT_SENDER, OPT_EXCLUDE, OPT_EXCLUDE_FROM,
      OPT_FILTER, OPT_COMPARE_DEST, OPT_COPY_DEST, OPT_LINK_DEST, OPT_HELP,
      OPT_INCLUDE, OPT_INCLUDE_FROM, OPT_MODIFY_WINDOW, OPT_MIN_SIZE, OPT_CHMOD,
      OPT_READ_BATCH, OPT_WRITE_BATCH, OPT_ONLY_WRITE_BATCH, OPT_MAX_SIZE,
      OPT_NO_D, OPT_APPEND, OPT_NO_ICONV, OPT_INFO, OPT_DEBUG,
      OPT_USERMAP, OPT_GROUPMAP, OPT_CHOWN, OPT_BWLIMIT,
      OPT_OLD_COMPRESS, OPT_NEW_COMPRESS, OPT_NO_COMPRESS,
      OPT_SERVER, OPT_REFUSED_BASE = 9000};

static struct poptOption long_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"help",             0,  POPT_ARG_NONE,   0, OPT_HELP, 0, 0 },
  {"version",          0,  POPT_ARG_NONE,   0, OPT_VERSION, 0, 0},
  {"verbose",         'v', POPT_ARG_NONE,   0, 'v', 0, 0 },
  {"no-verbose",       0,  POPT_ARG_VAL,    &verbose, 0, 0, 0 },
  {"no-v",             0,  POPT_ARG_VAL,    &verbose, 0, 0, 0 },
  {"info",             0,  POPT_ARG_STRING, 0, OPT_INFO, 0, 0 },
  {"debug",            0,  POPT_ARG_STRING, 0, OPT_DEBUG, 0, 0 },
  {"msgs2stderr",      0,  POPT_ARG_NONE,   &msgs2stderr, 0, 0, 0 },
  {"quiet",           'q', POPT_ARG_NONE,   0, 'q', 0, 0 },
  {"motd",             0,  POPT_ARG_VAL,    &output_motd, 1, 0, 0 },
  {"no-motd",          0,  POPT_ARG_VAL,    &output_motd, 0, 0, 0 },
  {"stats",            0,  POPT_ARG_NONE,   &do_stats, 0, 0, 0 },
  {"human-readable",  'h', POPT_ARG_NONE,   0, 'h', 0, 0},
  {"no-human-readable",0,  POPT_ARG_VAL,    &human_readable, 0, 0, 0},
  {"no-h",             0,  POPT_ARG_VAL,    &human_readable, 0, 0, 0},
  {"dry-run",         'n', POPT_ARG_NONE,   &dry_run, 0, 0, 0 },
  {"archive",         'a', POPT_ARG_NONE,   0, 'a', 0, 0 },
  {"recursive",       'r', POPT_ARG_VAL,    &recurse, 2, 0, 0 },
  {"no-recursive",     0,  POPT_ARG_VAL,    &recurse, 0, 0, 0 },
  {"no-r",             0,  POPT_ARG_VAL,    &recurse, 0, 0, 0 },
  {"inc-recursive",    0,  POPT_ARG_VAL,    &allow_inc_recurse, 1, 0, 0 },
  {"no-inc-recursive", 0,  POPT_ARG_VAL,    &allow_inc_recurse, 0, 0, 0 },
  {"i-r",              0,  POPT_ARG_VAL,    &allow_inc_recurse, 1, 0, 0 },
  {"no-i-r",           0,  POPT_ARG_VAL,    &allow_inc_recurse, 0, 0, 0 },
  {"dirs",            'd', POPT_ARG_VAL,    &xfer_dirs, 2, 0, 0 },
  {"no-dirs",          0,  POPT_ARG_VAL,    &xfer_dirs, 0, 0, 0 },
  {"no-d",             0,  POPT_ARG_VAL,    &xfer_dirs, 0, 0, 0 },
  {"old-dirs",         0,  POPT_ARG_VAL,    &xfer_dirs, 4, 0, 0 },
  {"old-d",            0,  POPT_ARG_VAL,    &xfer_dirs, 4, 0, 0 },
  {"perms",           'p', POPT_ARG_VAL,    &preserve_perms, 1, 0, 0 },
  {"no-perms",         0,  POPT_ARG_VAL,    &preserve_perms, 0, 0, 0 },
  {"no-p",             0,  POPT_ARG_VAL,    &preserve_perms, 0, 0, 0 },
  {"executability",   'E', POPT_ARG_NONE,   &preserve_executability, 0, 0, 0 },
  {"acls",            'A', POPT_ARG_NONE,   0, 'A', 0, 0 },
  {"no-acls",          0,  POPT_ARG_VAL,    &preserve_acls, 0, 0, 0 },
  {"no-A",             0,  POPT_ARG_VAL,    &preserve_acls, 0, 0, 0 },
  {"xattrs",          'X', POPT_ARG_NONE,   0, 'X', 0, 0 },
  {"no-xattrs",        0,  POPT_ARG_VAL,    &preserve_xattrs, 0, 0, 0 },
  {"no-X",             0,  POPT_ARG_VAL,    &preserve_xattrs, 0, 0, 0 },
  {"times",           't', POPT_ARG_VAL,    &preserve_times, 1, 0, 0 },
  {"no-times",         0,  POPT_ARG_VAL,    &preserve_times, 0, 0, 0 },
  {"no-t",             0,  POPT_ARG_VAL,    &preserve_times, 0, 0, 0 },
  {"atimes",          'U', POPT_ARG_NONE,   0, 'U', 0, 0 },
  {"no-atimes",        0,  POPT_ARG_VAL,    &preserve_atimes, 0, 0, 0 },
  {"no-U",             0,  POPT_ARG_VAL,    &preserve_atimes, 0, 0, 0 },
  {"open-noatime",     0,  POPT_ARG_VAL,    &open_noatime, 1, 0, 0 },
  {"no-open-noatime",  0,  POPT_ARG_VAL,    &open_noatime, 0, 0, 0 },
  {"omit-dir-times",  'O', POPT_ARG_VAL,    &omit_dir_times, 1, 0, 0 },
  {"no-omit-dir-times",0,  POPT_ARG_VAL,    &omit_dir_times, 0, 0, 0 },
  {"no-O",             0,  POPT_ARG_VAL,    &omit_dir_times, 0, 0, 0 },
  {"omit-link-times", 'J', POPT_ARG_VAL,    &omit_link_times, 1, 0, 0 },
  {"no-omit-link-times",0, POPT_ARG_VAL,    &omit_link_times, 0, 0, 0 },
  {"no-J",             0,  POPT_ARG_VAL,    &omit_link_times, 0, 0, 0 },
  {"modify-window",   '@', POPT_ARG_INT,    &modify_window, OPT_MODIFY_WINDOW, 0, 0 },
  {"super",            0,  POPT_ARG_VAL,    &am_root, 2, 0, 0 },
  {"no-super",         0,  POPT_ARG_VAL,    &am_root, 0, 0, 0 },
  {"fake-super",       0,  POPT_ARG_VAL,    &am_root, -1, 0, 0 },
  {"owner",           'o', POPT_ARG_VAL,    &preserve_uid, 1, 0, 0 },
  {"no-owner",         0,  POPT_ARG_VAL,    &preserve_uid, 0, 0, 0 },
  {"no-o",             0,  POPT_ARG_VAL,    &preserve_uid, 0, 0, 0 },
  {"group",           'g', POPT_ARG_VAL,    &preserve_gid, 1, 0, 0 },
  {"no-group",         0,  POPT_ARG_VAL,    &preserve_gid, 0, 0, 0 },
  {"no-g",             0,  POPT_ARG_VAL,    &preserve_gid, 0, 0, 0 },
  {0,                 'D', POPT_ARG_NONE,   0, 'D', 0, 0 },
  {"no-D",             0,  POPT_ARG_NONE,   0, OPT_NO_D, 0, 0 },
  {"devices",          0,  POPT_ARG_VAL,    &preserve_devices, 1, 0, 0 },
  {"no-devices",       0,  POPT_ARG_VAL,    &preserve_devices, 0, 0, 0 },
  {"write-devices",    0,  POPT_ARG_VAL,    &write_devices, 1, 0, 0 },
  {"no-write-devices", 0,  POPT_ARG_VAL,    &write_devices, 0, 0, 0 },
  {"specials",         0,  POPT_ARG_VAL,    &preserve_specials, 1, 0, 0 },
  {"no-specials",      0,  POPT_ARG_VAL,    &preserve_specials, 0, 0, 0 },
  {"links",           'l', POPT_ARG_VAL,    &preserve_links, 1, 0, 0 },
  {"no-links",         0,  POPT_ARG_VAL,    &preserve_links, 0, 0, 0 },
  {"no-l",             0,  POPT_ARG_VAL,    &preserve_links, 0, 0, 0 },
  {"copy-links",      'L', POPT_ARG_NONE,   &copy_links, 0, 0, 0 },
  {"copy-unsafe-links",0,  POPT_ARG_NONE,   &copy_unsafe_links, 0, 0, 0 },
  {"safe-links",       0,  POPT_ARG_NONE,   &safe_symlinks, 0, 0, 0 },
  {"munge-links",      0,  POPT_ARG_VAL,    &munge_symlinks, 1, 0, 0 },
  {"no-munge-links",   0,  POPT_ARG_VAL,    &munge_symlinks, 0, 0, 0 },
  {"copy-dirlinks",   'k', POPT_ARG_NONE,   &copy_dirlinks, 0, 0, 0 },
  {"keep-dirlinks",   'K', POPT_ARG_NONE,   &keep_dirlinks, 0, 0, 0 },
  {"hard-links",      'H', POPT_ARG_NONE,   0, 'H', 0, 0 },
  {"no-hard-links",    0,  POPT_ARG_VAL,    &preserve_hard_links, 0, 0, 0 },
  {"no-H",             0,  POPT_ARG_VAL,    &preserve_hard_links, 0, 0, 0 },
  {"relative",        'R', POPT_ARG_VAL,    &relative_paths, 1, 0, 0 },
  {"no-relative",      0,  POPT_ARG_VAL,    &relative_paths, 0, 0, 0 },
  {"no-R",             0,  POPT_ARG_VAL,    &relative_paths, 0, 0, 0 },
  {"implied-dirs",     0,  POPT_ARG_VAL,    &implied_dirs, 1, 0, 0 },
  {"no-implied-dirs",  0,  POPT_ARG_VAL,    &implied_dirs, 0, 0, 0 },
  {"i-d",              0,  POPT_ARG_VAL,    &implied_dirs, 1, 0, 0 },
  {"no-i-d",           0,  POPT_ARG_VAL,    &implied_dirs, 0, 0, 0 },
  {"chmod",            0,  POPT_ARG_STRING, 0, OPT_CHMOD, 0, 0 },
  {"ignore-times",    'I', POPT_ARG_NONE,   &ignore_times, 0, 0, 0 },
  {"size-only",        0,  POPT_ARG_NONE,   &size_only, 0, 0, 0 },
  {"one-file-system", 'x', POPT_ARG_NONE,   0, 'x', 0, 0 },
  {"no-one-file-system",0, POPT_ARG_VAL,    &one_file_system, 0, 0, 0 },
  {"no-x",             0,  POPT_ARG_VAL,    &one_file_system, 0, 0, 0 },
  {"update",          'u', POPT_ARG_NONE,   &update_only, 0, 0, 0 },
  {"existing",         0,  POPT_ARG_NONE,   &ignore_non_existing, 0, 0, 0 },
  {"ignore-non-existing",0,POPT_ARG_NONE,   &ignore_non_existing, 0, 0, 0 },
  {"ignore-existing",  0,  POPT_ARG_NONE,   &ignore_existing, 0, 0, 0 },
  {"max-size",         0,  POPT_ARG_STRING, &max_size_arg, OPT_MAX_SIZE, 0, 0 },
  {"min-size",         0,  POPT_ARG_STRING, &min_size_arg, OPT_MIN_SIZE, 0, 0 },
  {"sparse",          'S', POPT_ARG_VAL,    &sparse_files, 1, 0, 0 },
  {"no-sparse",        0,  POPT_ARG_VAL,    &sparse_files, 0, 0, 0 },
  {"no-S",             0,  POPT_ARG_VAL,    &sparse_files, 0, 0, 0 },
  {"preallocate",      0,  POPT_ARG_NONE,   &preallocate_files, 0, 0, 0},
  {"inplace",          0,  POPT_ARG_VAL,    &inplace, 1, 0, 0 },
  {"no-inplace",       0,  POPT_ARG_VAL,    &inplace, 0, 0, 0 },
  {"append",           0,  POPT_ARG_NONE,   0, OPT_APPEND, 0, 0 },
  {"append-verify",    0,  POPT_ARG_VAL,    &append_mode, 2, 0, 0 },
  {"no-append",        0,  POPT_ARG_VAL,    &append_mode, 0, 0, 0 },
  {"del",              0,  POPT_ARG_NONE,   &delete_during, 0, 0, 0 },
  {"delete",           0,  POPT_ARG_NONE,   &delete_mode, 0, 0, 0 },
  {"delete-before",    0,  POPT_ARG_NONE,   &delete_before, 0, 0, 0 },
  {"delete-during",    0,  POPT_ARG_VAL,    &delete_during, 1, 0, 0 },
  {"delete-delay",     0,  POPT_ARG_VAL,    &delete_during, 2, 0, 0 },
  {"delete-after",     0,  POPT_ARG_NONE,   &delete_after, 0, 0, 0 },
  {"delete-excluded",  0,  POPT_ARG_NONE,   &delete_excluded, 0, 0, 0 },
  {"delete-missing-args",0,POPT_BIT_SET,    &missing_args, 2, 0, 0 },
  {"ignore-missing-args",0,POPT_BIT_SET,    &missing_args, 1, 0, 0 },
  {"remove-sent-files",0,  POPT_ARG_VAL,    &remove_source_files, 2, 0, 0 }, /* deprecated */
  {"remove-source-files",0,POPT_ARG_VAL,    &remove_source_files, 1, 0, 0 },
  {"force",            0,  POPT_ARG_VAL,    &force_delete, 1, 0, 0 },
  {"no-force",         0,  POPT_ARG_VAL,    &force_delete, 0, 0, 0 },
  {"ignore-errors",    0,  POPT_ARG_VAL,    &ignore_errors, 1, 0, 0 },
  {"no-ignore-errors", 0,  POPT_ARG_VAL,    &ignore_errors, 0, 0, 0 },
  {"max-delete",       0,  POPT_ARG_INT,    &max_delete, 0, 0, 0 },
  {0,                 'F', POPT_ARG_NONE,   0, 'F', 0, 0 },
  {"filter",          'f', POPT_ARG_STRING, 0, OPT_FILTER, 0, 0 },
  {"exclude",          0,  POPT_ARG_STRING, 0, OPT_EXCLUDE, 0, 0 },
  {"include",          0,  POPT_ARG_STRING, 0, OPT_INCLUDE, 0, 0 },
  {"exclude-from",     0,  POPT_ARG_STRING, 0, OPT_EXCLUDE_FROM, 0, 0 },
  {"include-from",     0,  POPT_ARG_STRING, 0, OPT_INCLUDE_FROM, 0, 0 },
  {"cvs-exclude",     'C', POPT_ARG_NONE,   &cvs_exclude, 0, 0, 0 },
  {"whole-file",      'W', POPT_ARG_VAL,    &whole_file, 1, 0, 0 },
  {"no-whole-file",    0,  POPT_ARG_VAL,    &whole_file, 0, 0, 0 },
  {"no-W",             0,  POPT_ARG_VAL,    &whole_file, 0, 0, 0 },
  {"checksum",        'c', POPT_ARG_VAL,    &always_checksum, 1, 0, 0 },
  {"no-checksum",      0,  POPT_ARG_VAL,    &always_checksum, 0, 0, 0 },
  {"no-c",             0,  POPT_ARG_VAL,    &always_checksum, 0, 0, 0 },
  {"checksum-choice",  0,  POPT_ARG_STRING, &checksum_choice, 0, 0, 0 },
  {"cc",               0,  POPT_ARG_STRING, &checksum_choice, 0, 0, 0 },
  {"block-size",      'B', POPT_ARG_LONG,   &block_size, 0, 0, 0 },
  {"compare-dest",     0,  POPT_ARG_STRING, 0, OPT_COMPARE_DEST, 0, 0 },
  {"copy-dest",        0,  POPT_ARG_STRING, 0, OPT_COPY_DEST, 0, 0 },
  {"link-dest",        0,  POPT_ARG_STRING, 0, OPT_LINK_DEST, 0, 0 },
  {"fuzzy",           'y', POPT_ARG_NONE,   0, 'y', 0, 0 },
  {"no-fuzzy",         0,  POPT_ARG_VAL,    &fuzzy_basis, 0, 0, 0 },
  {"no-y",             0,  POPT_ARG_VAL,    &fuzzy_basis, 0, 0, 0 },
  {"compress",        'z', POPT_ARG_NONE,   0, 'z', 0, 0 },
  {"old-compress",     0,  POPT_ARG_NONE,   0, OPT_OLD_COMPRESS, 0, 0 },
  {"new-compress",     0,  POPT_ARG_NONE,   0, OPT_NEW_COMPRESS, 0, 0 },
  {"no-compress",      0,  POPT_ARG_NONE,   0, OPT_NO_COMPRESS, 0, 0 },
  {"no-z",             0,  POPT_ARG_NONE,   0, OPT_NO_COMPRESS, 0, 0 },
  {"compress-choice",  0,  POPT_ARG_STRING, &compress_choice, 0, 0, 0 },
  {"zc",               0,  POPT_ARG_STRING, &compress_choice, 0, 0, 0 },
  {"skip-compress",    0,  POPT_ARG_STRING, &skip_compress, 0, 0, 0 },
  {"compress-level",   0,  POPT_ARG_INT,    &do_compression_level, 0, 0, 0 },
  {0,                 'P', POPT_ARG_NONE,   0, 'P', 0, 0 },
  {"progress",         0,  POPT_ARG_VAL,    &do_progress, 1, 0, 0 },
  {"no-progress",      0,  POPT_ARG_VAL,    &do_progress, 0, 0, 0 },
  {"partial",          0,  POPT_ARG_VAL,    &keep_partial, 1, 0, 0 },
  {"no-partial",       0,  POPT_ARG_VAL,    &keep_partial, 0, 0, 0 },
  {"partial-dir",      0,  POPT_ARG_STRING, &partial_dir, 0, 0, 0 },
  {"delay-updates",    0,  POPT_ARG_VAL,    &delay_updates, 1, 0, 0 },
  {"no-delay-updates", 0,  POPT_ARG_VAL,    &delay_updates, 0, 0, 0 },
  {"prune-empty-dirs",'m', POPT_ARG_VAL,    &prune_empty_dirs, 1, 0, 0 },
  {"no-prune-empty-dirs",0,POPT_ARG_VAL,    &prune_empty_dirs, 0, 0, 0 },
  {"no-m",             0,  POPT_ARG_VAL,    &prune_empty_dirs, 0, 0, 0 },
  {"log-file",         0,  POPT_ARG_STRING, &logfile_name, 0, 0, 0 },
  {"log-file-format",  0,  POPT_ARG_STRING, &logfile_format, 0, 0, 0 },
  {"out-format",       0,  POPT_ARG_STRING, &stdout_format, 0, 0, 0 },
  {"log-format",       0,  POPT_ARG_STRING, &stdout_format, 0, 0, 0 }, /* DEPRECATED */
  {"itemize-changes", 'i', POPT_ARG_NONE,   0, 'i', 0, 0 },
  {"no-itemize-changes",0, POPT_ARG_VAL,    &itemize_changes, 0, 0, 0 },
  {"no-i",             0,  POPT_ARG_VAL,    &itemize_changes, 0, 0, 0 },
  {"bwlimit",          0,  POPT_ARG_STRING, &bwlimit_arg, OPT_BWLIMIT, 0, 0 },
  {"no-bwlimit",       0,  POPT_ARG_VAL,    &bwlimit, 0, 0, 0 },
  {"backup",          'b', POPT_ARG_VAL,    &make_backups, 1, 0, 0 },
  {"no-backup",        0,  POPT_ARG_VAL,    &make_backups, 0, 0, 0 },
  {"backup-dir",       0,  POPT_ARG_STRING, &backup_dir, 0, 0, 0 },
  {"suffix",           0,  POPT_ARG_STRING, &backup_suffix, 0, 0, 0 },
  {"list-only",        0,  POPT_ARG_VAL,    &list_only, 2, 0, 0 },
  {"read-batch",       0,  POPT_ARG_STRING, &batch_name, OPT_READ_BATCH, 0, 0 },
  {"write-batch",      0,  POPT_ARG_STRING, &batch_name, OPT_WRITE_BATCH, 0, 0 },
  {"only-write-batch", 0,  POPT_ARG_STRING, &batch_name, OPT_ONLY_WRITE_BATCH, 0, 0 },
  {"files-from",       0,  POPT_ARG_STRING, &files_from, 0, 0, 0 },
  {"from0",           '0', POPT_ARG_VAL,    &eol_nulls, 1, 0, 0},
  {"no-from0",         0,  POPT_ARG_VAL,    &eol_nulls, 0, 0, 0},
  {"protect-args",    's', POPT_ARG_VAL,    &protect_args, 1, 0, 0},
  {"no-protect-args",  0,  POPT_ARG_VAL,    &protect_args, 0, 0, 0},
  {"no-s",             0,  POPT_ARG_VAL,    &protect_args, 0, 0, 0},
  {"numeric-ids",      0,  POPT_ARG_VAL,    &numeric_ids, 1, 0, 0 },
  {"no-numeric-ids",   0,  POPT_ARG_VAL,    &numeric_ids, 0, 0, 0 },
  {"usermap",          0,  POPT_ARG_STRING, 0, OPT_USERMAP, 0, 0 },
  {"groupmap",         0,  POPT_ARG_STRING, 0, OPT_GROUPMAP, 0, 0 },
  {"chown",            0,  POPT_ARG_STRING, 0, OPT_CHOWN, 0, 0 },
  {"timeout",          0,  POPT_ARG_INT,    &io_timeout, 0, 0, 0 },
  {"no-timeout",       0,  POPT_ARG_VAL,    &io_timeout, 0, 0, 0 },
  {"contimeout",       0,  POPT_ARG_INT,    &connect_timeout, 0, 0, 0 },
  {"no-contimeout",    0,  POPT_ARG_VAL,    &connect_timeout, 0, 0, 0 },
  {"rsh",             'e', POPT_ARG_STRING, &shell_cmd, 0, 0, 0 },
  {"rsync-path",       0,  POPT_ARG_STRING, &rsync_path, 0, 0, 0 },
  {"temp-dir",        'T', POPT_ARG_STRING, &tmpdir, 0, 0, 0 },
#ifdef ICONV_OPTION
  {"iconv",            0,  POPT_ARG_STRING, &iconv_opt, 0, 0, 0 },
  {"no-iconv",         0,  POPT_ARG_NONE,   0, OPT_NO_ICONV, 0, 0 },
#endif
  {"ipv4",            '4', POPT_ARG_VAL,    &default_af_hint, AF_INET, 0, 0 },
  {"ipv6",            '6', POPT_ARG_VAL,    &default_af_hint, AF_INET6, 0, 0 },
  {"8-bit-output",    '8', POPT_ARG_VAL,    &allow_8bit_chars, 1, 0, 0 },
  {"no-8-bit-output",  0,  POPT_ARG_VAL,    &allow_8bit_chars, 0, 0, 0 },
  {"no-8",             0,  POPT_ARG_VAL,    &allow_8bit_chars, 0, 0, 0 },
  {"qsort",            0,  POPT_ARG_NONE,   &use_qsort, 0, 0, 0 },
  {"copy-as",          0,  POPT_ARG_STRING, &copy_as, 0, 0, 0 },
  {"address",          0,  POPT_ARG_STRING, &bind_address, 0, 0, 0 },
  {"port",             0,  POPT_ARG_INT,    &rsync_port, 0, 0, 0 },
  {"sockopts",         0,  POPT_ARG_STRING, &sockopts, 0, 0, 0 },
  {"password-file",    0,  POPT_ARG_STRING, &password_file, 0, 0, 0 },
  {"blocking-io",      0,  POPT_ARG_VAL,    &blocking_io, 1, 0, 0 },
  {"no-blocking-io",   0,  POPT_ARG_VAL,    &blocking_io, 0, 0, 0 },
#ifdef HAVE_SETVBUF
  {"outbuf",           0,  POPT_ARG_STRING, &outbuf_mode, 0, 0, 0 },
#endif
  {"remote-option",   'M', POPT_ARG_STRING, 0, 'M', 0, 0 },
  {"protocol",         0,  POPT_ARG_INT,    &protocol_version, 0, 0, 0 },
  {"checksum-seed",    0,  POPT_ARG_INT,    &checksum_seed, 0, 0, 0 },
  {"server",           0,  POPT_ARG_NONE,   0, OPT_SERVER, 0, 0 },
  {"sender",           0,  POPT_ARG_NONE,   0, OPT_SENDER, 0, 0 },
  /* All the following options switch us into daemon-mode option-parsing. */
  {"config",           0,  POPT_ARG_STRING, 0, OPT_DAEMON, 0, 0 },
  {"daemon",           0,  POPT_ARG_NONE,   0, OPT_DAEMON, 0, 0 },
  {"dparam",           0,  POPT_ARG_STRING, 0, OPT_DAEMON, 0, 0 },
  {"detach",           0,  POPT_ARG_NONE,   0, OPT_DAEMON, 0, 0 },
  {"no-detach",        0,  POPT_ARG_NONE,   0, OPT_DAEMON, 0, 0 },
  {0,0,0,0, 0, 0, 0}
};

static void daemon_usage(enum logcode F)
{
  print_rsync_version(F);

  rprintf(F,"\n");
  rprintf(F,"Usage: rsync --daemon [OPTION]...\n");
  rprintf(F,"     --address=ADDRESS       bind to the specified address\n");
  rprintf(F,"     --bwlimit=RATE          limit socket I/O bandwidth\n");
  rprintf(F,"     --config=FILE           specify alternate rsyncd.conf file\n");
  rprintf(F," -M, --dparam=OVERRIDE       override global daemon config parameter\n");
  rprintf(F,"     --no-detach             do not detach from the parent\n");
  rprintf(F,"     --port=PORT             listen on alternate port number\n");
  rprintf(F,"     --log-file=FILE         override the \"log file\" setting\n");
  rprintf(F,"     --log-file-format=FMT   override the \"log format\" setting\n");
  rprintf(F,"     --sockopts=OPTIONS      specify custom TCP options\n");
  rprintf(F," -v, --verbose               increase verbosity\n");
  rprintf(F," -4, --ipv4                  prefer IPv4\n");
  rprintf(F," -6, --ipv6                  prefer IPv6\n");
  rprintf(F,"     --help                  show this help screen\n");

  rprintf(F,"\n");
  rprintf(F,"If you were not trying to invoke rsync as a daemon, avoid using any of the\n");
  rprintf(F,"daemon-specific rsync options.  See also the rsyncd.conf(5) man page.\n");
}

static struct poptOption long_daemon_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"address",          0,  POPT_ARG_STRING, &bind_address, 0, 0, 0 },
  {"bwlimit",          0,  POPT_ARG_INT,    &daemon_bwlimit, 0, 0, 0 },
  {"config",           0,  POPT_ARG_STRING, &config_file, 0, 0, 0 },
  {"daemon",           0,  POPT_ARG_NONE,   &daemon_opt, 0, 0, 0 },
  {"dparam",          'M', POPT_ARG_STRING, 0, 'M', 0, 0 },
  {"ipv4",            '4', POPT_ARG_VAL,    &default_af_hint, AF_INET, 0, 0 },
  {"ipv6",            '6', POPT_ARG_VAL,    &default_af_hint, AF_INET6, 0, 0 },
  {"detach",           0,  POPT_ARG_VAL,    &no_detach, 0, 0, 0 },
  {"no-detach",        0,  POPT_ARG_VAL,    &no_detach, 1, 0, 0 },
  {"log-file",         0,  POPT_ARG_STRING, &logfile_name, 0, 0, 0 },
  {"log-file-format",  0,  POPT_ARG_STRING, &logfile_format, 0, 0, 0 },
  {"port",             0,  POPT_ARG_INT,    &rsync_port, 0, 0, 0 },
  {"sockopts",         0,  POPT_ARG_STRING, &sockopts, 0, 0, 0 },
  {"protocol",         0,  POPT_ARG_INT,    &protocol_version, 0, 0, 0 },
  {"server",           0,  POPT_ARG_NONE,   &am_server, 0, 0, 0 },
  {"temp-dir",        'T', POPT_ARG_STRING, &tmpdir, 0, 0, 0 },
  {"verbose",         'v', POPT_ARG_NONE,   0, 'v', 0, 0 },
  {"no-verbose",       0,  POPT_ARG_VAL,    &verbose, 0, 0, 0 },
  {"no-v",             0,  POPT_ARG_VAL,    &verbose, 0, 0, 0 },
  {"help",            'h', POPT_ARG_NONE,   0, 'h', 0, 0 },
  {0,0,0,0, 0, 0, 0}
};


static char err_buf[200];


/**
 * Store the option error message, if any, so that we can log the
 * connection attempt (which requires parsing the options), and then
 * show the error later on.
 **/
void option_error(void)
{
	if (!err_buf[0]) {
		strlcpy(err_buf, "Error parsing options: option may "
			"be supported on client but not on server?\n",
			sizeof err_buf);
	}

	rprintf(FERROR, RSYNC_NAME ": %s", err_buf);
	io_flush(FULL_FLUSH);
	msleep(20);
}


static void parse_one_refuse_match(int negated, const char *ref, const struct poptOption *list_end)
{
	struct poptOption *op;
	char shortName[2];
	int is_wild = strpbrk(ref, "*?[") != NULL;
	int found_match = 0;

	shortName[1] = '\0';

	if (strcmp("a", ref) == 0 || strcmp("archive", ref) == 0) {
		ref = "[ardlptgoD]";
		is_wild = 1;
	}

	for (op = long_options; op != list_end; op++) {
		*shortName = op->shortName;
		if ((op->longName && wildmatch(ref, op->longName))
		 || (*shortName && wildmatch(ref, shortName))) {
			if (op->descrip[1] == '*')
				op->descrip = negated ? "a*" : "r*";
			else if (!is_wild)
				op->descrip = negated ? "a=" : "r=";
			found_match = 1;
			if (!is_wild)
				break;
		}
	}

	if (!found_match)
		rprintf(FLOG, "No match for refuse-options string \"%s\"\n", ref);
}


/**
 * Tweak the option table to disable all options that the rsyncd.conf
 * file has told us to refuse.
 **/
static void set_refuse_options(void)
{
	struct poptOption *op, *list_end = NULL;
	char *cp, *ref = lp_refuse_options(module_id);
	int negated;

	if (!ref)
		ref = "";

	if (!*ref && !am_daemon) /* A simple optimization */
		return;

	/* We abuse the descrip field in poptOption to make it easy to flag which options
	 * are refused (since we don't use it otherwise).  Start by marking all options
	 * as "a"ccepted with a few options also marked as non-wild. */
	for (op = long_options; ; op++) {
		const char *longName = op->longName ? op->longName : "";
		if (!op->longName && !op->shortName) {
			list_end = op;
			break;
		}
		if (op->shortName == 'e' /* Required for compatibility flags */
		 || op->shortName == '0' /* --from0 just modifies --files-from, so refuse that instead (or not) */
		 || op->shortName == 's' /* --protect-args is always OK */
		 || op->shortName == 'n' /* --dry-run is always OK */
		 || strcmp("iconv", longName) == 0
		 || strcmp("no-iconv", longName) == 0
		 || strcmp("checksum-seed", longName) == 0
		 || strcmp("write-devices", longName) == 0 /* disable wild-match (it gets refused below) */
		 || strcmp("log-format", longName) == 0 /* aka out-format (NOT log-file-format) */
		 || strcmp("sender", longName) == 0
		 || strcmp("server", longName) == 0)
			op->descrip = "a="; /* exact-match only */
		else
			op->descrip = "a*"; /* wild-card-able */
	}
	assert(list_end != NULL);

	if (am_daemon) /* Refused by default, but can be accepted via "!write-devices" */
		parse_one_refuse_match(0, "write-devices", list_end);

	while (1) {
		while (*ref == ' ') ref++;
		if (!*ref)
			break;
		if ((cp = strchr(ref, ' ')) != NULL)
			*cp = '\0';
		negated = *ref == '!';
		if (negated && ref[1])
			ref++;
		parse_one_refuse_match(negated, ref, list_end);
		if (!cp)
			break;
		*cp = ' ';
		ref = cp + 1;
	}

	if (am_daemon) {
#ifdef ICONV_OPTION
		if (!*lp_charset(module_id))
			parse_one_refuse_match(0, "iconv", list_end);
#endif
		parse_one_refuse_match(0, "log-file*", list_end);
	}

	/* Now we use the descrip values to actually mark the options for refusal. */
	for (op = long_options; op != list_end; op++) {
		int refused = op->descrip[0] == 'r';
		op->descrip = NULL;
		if (!refused)
			continue;
		if (op->argInfo == POPT_ARG_VAL)
			op->argInfo = POPT_ARG_NONE;
		op->val = (op - long_options) + OPT_REFUSED_BASE;
		/* The following flags are set to let us easily check an implied option later in the code. */
		switch (op->shortName) {
		case 'r': case 'd': case 'l': case 'p':
		case 't': case 'g': case 'o': case 'D':
			refused_archive_part = op->val;
			break;
		case 'z':
			refused_compress = op->val;
			break;
		case '\0':
			if (strcmp("delete", op->longName) == 0)
				refused_delete = op->val;
			else if (strcmp("delete-before", op->longName) == 0)
				refused_delete_before = op->val;
			else if (strcmp("delete-during", op->longName) == 0)
				refused_delete_during = op->val;
			else if (strcmp("partial", op->longName) == 0)
				refused_partial = op->val;
			else if (strcmp("progress", op->longName) == 0)
				refused_progress = op->val;
			else if (strcmp("inplace", op->longName) == 0)
				refused_inplace = op->val;
			else if (strcmp("no-iconv", op->longName) == 0)
				refused_no_iconv = op->val;
			break;
		}
	}
}


static int count_args(const char **argv)
{
	int i = 0;

	if (argv) {
		while (argv[i] != NULL)
			i++;
	}

	return i;
}


static OFF_T parse_size_arg(char **size_arg, char def_suf)
{
	int reps, mult, make_compatible = 0;
	const char *arg;
	OFF_T size = 1;

	for (arg = *size_arg; isDigit(arg); arg++) {}
	if (*arg == '.')
		for (arg++; isDigit(arg); arg++) {}
	switch (*arg && *arg != '+' && *arg != '-' ? *arg++ : def_suf) {
	case 'b': case 'B':
		reps = 0;
		break;
	case 'k': case 'K':
		reps = 1;
		break;
	case 'm': case 'M':
		reps = 2;
		break;
	case 'g': case 'G':
		reps = 3;
		break;
	default:
		return -1;
	}
	if (*arg == 'b' || *arg == 'B')
		mult = 1000, make_compatible = 1, arg++;
	else if (!*arg || *arg == '+' || *arg == '-')
		mult = 1024;
	else if (strncasecmp(arg, "ib", 2) == 0)
		mult = 1024, arg += 2;
	else
		return -1;
	while (reps--)
		size *= mult;
	size *= atof(*size_arg);
	if ((*arg == '+' || *arg == '-') && arg[1] == '1')
		size += atoi(arg), make_compatible = 1, arg += 2;
	if (*arg)
		return -1;
	if (size > 0 && make_compatible && def_suf == 'b') {
		/* We convert this manually because we may need %lld precision,
		 * and that's not a portable sprintf() escape. */
		char buf[128], *s = buf + sizeof buf - 1;
		OFF_T num = size;
		*s = '\0';
		while (num) {
			*--s = (char)(num % 10) + '0';
			num /= 10;
		}
		if (!(*size_arg = strdup(s)))
			out_of_memory("parse_size_arg");
	}
	return size;
}


static void create_refuse_error(int which)
{
	/* The "which" value is the index + OPT_REFUSED_BASE. */
	struct poptOption *op = &long_options[which - OPT_REFUSED_BASE];
	int n = snprintf(err_buf, sizeof err_buf,
			 "The server is configured to refuse --%s\n",
			 op->longName) - 1;
	if (op->shortName) {
		snprintf(err_buf + n, sizeof err_buf - n,
			 " (-%c)\n", op->shortName);
	}
}

/* This is used to make sure that --daemon & --server cannot be aliased to
 * something else. These options have always disabled popt aliases for the
 * parsing of a daemon or server command-line, but we have to make sure that
 * these options cannot vanish so that the alias disabling can take effect. */
static void popt_unalias(poptContext con, const char *opt)
{
	struct poptAlias unalias;

	unalias.longName = opt + 2; /* point past the leading "--" */
	unalias.shortName = '\0';
	unalias.argc = 1;
	unalias.argv = new_array(const char*, 1);
	unalias.argv[0] = strdup(opt);

	poptAddAlias(con, unalias, 0);
}

/**
 * Process command line arguments.  Called on both local and remote.
 *
 * @retval 1 if all options are OK; with globals set to appropriate
 * values
 *
 * @retval 0 on error, with err_buf containing an explanation
 **/
int parse_arguments(int *argc_p, const char ***argv_p)
{
	static poptContext pc;
	const char *arg, **argv = *argv_p;
	int argc = *argc_p;
	int opt;
	int orig_protect_args = protect_args;

	if (argc == 0) {
		strlcpy(err_buf, "argc is zero!\n", sizeof err_buf);
		return 0;
	}

	set_refuse_options();

#ifdef ICONV_OPTION
	if (!am_daemon && protect_args <= 0 && (arg = getenv("RSYNC_ICONV")) != NULL && *arg)
		iconv_opt = strdup(arg);
#endif

	/* TODO: Call poptReadDefaultConfig; handle errors. */

	/* The context leaks in case of an error, but if there's a
	 * problem we always exit anyhow. */
	if (pc)
		poptFreeContext(pc);
	pc = poptGetContext(RSYNC_NAME, argc, argv, long_options, 0);
	if (!am_server) {
		poptReadDefaultConfig(pc, 0);
		popt_unalias(pc, "--daemon");
		popt_unalias(pc, "--server");
	}

	while ((opt = poptGetNextOpt(pc)) != -1) {
		/* most options are handled automatically by popt;
		 * only special cases are returned and listed here. */

		switch (opt) {
		case OPT_VERSION:
			print_rsync_version(FINFO);
			exit_cleanup(0);

		case OPT_SERVER:
			if (!am_server) {
				/* Disable popt aliases on the server side and
				 * then start parsing the options again. */
				poptFreeContext(pc);
				pc = poptGetContext(RSYNC_NAME, argc, argv,
						    long_options, 0);
				am_server = 1;
			}
#ifdef ICONV_OPTION
			iconv_opt = NULL;
#endif
			break;

		case OPT_SENDER:
			if (!am_server) {
				usage(FERROR);
				exit_cleanup(RERR_SYNTAX);
			}
			am_sender = 1;
			break;

		case OPT_DAEMON:
			if (am_daemon) {
				strlcpy(err_buf,
					"Attempt to hack rsync thwarted!\n",
					sizeof err_buf);
				return 0;
			}
#ifdef ICONV_OPTION
			iconv_opt = NULL;
#endif
			protect_args = 0;
			poptFreeContext(pc);
			pc = poptGetContext(RSYNC_NAME, argc, argv,
					    long_daemon_options, 0);
			while ((opt = poptGetNextOpt(pc)) != -1) {
				char **cpp;
				switch (opt) {
				case 'h':
					daemon_usage(FINFO);
					exit_cleanup(0);

				case 'M':
					arg = poptGetOptArg(pc);
					if (!strchr(arg, '=')) {
						rprintf(FERROR,
						    "--dparam value is missing an '=': %s\n",
						    arg);
						goto daemon_error;
					}
					cpp = EXPAND_ITEM_LIST(&dparam_list, char *, 4);
					*cpp = strdup(arg);
					break;

				case 'v':
					verbose++;
					break;

				default:
					rprintf(FERROR,
					    "rsync: %s: %s (in daemon mode)\n",
					    poptBadOption(pc, POPT_BADOPTION_NOALIAS),
					    poptStrerror(opt));
					goto daemon_error;
				}
			}

			if (dparam_list.count && !set_dparams(1))
				exit_cleanup(RERR_SYNTAX);

			if (tmpdir && strlen(tmpdir) >= MAXPATHLEN - 10) {
				snprintf(err_buf, sizeof err_buf,
					 "the --temp-dir path is WAY too long.\n");
				return 0;
			}

			if (!daemon_opt) {
				rprintf(FERROR, "Daemon option(s) used without --daemon.\n");
			    daemon_error:
				rprintf(FERROR,
				    "(Type \"rsync --daemon --help\" for assistance with daemon mode.)\n");
				exit_cleanup(RERR_SYNTAX);
			}

			*argv_p = argv = poptGetArgs(pc);
			*argc_p = argc = count_args(argv);
			am_starting_up = 0;
			daemon_opt = 0;
			am_daemon = 1;
			return 1;

		case OPT_MODIFY_WINDOW:
			/* The value has already been set by popt, but
			 * we need to remember that we're using a
			 * non-default setting. */
			modify_window_set = 1;
			break;

		case OPT_FILTER:
			parse_filter_str(&filter_list, poptGetOptArg(pc),
					rule_template(0), 0);
			break;

		case OPT_EXCLUDE:
			parse_filter_str(&filter_list, poptGetOptArg(pc),
					rule_template(0), XFLG_OLD_PREFIXES);
			break;

		case OPT_INCLUDE:
			parse_filter_str(&filter_list, poptGetOptArg(pc),
					rule_template(FILTRULE_INCLUDE), XFLG_OLD_PREFIXES);
			break;

		case OPT_EXCLUDE_FROM:
		case OPT_INCLUDE_FROM:
			arg = poptGetOptArg(pc);
			if (sanitize_paths)
				arg = sanitize_path(NULL, arg, NULL, 0, SP_DEFAULT);
			if (daemon_filter_list.head) {
				int rej;
				char *cp = strdup(arg);
				if (!cp)
					out_of_memory("parse_arguments");
				if (!*cp)
					rej = 1;
				else {
					char *dir = cp + (*cp == '/' ? module_dirlen : 0);
					clean_fname(dir, CFN_COLLAPSE_DOT_DOT_DIRS);
					rej = check_filter(&daemon_filter_list, FLOG, dir, 0) < 0;
				}
				free(cp);
				if (rej)
					goto options_rejected;
			}
			parse_filter_file(&filter_list, arg,
				rule_template(opt == OPT_INCLUDE_FROM ? FILTRULE_INCLUDE : 0),
				XFLG_FATAL_ERRORS | XFLG_OLD_PREFIXES);
			break;

		case 'a':
			if (refused_archive_part) {
				create_refuse_error(refused_archive_part);
				return 0;
			}
			if (!recurse) /* preserve recurse == 2 */
				recurse = 1;
#ifdef SUPPORT_LINKS
			preserve_links = 1;
#endif
			preserve_perms = 1;
			preserve_times = 1;
			preserve_gid = 1;
			preserve_uid = 1;
			preserve_devices = 1;
			preserve_specials = 1;
			break;

		case 'D':
			preserve_devices = preserve_specials = 1;
			break;

		case OPT_NO_D:
			preserve_devices = preserve_specials = 0;
			break;

		case 'h':
			human_readable++;
			break;

		case 'H':
			preserve_hard_links++;
			break;

		case 'i':
			itemize_changes++;
			break;

		case 'U':
			if (++preserve_atimes > 1)
				open_noatime = 1;
			break;

		case 'v':
			verbose++;
			break;

		case 'y':
			fuzzy_basis++;
			break;

		case 'q':
			quiet++;
			break;

		case 'x':
			one_file_system++;
			break;

		case 'F':
			switch (++F_option_cnt) {
			case 1:
				parse_filter_str(&filter_list,": /.rsync-filter",rule_template(0),0);
				break;
			case 2:
				parse_filter_str(&filter_list,"- .rsync-filter",rule_template(0),0);
				break;
			}
			break;

		case 'P':
			if (refused_partial || refused_progress) {
				create_refuse_error(refused_partial
				    ? refused_partial : refused_progress);
				return 0;
			}
			do_progress = 1;
			keep_partial = 1;
			break;

		case 'z':
			do_compression++;
			break;

		case OPT_OLD_COMPRESS:
			compress_choice = "zlib";
			break;

		case OPT_NEW_COMPRESS:
			compress_choice = "zlibx";
			break;

		case OPT_NO_COMPRESS:
			do_compression = 0;
			compress_choice = NULL;
			break;

		case 'M':
			arg = poptGetOptArg(pc);
			if (*arg != '-') {
				snprintf(err_buf, sizeof err_buf,
					"Remote option must start with a dash: %s\n", arg);
				return 0;
			}
			if (remote_option_cnt+2 >= remote_option_alloc) {
				remote_option_alloc += 16;
				remote_options = realloc_array(remote_options,
							const char *, remote_option_alloc);
				if (!remote_options)
					out_of_memory("parse_arguments");
				if (!remote_option_cnt)
					remote_options[0] = "ARG0";
			}
			remote_options[++remote_option_cnt] = arg;
			remote_options[remote_option_cnt+1] = NULL;
			break;

		case OPT_WRITE_BATCH:
			/* batch_name is already set */
			write_batch = 1;
			break;

		case OPT_ONLY_WRITE_BATCH:
			/* batch_name is already set */
			write_batch = -1;
			break;

		case OPT_READ_BATCH:
			/* batch_name is already set */
			read_batch = 1;
			break;

		case OPT_NO_ICONV:
#ifdef ICONV_OPTION
			iconv_opt = NULL;
#endif
			break;

		case OPT_MAX_SIZE:
			if ((max_size = parse_size_arg(&max_size_arg, 'b')) < 0) {
				snprintf(err_buf, sizeof err_buf,
					"--max-size value is invalid: %s\n",
					max_size_arg);
				return 0;
			}
			break;

		case OPT_MIN_SIZE:
			if ((min_size = parse_size_arg(&min_size_arg, 'b')) < 0) {
				snprintf(err_buf, sizeof err_buf,
					"--min-size value is invalid: %s\n",
					min_size_arg);
				return 0;
			}
			break;

		case OPT_BWLIMIT:
			{
				OFF_T limit = parse_size_arg(&bwlimit_arg, 'K');
				if (limit < 0) {
					snprintf(err_buf, sizeof err_buf,
						"--bwlimit value is invalid: %s\n", bwlimit_arg);
					return 0;
				}
				bwlimit = (limit + 512) / 1024;
				if (limit && !bwlimit) {
					snprintf(err_buf, sizeof err_buf,
						"--bwlimit value is too small: %s\n", bwlimit_arg);
					return 0;
				}
			}
			break;

		case OPT_APPEND:
			if (am_server)
				append_mode++;
			else
				append_mode = 1;
			break;

		case OPT_LINK_DEST:
#ifdef SUPPORT_HARD_LINKS
			link_dest = 1;
			dest_option = "--link-dest";
			goto set_dest_dir;
#else
			snprintf(err_buf, sizeof err_buf,
				 "hard links are not supported on this %s\n",
				 am_server ? "server" : "client");
			return 0;
#endif

		case OPT_COPY_DEST:
			copy_dest = 1;
			dest_option = "--copy-dest";
			goto set_dest_dir;

		case OPT_COMPARE_DEST:
			compare_dest = 1;
			dest_option = "--compare-dest";
		set_dest_dir:
			if (basis_dir_cnt >= MAX_BASIS_DIRS) {
				snprintf(err_buf, sizeof err_buf,
					"ERROR: at most %d %s args may be specified\n",
					MAX_BASIS_DIRS, dest_option);
				return 0;
			}
			/* We defer sanitizing this arg until we know what
			 * our destination directory is going to be. */
			basis_dir[basis_dir_cnt++] = (char *)poptGetOptArg(pc);
			break;

		case OPT_CHMOD:
			arg = poptGetOptArg(pc);
			if (!parse_chmod(arg, &chmod_modes)) {
				snprintf(err_buf, sizeof err_buf,
				    "Invalid argument passed to --chmod (%s)\n",
				    arg);
				return 0;
			}
			break;

		case OPT_INFO:
			arg = poptGetOptArg(pc);
			parse_output_words(info_words, info_levels, arg, USER_PRIORITY);
			break;

		case OPT_DEBUG:
			arg = poptGetOptArg(pc);
			parse_output_words(debug_words, debug_levels, arg, USER_PRIORITY);
			break;

		case OPT_USERMAP:
			if (usermap) {
				if (usermap_via_chown) {
					snprintf(err_buf, sizeof err_buf,
					    "--usermap conflicts with prior --chown.\n");
					return 0;
				}
				snprintf(err_buf, sizeof err_buf,
				    "You can only specify --usermap once.\n");
				return 0;
			}
			usermap = (char *)poptGetOptArg(pc);
			usermap_via_chown = False;
			break;

		case OPT_GROUPMAP:
			if (groupmap) {
				if (groupmap_via_chown) {
					snprintf(err_buf, sizeof err_buf,
					    "--groupmap conflicts with prior --chown.\n");
					return 0;
				}
				snprintf(err_buf, sizeof err_buf,
				    "You can only specify --groupmap once.\n");
				return 0;
			}
			groupmap = (char *)poptGetOptArg(pc);
			groupmap_via_chown = False;
			break;

		case OPT_CHOWN: {
			const char *chown = poptGetOptArg(pc);
			int len;
			if ((arg = strchr(chown, ':')) != NULL)
				len = arg++ - chown;
			else
				len = strlen(chown);
			if (len) {
				if (usermap) {
					if (!usermap_via_chown) {
						snprintf(err_buf, sizeof err_buf,
						    "--chown conflicts with prior --usermap.\n");
						return 0;
					}
					snprintf(err_buf, sizeof err_buf,
					    "You can only specify a user-affecting --chown once.\n");
					return 0;
				}
				if (asprintf(&usermap, "*:%.*s", len, chown) < 0)
					out_of_memory("parse_arguments");
				usermap_via_chown = True;
			}
			if (arg && *arg) {
				if (groupmap) {
					if (!groupmap_via_chown) {
						snprintf(err_buf, sizeof err_buf,
						    "--chown conflicts with prior --groupmap.\n");
						return 0;
					}
					snprintf(err_buf, sizeof err_buf,
					    "You can only specify a group-affecting --chown once.\n");
					return 0;
				}
				if (asprintf(&groupmap, "*:%s", arg) < 0)
					out_of_memory("parse_arguments");
				groupmap_via_chown = True;
			}
			break;
		}

		case OPT_HELP:
			usage(FINFO);
			exit_cleanup(0);

		case 'A':
#ifdef SUPPORT_ACLS
			preserve_acls = 1;
			preserve_perms = 1;
			break;
#else
			/* FIXME: this should probably be ignored with a
			 * warning and then countermeasures taken to
			 * restrict group and other access in the presence
			 * of any more restrictive ACLs, but this is safe
			 * for now */
			snprintf(err_buf,sizeof(err_buf),
				 "ACLs are not supported on this %s\n",
				 am_server ? "server" : "client");
			return 0;
#endif

		case 'X':
#ifdef SUPPORT_XATTRS
			preserve_xattrs++;
			break;
#else
			snprintf(err_buf,sizeof(err_buf),
				 "extended attributes are not supported on this %s\n",
				 am_server ? "server" : "client");
			return 0;
#endif

		default:
			/* A large opt value means that set_refuse_options()
			 * turned this option off. */
			if (opt >= OPT_REFUSED_BASE) {
				create_refuse_error(opt);
				return 0;
			}
			snprintf(err_buf, sizeof err_buf, "%s%s: %s\n",
				 am_server ? "on remote machine: " : "",
				 poptBadOption(pc, POPT_BADOPTION_NOALIAS),
				 poptStrerror(opt));
			return 0;
		}
	}

	if (protect_args < 0) {
		if (am_server)
			protect_args = 0;
		else if ((arg = getenv("RSYNC_PROTECT_ARGS")) != NULL && *arg)
			protect_args = atoi(arg) ? 1 : 0;
		else {
#ifdef RSYNC_USE_PROTECTED_ARGS
			protect_args = 1;
#else
			protect_args = 0;
#endif
		}
	}

	if (checksum_choice && strcasecmp(checksum_choice, "auto") != 0 && strcasecmp(checksum_choice, "auto,auto") != 0) {
		/* Call this early to verify the args and figure out if we need to force
		 * --whole-file. Note that the parse function will get called again later,
		 * just in case an "auto" choice needs to know the protocol_version. */
		parse_checksum_choice(0);
	} else
		checksum_choice = NULL;

	if (human_readable > 1 && argc == 2 && !am_server) {
		/* Allow the old meaning of 'h' (--help) on its own. */
		usage(FINFO);
		exit_cleanup(0);
	}

	if (!compress_choice && do_compression > 1)
		compress_choice = "zlibx";
	if (compress_choice && strcasecmp(compress_choice, "auto") != 0)
		parse_compress_choice(0); /* Twiddles do_compression and can possibly NULL-out compress_choice. */
	else
		compress_choice = NULL;

	if (do_compression || do_compression_level != CLVL_NOT_SPECIFIED) {
		if (!do_compression)
			do_compression = CPRES_AUTO;
		if (do_compression && refused_compress) {
			create_refuse_error(refused_compress);
			return 0;
		}
	}

#ifdef HAVE_SETVBUF
	if (outbuf_mode && !am_server) {
		int mode = *(uchar *)outbuf_mode;
		if (islower(mode))
			mode = toupper(mode);
		fflush(stdout); /* Just in case... */
		switch (mode) {
		case 'N': /* None */
		case 'U': /* Unbuffered */
			mode = _IONBF;
			break;
		case 'L': /* Line */
			mode = _IOLBF;
			break;
		case 'B': /* Block */
		case 'F': /* Full */
			mode = _IOFBF;
			break;
		default:
			snprintf(err_buf, sizeof err_buf,
				"Invalid --outbuf setting -- specify N, L, or B.\n");
			return 0;
		}
		setvbuf(stdout, (char *)NULL, mode, 0);
	}

	if (msgs2stderr) {
		/* Make stderr line buffered for better sharing of the stream. */
		fflush(stderr); /* Just in case... */
		setvbuf(stderr, (char *)NULL, _IOLBF, 0);
	}
#endif

	set_output_verbosity(verbose, DEFAULT_PRIORITY);

	if (do_stats) {
		parse_output_words(info_words, info_levels,
			verbose > 1 ? "stats3" : "stats2", DEFAULT_PRIORITY);
	}

#ifdef ICONV_OPTION
	if (iconv_opt && protect_args != 2) {
		if (!am_server && strcmp(iconv_opt, "-") == 0)
			iconv_opt = NULL;
		else
			need_unsorted_flist = 1;
	}
	if (refused_no_iconv && !iconv_opt) {
		create_refuse_error(refused_no_iconv);
		return 0;
	}
#endif

	if (fuzzy_basis > 1)
		fuzzy_basis = basis_dir_cnt + 1;

	/* Don't let the client reset protect_args if it was already processed */
	if (orig_protect_args == 2 && am_server)
		protect_args = orig_protect_args;

	if (protect_args == 1 && am_server)
		return 1;

	*argv_p = argv = poptGetArgs(pc);
	*argc_p = argc = count_args(argv);

#ifndef SUPPORT_LINKS
	if (preserve_links && !am_sender) {
		snprintf(err_buf, sizeof err_buf,
			 "symlinks are not supported on this %s\n",
			 am_server ? "server" : "client");
		return 0;
	}
#endif

#ifndef SUPPORT_HARD_LINKS
	if (preserve_hard_links) {
		snprintf(err_buf, sizeof err_buf,
			 "hard links are not supported on this %s\n",
			 am_server ? "server" : "client");
		return 0;
	}
#endif

#ifdef SUPPORT_XATTRS
	if (am_root < 0 && preserve_xattrs > 1) {
		snprintf(err_buf, sizeof err_buf,
			 "--fake-super conflicts with -XX\n");
		return 0;
	}
#else
	if (am_root < 0) {
		snprintf(err_buf, sizeof err_buf,
			 "--fake-super requires an rsync with extended attributes enabled\n");
		return 0;
	}
#endif

	if (block_size) {
		/* We may not know the real protocol_version at this point if this is the client
		 * option parsing, but we still want to check it so that the client can specify
		 * a --protocol=29 option with a larger block size. */
		int32 max_blength = protocol_version < 30 ? OLD_MAX_BLOCK_SIZE : MAX_BLOCK_SIZE;

		if (block_size > max_blength) {
			snprintf(err_buf, sizeof err_buf,
				 "--block-size=%lu is too large (max: %u)\n", block_size, max_blength);
			return 0;
		}
	}

	if (write_batch && read_batch) {
		snprintf(err_buf, sizeof err_buf,
			"--write-batch and --read-batch can not be used together\n");
		return 0;
	}
	if (write_batch > 0 || read_batch) {
		if (am_server) {
			rprintf(FINFO,
				"ignoring --%s-batch option sent to server\n",
				write_batch ? "write" : "read");
			/* We don't actually exit_cleanup(), so that we can
			 * still service older version clients that still send
			 * batch args to server. */
			read_batch = write_batch = 0;
			batch_name = NULL;
		} else if (dry_run)
			write_batch = 0;
	} else if (write_batch < 0 && dry_run)
		write_batch = 0;
	if (read_batch && files_from) {
		snprintf(err_buf, sizeof err_buf,
			"--read-batch cannot be used with --files-from\n");
		return 0;
	}
	if (read_batch && remove_source_files) {
		snprintf(err_buf, sizeof err_buf,
			"--read-batch cannot be used with --remove-%s-files\n",
			remove_source_files == 1 ? "source" : "sent");
		return 0;
	}
	if (batch_name && strlen(batch_name) > MAX_BATCH_NAME_LEN) {
		snprintf(err_buf, sizeof err_buf,
			"the batch-file name must be %d characters or less.\n",
			MAX_BATCH_NAME_LEN);
		return 0;
	}

	if (tmpdir && strlen(tmpdir) >= MAXPATHLEN - 10) {
		snprintf(err_buf, sizeof err_buf,
			 "the --temp-dir path is WAY too long.\n");
		return 0;
	}

	if (max_delete < 0 && max_delete != INT_MIN) {
		/* Negative numbers are treated as "no deletions". */
		max_delete = 0;
	}

	if (compare_dest + copy_dest + link_dest > 1) {
		snprintf(err_buf, sizeof err_buf,
			"You may not mix --compare-dest, --copy-dest, and --link-dest.\n");
		return 0;
	}

	if (files_from) {
		if (recurse == 1) /* preserve recurse == 2 */
			recurse = 0;
		if (xfer_dirs < 0)
			xfer_dirs = 1;
	}

	if (argc < 2 && !read_batch && !am_server)
		list_only |= 1;

	if (xfer_dirs >= 4) {
		parse_filter_str(&filter_list, "- /*/*", rule_template(0), 0);
		recurse = xfer_dirs = 1;
	} else if (recurse)
		xfer_dirs = 1;
	else if (xfer_dirs < 0)
		xfer_dirs = list_only ? 1 : 0;

	if (relative_paths < 0)
		relative_paths = files_from? 1 : 0;
	if (!relative_paths)
		implied_dirs = 0;

	if (delete_before + !!delete_during + delete_after > 1) {
		snprintf(err_buf, sizeof err_buf,
			"You may not combine multiple --delete-WHEN options.\n");
		return 0;
	}
	if (delete_before || delete_during || delete_after)
		delete_mode = 1;
	else if (delete_mode || delete_excluded) {
		/* Only choose now between before & during if one is refused. */
		if (refused_delete_before) {
			if (!refused_delete_during)
				delete_during = 1;
			else {
				create_refuse_error(refused_delete_before);
				return 0;
			}
		} else if (refused_delete_during)
			delete_before = 1;
		delete_mode = 1;
	}
	if (!xfer_dirs && delete_mode) {
		snprintf(err_buf, sizeof err_buf,
			"--delete does not work without --recursive (-r) or --dirs (-d).\n");
		return 0;
	}

	if (missing_args == 3) /* simplify if both options were specified */
		missing_args = 2;
	if (refused_delete && (delete_mode || missing_args == 2)) {
		create_refuse_error(refused_delete);
		return 0;
	}

	if (remove_source_files) {
		/* We only want to infer this refusal of --remove-source-files
		 * via the refusal of "delete", not any of the "delete-FOO"
		 * options. */
		if (refused_delete && am_sender) {
			create_refuse_error(refused_delete);
			return 0;
		}
		need_messages_from_generator = 1;
	}

	if (munge_symlinks && !am_daemon) {
		STRUCT_STAT st;
		char prefix[SYMLINK_PREFIX_LEN]; /* NOT +1 ! */
		strlcpy(prefix, SYMLINK_PREFIX, sizeof prefix); /* trim the trailing slash */
		if (do_stat(prefix, &st) == 0 && S_ISDIR(st.st_mode)) {
			rprintf(FERROR, "Symlink munging is unsafe when a %s directory exists.\n",
				prefix);
			exit_cleanup(RERR_UNSUPPORTED);
		}
	}

	if (sanitize_paths) {
		int i;
		for (i = argc; i-- > 0; )
			argv[i] = sanitize_path(NULL, argv[i], "", 0, SP_KEEP_DOT_DIRS);
		if (tmpdir)
			tmpdir = sanitize_path(NULL, tmpdir, NULL, 0, SP_DEFAULT);
		if (backup_dir)
			backup_dir = sanitize_path(NULL, backup_dir, NULL, 0, SP_DEFAULT);
	}
	if (daemon_filter_list.head && !am_sender) {
		filter_rule_list *elp = &daemon_filter_list;
		if (tmpdir) {
			char *dir;
			if (!*tmpdir)
				goto options_rejected;
			dir = tmpdir + (*tmpdir == '/' ? module_dirlen : 0);
			clean_fname(dir, CFN_COLLAPSE_DOT_DOT_DIRS);
			if (check_filter(elp, FLOG, dir, 1) < 0)
				goto options_rejected;
		}
		if (backup_dir) {
			char *dir;
			if (!*backup_dir)
				goto options_rejected;
			dir = backup_dir + (*backup_dir == '/' ? module_dirlen : 0);
			clean_fname(dir, CFN_COLLAPSE_DOT_DOT_DIRS);
			if (check_filter(elp, FLOG, dir, 1) < 0)
				goto options_rejected;
		}
	}

	if (!backup_suffix)
		backup_suffix = backup_dir ? "" : BACKUP_SUFFIX;
	backup_suffix_len = strlen(backup_suffix);
	if (strchr(backup_suffix, '/') != NULL) {
		snprintf(err_buf, sizeof err_buf,
			"--suffix cannot contain slashes: %s\n",
			backup_suffix);
		return 0;
	}
	if (backup_dir) {
		size_t len;
		while (*backup_dir == '.' && backup_dir[1] == '/')
			backup_dir += 2;
		if (*backup_dir == '.' && backup_dir[1] == '\0')
			backup_dir++;
		len = strlcpy(backup_dir_buf, backup_dir, sizeof backup_dir_buf);
		if (len > sizeof backup_dir_buf - 128) {
			snprintf(err_buf, sizeof err_buf,
				"the --backup-dir path is WAY too long.\n");
			return 0;
		}
		backup_dir_len = (int)len;
		if (!backup_dir_len) {
			backup_dir_len = -1;
			backup_dir = NULL;
		} else if (backup_dir_buf[backup_dir_len - 1] != '/') {
			backup_dir_buf[backup_dir_len++] = '/';
			backup_dir_buf[backup_dir_len] = '\0';
		}
		backup_dir_remainder = sizeof backup_dir_buf - backup_dir_len;
	}
	if (backup_dir) {
		/* No need for a suffix or a protect rule. */
	} else if (!backup_suffix_len && (!am_server || !am_sender)) {
		snprintf(err_buf, sizeof err_buf,
			"--suffix cannot be empty %s\n", backup_dir_len < 0
			? "when --backup-dir is the same as the dest dir"
			: "without a --backup-dir");
		return 0;
	} else if (make_backups && delete_mode && !delete_excluded && !am_server) {
		snprintf(backup_dir_buf, sizeof backup_dir_buf,
			"P *%s", backup_suffix);
		parse_filter_str(&filter_list, backup_dir_buf, rule_template(0), 0);
	}

	if (preserve_times) {
		preserve_times = PRESERVE_FILE_TIMES;
		if (!omit_dir_times)
			preserve_times |= PRESERVE_DIR_TIMES;
#ifdef CAN_SET_SYMLINK_TIMES
		if (!omit_link_times)
			preserve_times |= PRESERVE_LINK_TIMES;
#endif
	}

	if (make_backups && !backup_dir) {
		omit_dir_times = 0; /* Implied, so avoid -O to sender. */
		preserve_times &= ~PRESERVE_DIR_TIMES;
	}

	if (stdout_format) {
		if (am_server && log_format_has(stdout_format, 'I'))
			stdout_format_has_i = 2;
		else if (log_format_has(stdout_format, 'i'))
			stdout_format_has_i = itemize_changes | 1;
		if (!log_format_has(stdout_format, 'b')
		 && !log_format_has(stdout_format, 'c')
		 && !log_format_has(stdout_format, 'C'))
			log_before_transfer = !am_server;
	} else if (itemize_changes) {
		stdout_format = "%i %n%L";
		stdout_format_has_i = itemize_changes;
		log_before_transfer = !am_server;
	}

	if (do_progress && !am_server) {
		if (!log_before_transfer && INFO_EQ(NAME, 0))
			parse_output_words(info_words, info_levels, "name", DEFAULT_PRIORITY);
		parse_output_words(info_words, info_levels, "flist2,progress", DEFAULT_PRIORITY);
	}

	if (dry_run)
		do_xfers = 0;

	set_io_timeout(io_timeout);

	if (INFO_GTE(NAME, 1) && !stdout_format) {
		stdout_format = "%n%L";
		log_before_transfer = !am_server;
	}
	if (stdout_format_has_i || log_format_has(stdout_format, 'o'))
		stdout_format_has_o_or_i = 1;

	if (logfile_name && !am_daemon) {
		if (!logfile_format) {
			logfile_format = "%i %n%L";
			logfile_format_has_i = logfile_format_has_o_or_i = 1;
		} else {
			if (log_format_has(logfile_format, 'i'))
				logfile_format_has_i = 1;
			if (logfile_format_has_i || log_format_has(logfile_format, 'o'))
				logfile_format_has_o_or_i = 1;
		}
		log_init(0);
	} else if (!am_daemon)
		logfile_format = NULL;

	if (daemon_bwlimit && (!bwlimit || bwlimit > daemon_bwlimit))
		bwlimit = daemon_bwlimit;
	if (bwlimit) {
		bwlimit_writemax = (size_t)bwlimit * 128;
		if (bwlimit_writemax < 512)
			bwlimit_writemax = 512;
	}

	if (append_mode) {
		if (whole_file > 0) {
			snprintf(err_buf, sizeof err_buf,
				 "--append cannot be used with --whole-file\n");
			return 0;
		}
		if (refused_inplace) {
			create_refuse_error(refused_inplace);
			return 0;
		}
		inplace = 1;
	}

	if (write_devices) {
		if (refused_inplace) {
			create_refuse_error(refused_inplace);
			return 0;
		}
		inplace = 1;
	}

	if (delay_updates && !partial_dir)
		partial_dir = tmp_partialdir;

	if (inplace) {
#ifdef HAVE_FTRUNCATE
		if (partial_dir) {
			snprintf(err_buf, sizeof err_buf,
				 "--%s cannot be used with --%s\n",
				 append_mode ? "append" : "inplace",
				 delay_updates ? "delay-updates" : "partial-dir");
			return 0;
		}
		/* --inplace implies --partial for refusal purposes, but we
		 * clear the keep_partial flag for internal logic purposes. */
		if (refused_partial) {
			create_refuse_error(refused_partial);
			return 0;
		}
		keep_partial = 0;
#else
		snprintf(err_buf, sizeof err_buf,
			 "--%s is not supported on this %s\n",
			 append_mode ? "append" : "inplace",
			 am_server ? "server" : "client");
		return 0;
#endif
	} else {
		if (keep_partial && !partial_dir && !am_server) {
			if ((arg = getenv("RSYNC_PARTIAL_DIR")) != NULL && *arg)
				partial_dir = strdup(arg);
		}
		if (partial_dir) {
			if (*partial_dir)
				clean_fname(partial_dir, CFN_COLLAPSE_DOT_DOT_DIRS);
			if (!*partial_dir || strcmp(partial_dir, ".") == 0)
				partial_dir = NULL;
			if (!partial_dir && refused_partial) {
				create_refuse_error(refused_partial);
				return 0;
			}
			keep_partial = 1;
		}
	}

	if (files_from) {
		char *h, *p;
		int q;
		if (argc > 2 || (!am_daemon && !am_server && argc == 1)) {
			usage(FERROR);
			exit_cleanup(RERR_SYNTAX);
		}
		if (strcmp(files_from, "-") == 0) {
			filesfrom_fd = 0;
			if (am_server)
				filesfrom_host = ""; /* reading from socket */
		} else if ((p = check_for_hostspec(files_from, &h, &q)) != 0) {
			if (am_server) {
				snprintf(err_buf, sizeof err_buf,
					"The --files-from sent to the server cannot specify a host.\n");
				return 0;
			}
			files_from = p;
			filesfrom_host = h;
			if (strcmp(files_from, "-") == 0) {
				snprintf(err_buf, sizeof err_buf,
					"Invalid --files-from remote filename\n");
				return 0;
			}
		} else {
			if (sanitize_paths)
				files_from = sanitize_path(NULL, files_from, NULL, 0, SP_DEFAULT);
			if (daemon_filter_list.head) {
				char *dir;
				if (!*files_from)
					goto options_rejected;
				dir = files_from + (*files_from == '/' ? module_dirlen : 0);
				clean_fname(dir, CFN_COLLAPSE_DOT_DOT_DIRS);
				if (check_filter(&daemon_filter_list, FLOG, dir, 0) < 0)
					goto options_rejected;
			}
			filesfrom_fd = open(files_from, O_RDONLY|O_BINARY);
			if (filesfrom_fd < 0) {
				snprintf(err_buf, sizeof err_buf,
					"failed to open files-from file %s: %s\n",
					files_from, strerror(errno));
				return 0;
			}
		}
	}

	am_starting_up = 0;

	return 1;

  options_rejected:
	snprintf(err_buf, sizeof err_buf,
		"Your options have been rejected by the server.\n");
	return 0;
}


/**
 * Construct a filtered list of options to pass through from the
 * client to the server.
 *
 * This involves setting options that will tell the server how to
 * behave, and also filtering out options that are processed only
 * locally.
 **/
void server_options(char **args, int *argc_p)
{
	static char argstr[64];
	int ac = *argc_p;
	uchar where;
	char *arg;
	int i, x;

	/* This should always remain first on the server's command-line. */
	args[ac++] = "--server";

	if (daemon_over_rsh > 0) {
		args[ac++] = "--daemon";
		*argc_p = ac;
		/* if we're passing --daemon, we're done */
		return;
	}

	if (!am_sender)
		args[ac++] = "--sender";

	x = 1;
	argstr[0] = '-';

	if (protect_args)
		argstr[x++] = 's';

	for (i = 0; i < verbose; i++)
		argstr[x++] = 'v';

	/* the -q option is intentionally left out */
	if (make_backups)
		argstr[x++] = 'b';
	if (update_only)
		argstr[x++] = 'u';
	if (!do_xfers) /* Note: NOT "dry_run"! */
		argstr[x++] = 'n';
	if (preserve_links)
		argstr[x++] = 'l';
	if ((xfer_dirs >= 2 && xfer_dirs < 4)
	 || (xfer_dirs && !recurse && (list_only || (delete_mode && am_sender))))
		argstr[x++] = 'd';
	if (am_sender) {
		if (keep_dirlinks)
			argstr[x++] = 'K';
		if (prune_empty_dirs)
			argstr[x++] = 'm';
		if (omit_dir_times)
			argstr[x++] = 'O';
		if (omit_link_times)
			argstr[x++] = 'J';
		if (fuzzy_basis) {
			argstr[x++] = 'y';
			if (fuzzy_basis > 1)
				argstr[x++] = 'y';
		}
	} else {
		if (copy_links)
			argstr[x++] = 'L';
		if (copy_dirlinks)
			argstr[x++] = 'k';
	}

	if (whole_file > 0)
		argstr[x++] = 'W';
	/* We don't need to send --no-whole-file, because it's the
	 * default for remote transfers, and in any case old versions
	 * of rsync will not understand it. */

	if (preserve_hard_links) {
		argstr[x++] = 'H';
		if (preserve_hard_links > 1)
			argstr[x++] = 'H';
	}
	if (preserve_uid)
		argstr[x++] = 'o';
	if (preserve_gid)
		argstr[x++] = 'g';
	if (preserve_devices) /* ignore preserve_specials here */
		argstr[x++] = 'D';
	if (preserve_times)
		argstr[x++] = 't';
	if (preserve_atimes) {
		argstr[x++] = 'U';
		if (preserve_atimes > 1)
			argstr[x++] = 'U';
	}
	if (preserve_perms)
		argstr[x++] = 'p';
	else if (preserve_executability && am_sender)
		argstr[x++] = 'E';
#ifdef SUPPORT_ACLS
	if (preserve_acls)
		argstr[x++] = 'A';
#endif
#ifdef SUPPORT_XATTRS
	if (preserve_xattrs) {
		argstr[x++] = 'X';
		if (preserve_xattrs > 1)
			argstr[x++] = 'X';
	}
#endif
	if (recurse)
		argstr[x++] = 'r';
	if (always_checksum)
		argstr[x++] = 'c';
	if (cvs_exclude)
		argstr[x++] = 'C';
	if (ignore_times)
		argstr[x++] = 'I';
	if (relative_paths)
		argstr[x++] = 'R';
	if (one_file_system) {
		argstr[x++] = 'x';
		if (one_file_system > 1)
			argstr[x++] = 'x';
	}
	if (sparse_files)
		argstr[x++] = 'S';
	if (do_compression == CPRES_ZLIB)
		argstr[x++] = 'z';

	set_allow_inc_recurse();

	/* We don't really know the actual protocol_version at this point,
	 * but checking the pre-negotiated value allows the user to use a
	 * --protocol=29 override to avoid the use of this -eFLAGS opt. */
	if (protocol_version >= 30) {
		/* Use "eFlags" alias so that cull_options doesn't think that these are no-arg option letters. */
#define eFlags argstr
		/* We make use of the -e option to let the server know about
		 * any pre-release protocol version && some behavior flags. */
		eFlags[x++] = 'e';
#if SUBPROTOCOL_VERSION != 0
		if (protocol_version == PROTOCOL_VERSION) {
			x += snprintf(argstr+x, sizeof argstr - x,
				      "%d.%d",
				      PROTOCOL_VERSION, SUBPROTOCOL_VERSION);
		} else
#endif
			eFlags[x++] = '.';
		if (allow_inc_recurse)
			eFlags[x++] = 'i';
#ifdef CAN_SET_SYMLINK_TIMES
		eFlags[x++] = 'L'; /* symlink time-setting support */
#endif
#ifdef ICONV_OPTION
		eFlags[x++] = 's'; /* symlink iconv translation support */
#endif
		eFlags[x++] = 'f'; /* flist I/O-error safety support */
		eFlags[x++] = 'x'; /* xattr hardlink optimization not desired */
		eFlags[x++] = 'C'; /* support checksum seed order fix */
		eFlags[x++] = 'I'; /* support inplace_partial behavior */
		eFlags[x++] = 'v'; /* use varint for flist & compat flags; negotiate checksum */
		/* NOTE: Avoid using 'V' -- it was the high bit of a write_byte() that became write_varint(). */
#undef eFlags
	}

	if (x >= (int)sizeof argstr) { /* Not possible... */
		rprintf(FERROR, "argstr overflow in server_options().\n");
		exit_cleanup(RERR_MALLOC);
	}

	argstr[x] = '\0';

	if (x > 1)
		args[ac++] = argstr;

#ifdef ICONV_OPTION
	if (iconv_opt) {
		char *set = strchr(iconv_opt, ',');
		if (set)
			set++;
		else
			set = iconv_opt;
		if (asprintf(&arg, "--iconv=%s", set) < 0)
			goto oom;
		args[ac++] = arg;
	}
#endif

	if (protect_args && !local_server) /* unprotected args stop here */
		args[ac++] = NULL;

	if (list_only > 1)
		args[ac++] = "--list-only";

	/* This makes sure that the remote rsync can handle deleting with -d
	 * sans -r because the --no-r option was added at the same time. */
	if (xfer_dirs && !recurse && delete_mode && am_sender)
		args[ac++] = "--no-r";

	if (do_compression && do_compression_level != CLVL_NOT_SPECIFIED) {
		if (asprintf(&arg, "--compress-level=%d", do_compression_level) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (preserve_devices) {
		/* Note: sending "--devices" would not be backward-compatible. */
		if (!preserve_specials)
			args[ac++] = "--no-specials"; /* -D is already set. */
	} else if (preserve_specials)
		args[ac++] = "--specials";

	/* The server side doesn't use our log-format, but in certain
	 * circumstances they need to know a little about the option. */
	if (stdout_format && am_sender) {
		/* Use --log-format, not --out-format, for compatibility. */
		if (stdout_format_has_i > 1)
			args[ac++] = "--log-format=%i%I";
		else if (stdout_format_has_i)
			args[ac++] = "--log-format=%i";
		else if (stdout_format_has_o_or_i)
			args[ac++] = "--log-format=%o";
		else if (!verbose)
			args[ac++] = "--log-format=X";
	}

	if (block_size) {
		if (asprintf(&arg, "-B%lu", block_size) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (io_timeout) {
		if (asprintf(&arg, "--timeout=%d", io_timeout) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (bwlimit) {
		if (asprintf(&arg, "--bwlimit=%d", bwlimit) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (backup_dir) {
		args[ac++] = "--backup-dir";
		args[ac++] = backup_dir;
	}

	/* Only send --suffix if it specifies a non-default value. */
	if (strcmp(backup_suffix, backup_dir ? "" : BACKUP_SUFFIX) != 0) {
		/* We use the following syntax to avoid weirdness with '~'. */
		if (asprintf(&arg, "--suffix=%s", backup_suffix) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (checksum_choice) {
		if (asprintf(&arg, "--checksum-choice=%s", checksum_choice) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (do_compression == CPRES_ZLIBX)
		args[ac++] = "--new-compress";
	else if (compress_choice && do_compression == CPRES_ZLIB)
		args[ac++] = "--old-compress";
	else if (compress_choice) {
		if (asprintf(&arg, "--compress-choice=%s", compress_choice) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (am_sender) {
		if (max_delete > 0) {
			if (asprintf(&arg, "--max-delete=%d", max_delete) < 0)
				goto oom;
			args[ac++] = arg;
		} else if (max_delete == 0)
			args[ac++] = "--max-delete=-1";
		if (min_size >= 0) {
			args[ac++] = "--min-size";
			args[ac++] = min_size_arg;
		}
		if (max_size >= 0) {
			args[ac++] = "--max-size";
			args[ac++] = max_size_arg;
		}
		if (delete_before)
			args[ac++] = "--delete-before";
		else if (delete_during == 2)
			args[ac++] = "--delete-delay";
		else if (delete_during)
			args[ac++] = "--delete-during";
		else if (delete_after)
			args[ac++] = "--delete-after";
		else if (delete_mode && !delete_excluded)
			args[ac++] = "--delete";
		if (delete_excluded)
			args[ac++] = "--delete-excluded";
		if (force_delete)
			args[ac++] = "--force";
		if (write_batch < 0)
			args[ac++] = "--only-write-batch=X";
		if (am_root > 1)
			args[ac++] = "--super";
		if (size_only)
			args[ac++] = "--size-only";
		if (do_stats)
			args[ac++] = "--stats";
	} else {
		if (skip_compress) {
			if (asprintf(&arg, "--skip-compress=%s", skip_compress) < 0)
				goto oom;
			args[ac++] = arg;
		}
	}

	/* --delete-missing-args needs the cooperation of both sides, but
	 * the sender can handle --ignore-missing-args by itself. */
	if (missing_args == 2)
		args[ac++] = "--delete-missing-args";
	else if (missing_args == 1 && !am_sender)
		args[ac++] = "--ignore-missing-args";

	if (modify_window_set && am_sender) {
		char *fmt = modify_window < 0 ? "-@%d" : "--modify-window=%d";
		if (asprintf(&arg, fmt, modify_window) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (checksum_seed) {
		if (asprintf(&arg, "--checksum-seed=%d", checksum_seed) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (partial_dir && am_sender) {
		if (partial_dir != tmp_partialdir) {
			args[ac++] = "--partial-dir";
			args[ac++] = partial_dir;
		}
		if (delay_updates)
			args[ac++] = "--delay-updates";
	} else if (keep_partial && am_sender)
		args[ac++] = "--partial";

	if (ignore_errors)
		args[ac++] = "--ignore-errors";

	if (copy_unsafe_links)
		args[ac++] = "--copy-unsafe-links";

	if (safe_symlinks)
		args[ac++] = "--safe-links";

	if (numeric_ids)
		args[ac++] = "--numeric-ids";

	if (use_qsort)
		args[ac++] = "--use-qsort";

	if (am_sender) {
		if (usermap) {
			if (asprintf(&arg, "--usermap=%s", usermap) < 0)
				goto oom;
			args[ac++] = arg;
		}

		if (groupmap) {
			if (asprintf(&arg, "--groupmap=%s", groupmap) < 0)
				goto oom;
			args[ac++] = arg;
		}

		if (ignore_existing)
			args[ac++] = "--ignore-existing";

		/* Backward compatibility: send --existing, not --ignore-non-existing. */
		if (ignore_non_existing)
			args[ac++] = "--existing";

		if (tmpdir) {
			args[ac++] = "--temp-dir";
			args[ac++] = tmpdir;
		}

		if (basis_dir[0]) {
			/* the server only needs this option if it is not the sender,
			 *   and it may be an older version that doesn't know this
			 *   option, so don't send it if client is the sender.
			 */
			for (i = 0; i < basis_dir_cnt; i++) {
				args[ac++] = dest_option;
				args[ac++] = basis_dir[i];
			}
		}
	}

	/* What flags do we need to send to the other side? */
	where = (am_server ? W_CLI : W_SRV) | (am_sender ? W_REC : W_SND);
	arg = make_output_option(info_words, info_levels, where);
	if (arg)
		args[ac++] = arg;

	if (append_mode) {
		if (append_mode > 1)
			args[ac++] = "--append";
		args[ac++] = "--append";
	} else if (inplace)
		args[ac++] = "--inplace";

	if (files_from && (!am_sender || filesfrom_host)) {
		if (filesfrom_host) {
			args[ac++] = "--files-from";
			args[ac++] = files_from;
			if (eol_nulls)
				args[ac++] = "--from0";
		} else {
			args[ac++] = "--files-from=-";
			args[ac++] = "--from0";
		}
		if (!relative_paths)
			args[ac++] = "--no-relative";
	}
	/* It's OK that this checks the upper-bound of the protocol_version. */
	if (relative_paths && !implied_dirs && (!am_sender || protocol_version >= 30))
		args[ac++] = "--no-implied-dirs";

	if (write_devices && am_sender)
		args[ac++] = "--write-devices";

	if (remove_source_files == 1)
		args[ac++] = "--remove-source-files";
	else if (remove_source_files)
		args[ac++] = "--remove-sent-files";

	if (preallocate_files && am_sender)
		args[ac++] = "--preallocate";

	if (open_noatime && preserve_atimes <= 1)
		args[ac++] = "--open-noatime";

	if (ac > MAX_SERVER_ARGS) { /* Not possible... */
		rprintf(FERROR, "argc overflow in server_options().\n");
		exit_cleanup(RERR_MALLOC);
	}

	if (remote_option_cnt) {
		int j;
		if (ac + remote_option_cnt > MAX_SERVER_ARGS) {
			rprintf(FERROR, "too many remote options specified.\n");
			exit_cleanup(RERR_SYNTAX);
		}
		for (j = 1; j <= remote_option_cnt; j++)
			args[ac++] = (char*)remote_options[j];
	}

	*argc_p = ac;
	return;

    oom:
	out_of_memory("server_options");
}

/* If str points to a valid hostspec, return allocated memory containing the
 * [USER@]HOST part of the string, and set the path_start_ptr to the part of
 * the string after the host part.  Otherwise, return NULL.  If port_ptr is
 * non-NULL, we must be parsing an rsync:// URL hostname, and we will set
 * *port_ptr if a port number is found.  Note that IPv6 IPs will have their
 * (required for parsing) [ and ] chars elided from the returned string. */
static char *parse_hostspec(char *str, char **path_start_ptr, int *port_ptr)
{
	char *s, *host_start = str;
	int hostlen = 0, userlen = 0;
	char *ret;

	for (s = str; ; s++) {
		if (!*s) {
			/* It is only OK if we run out of string with rsync:// */
			if (!port_ptr)
				return NULL;
			if (!hostlen)
				hostlen = s - host_start;
			break;
		}
		if (*s == ':' || *s == '/') {
			if (!hostlen)
				hostlen = s - host_start;
			if (*s++ == '/') {
				if (!port_ptr)
					return NULL;
			} else if (port_ptr) {
				*port_ptr = atoi(s);
				while (isDigit(s)) s++;
				if (*s && *s++ != '/')
					return NULL;
			}
			break;
		}
		if (*s == '@') {
			userlen = s - str + 1;
			host_start = s + 1;
		} else if (*s == '[') {
			if (s != host_start++)
				return NULL;
			while (*s && *s != ']' && *s != '/') s++; /*SHARED ITERATOR*/
			hostlen = s - host_start;
			if (*s != ']' || (s[1] && s[1] != '/' && s[1] != ':') || !hostlen)
				return NULL;
		}
	}

	*path_start_ptr = s;
	ret = new_array(char, userlen + hostlen + 1);
	if (userlen)
		strlcpy(ret, str, userlen + 1);
	strlcpy(ret + userlen, host_start, hostlen + 1);
	return ret;
}

/* Look for a HOST specification of the form "HOST:PATH", "HOST::PATH", or
 * "rsync://HOST:PORT/PATH".  If found, *host_ptr will be set to some allocated
 * memory with the HOST.  If a daemon-accessing spec was specified, the value
 * of *port_ptr will contain a non-0 port number, otherwise it will be set to
 * 0.  The return value is a pointer to the PATH.  Note that the HOST spec can
 * be an IPv6 literal address enclosed in '[' and ']' (such as "[::1]" or
 * "[::ffff:127.0.0.1]") which is returned without the '[' and ']'. */
char *check_for_hostspec(char *s, char **host_ptr, int *port_ptr)
{
	char *path;

	if (port_ptr && strncasecmp(URL_PREFIX, s, strlen(URL_PREFIX)) == 0) {
		*host_ptr = parse_hostspec(s + strlen(URL_PREFIX), &path, port_ptr);
		if (*host_ptr) {
			if (!*port_ptr)
				*port_ptr = -1; /* -1 indicates they want the default */
			return path;
		}
	}

	*host_ptr = parse_hostspec(s, &path, NULL);
	if (!*host_ptr)
		return NULL;

	if (*path == ':') {
		if (port_ptr && !*port_ptr)
			*port_ptr = -1;
		return path + 1;
	}
	if (port_ptr)
		*port_ptr = 0;

	return path;
}
