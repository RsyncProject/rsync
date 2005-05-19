/*  -*- c-file-style: "linux" -*-
 *
 * Copyright (C) 1998-2001 by Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2000, 2001, 2002 by Martin Pool <mbp@samba.org>
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
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

#include "rsync.h"
#include "popt.h"

extern int module_id;
extern int sanitize_paths;
extern struct filter_list_struct filter_list;
extern struct filter_list_struct server_filter_list;

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

int archive_mode = 0;
int keep_dirlinks = 0;
int copy_links = 0;
int preserve_links = 0;
int preserve_hard_links = 0;
int preserve_perms = 0;
int preserve_devices = 0;
int preserve_uid = 0;
int preserve_gid = 0;
int preserve_times = 0;
int omit_dir_times = 0;
int update_only = 0;
int cvs_exclude = 0;
int dry_run = 0;
int do_xfers = 1;
int ignore_times = 0;
int delete_mode = 0;
int delete_during = 0;
int delete_before = 0;
int delete_after = 0;
int delete_excluded = 0;
int remove_sent_files = 0;
int one_file_system = 0;
int protocol_version = PROTOCOL_VERSION;
int sparse_files = 0;
int do_compression = 0;
int am_root = 0;
int am_server = 0;
int am_sender = 0;
int am_generator = 0;
int am_starting_up = 1;
int orig_umask = 0;
int relative_paths = -1;
int implied_dirs = 1;
int numeric_ids = 0;
int force_delete = 0;
int io_timeout = 0;
int allowed_lull = 0;
char *files_from = NULL;
int filesfrom_fd = -1;
char *filesfrom_host = NULL;
int eol_nulls = 0;
int recurse = 0;
int xfer_dirs = 0;
int am_daemon = 0;
int daemon_over_rsh = 0;
int do_stats = 0;
int do_progress = 0;
int keep_partial = 0;
int safe_symlinks = 0;
int copy_unsafe_links = 0;
int size_only = 0;
int daemon_bwlimit = 0;
int bwlimit = 0;
int fuzzy_basis = 0;
size_t bwlimit_writemax = 0;
int only_existing = 0;
int opt_ignore_existing = 0;
int need_messages_from_generator = 0;
int max_delete = 0;
OFF_T max_size = 0;
int ignore_errors = 0;
int modify_window = 0;
int blocking_io = -1;
int checksum_seed = 0;
int inplace = 0;
int delay_updates = 0;
long block_size = 0; /* "long" because popt can't set an int32. */


/** Network address family. **/
#ifdef INET6
int default_af_hint = 0;	/* Any protocol */
#else
int default_af_hint = AF_INET;	/* Must use IPv4 */
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
char *log_format = NULL;
char *password_file = NULL;
char *rsync_path = RSYNC_PATH;
char *backup_dir = NULL;
char backup_dir_buf[MAXPATHLEN];
int rsync_port = 0;
int compare_dest = 0;
int copy_dest = 0;
int link_dest = 0;
int basis_dir_cnt = 0;
char *dest_option = NULL;

int verbose = 0;
int quiet = 0;
int log_before_transfer = 0;
int log_format_has_i = 0;
int log_format_has_o_or_i = 0;
int always_checksum = 0;
int list_only = 0;

#define MAX_BATCH_NAME_LEN 256	/* Must be less than MAXPATHLEN-13 */
char *batch_name = NULL;

static int daemon_opt;   /* sets am_daemon after option error-reporting */
static int F_option_cnt = 0;
static int modify_window_set;
static int itemize_changes = 0;
static int refused_delete, refused_archive_part;
static int refused_partial, refused_progress, refused_delete_before;
static char *max_size_arg;
static char partialdir_for_delayupdate[] = ".~tmp~";

/** Local address to bind.  As a character string because it's
 * interpreted by the IPv6 layer: should be a numeric IP4 or IP6
 * address, or a hostname. **/
char *bind_address;


static void print_rsync_version(enum logcode f)
{
	char const *got_socketpair = "no ";
	char const *have_inplace = "no ";
	char const *hardlinks = "no ";
	char const *links = "no ";
	char const *ipv6 = "no ";
	STRUCT_STAT *dumstat;

#ifdef HAVE_SOCKETPAIR
	got_socketpair = "";
#endif

#ifdef HAVE_FTRUNCATE
	have_inplace = "";
#endif

#ifdef SUPPORT_HARD_LINKS
	hardlinks = "";
#endif

#ifdef SUPPORT_LINKS
	links = "";
#endif

#ifdef INET6
	ipv6 = "";
#endif

	rprintf(f, "%s  version %s  protocol version %d\n",
		RSYNC_NAME, RSYNC_VERSION, PROTOCOL_VERSION);
	rprintf(f,
		"Copyright (C) 1996-2005 by Andrew Tridgell and others\n");
	rprintf(f, "<http://rsync.samba.org/>\n");
	rprintf(f, "Capabilities: %d-bit files, %ssocketpairs, "
		"%shard links, %ssymlinks, batchfiles, \n",
		(int) (sizeof (OFF_T) * 8),
		got_socketpair, hardlinks, links);

	/* Note that this field may not have type ino_t.  It depends
	 * on the complicated interaction between largefile feature
	 * macros. */
	rprintf(f, "              %sinplace, %sIPv6, %d-bit system inums, %d-bit internal inums\n",
		have_inplace, ipv6,
		(int) (sizeof dumstat->st_ino * 8),
		(int) (sizeof (int64) * 8));
#ifdef MAINTAINER_MODE
	rprintf(f, "              panic action: \"%s\"\n",
		get_panic_action());
#endif

#if SIZEOF_INT64 < 8
	rprintf(f, "WARNING: no 64-bit integers on this platform!\n");
#endif
	if (sizeof (int64) != SIZEOF_INT64) {
		rprintf(f,
			"WARNING: size mismatch in SIZEOF_INT64 define (%d != %d)\n",
			(int) SIZEOF_INT64, (int) sizeof (int64));
	}

	rprintf(f,
"\n"
"rsync comes with ABSOLUTELY NO WARRANTY.  This is free software, and you\n"
"are welcome to redistribute it under certain conditions.  See the GNU\n"
"General Public Licence for details.\n"
		);
}


void usage(enum logcode F)
{
  print_rsync_version(F);

  rprintf(F,"\nrsync is a file transfer program capable of efficient remote update\nvia a fast differencing algorithm.\n\n");

  rprintf(F,"Usage: rsync [OPTION]... SRC [SRC]... [USER@]HOST:DEST\n");
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST:SRC [DEST]\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... DEST\n");
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST::SRC [DEST]\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... [USER@]HOST::DEST\n");
  rprintf(F,"  or   rsync [OPTION]... rsync://[USER@]HOST[:PORT]/SRC [DEST]\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... rsync://[USER@]HOST[:PORT]/DEST\n");
  rprintf(F,"SRC on single-colon remote HOST will be expanded by remote shell\n");
  rprintf(F,"SRC on server remote HOST may contain shell wildcards or multiple\n");
  rprintf(F,"  sources separated by space as long as they have same top-level\n");
  rprintf(F,"\nOptions\n");
  rprintf(F," -v, --verbose               increase verbosity\n");
  rprintf(F," -q, --quiet                 suppress non-error messages\n");
  rprintf(F," -c, --checksum              skip based on checksum, not mod-time & size\n");
  rprintf(F," -a, --archive               archive mode; same as -rlptgoD (no -H)\n");
  rprintf(F," -r, --recursive             recurse into directories\n");
  rprintf(F," -R, --relative              use relative path names\n");
  rprintf(F,"     --no-relative           turn off --relative\n");
  rprintf(F,"     --no-implied-dirs       don't send implied dirs with -R\n");
  rprintf(F," -b, --backup                make backups (see --suffix & --backup-dir)\n");
  rprintf(F,"     --backup-dir=DIR        make backups into hierarchy based in DIR\n");
  rprintf(F,"     --suffix=SUFFIX         set backup suffix (default %s w/o --backup-dir)\n",BACKUP_SUFFIX);
  rprintf(F," -u, --update                skip files that are newer on the receiver\n");
  rprintf(F,"     --inplace               update destination files in-place (SEE MAN PAGE)\n");
  rprintf(F," -d, --dirs                  transfer directories without recursing\n");
  rprintf(F," -l, --links                 copy symlinks as symlinks\n");
  rprintf(F," -L, --copy-links            transform symlink into referent file/dir\n");
  rprintf(F,"     --copy-unsafe-links     only \"unsafe\" symlinks are transformed\n");
  rprintf(F,"     --safe-links            ignore symlinks that point outside the source tree\n");
  rprintf(F," -H, --hard-links            preserve hard links\n");
  rprintf(F," -K, --keep-dirlinks         treat symlinked dir on receiver as dir\n");
  rprintf(F," -p, --perms                 preserve permissions\n");
  rprintf(F," -o, --owner                 preserve owner (root only)\n");
  rprintf(F," -g, --group                 preserve group\n");
  rprintf(F," -D, --devices               preserve devices (root only)\n");
  rprintf(F," -t, --times                 preserve times\n");
  rprintf(F," -O, --omit-dir-times        omit directories when preserving times\n");
  rprintf(F," -S, --sparse                handle sparse files efficiently\n");
  rprintf(F," -n, --dry-run               show what would have been transferred\n");
  rprintf(F," -W, --whole-file            copy files whole (without rsync algorithm)\n");
  rprintf(F,"     --no-whole-file         always use incremental rsync algorithm\n");
  rprintf(F," -x, --one-file-system       don't cross filesystem boundaries\n");
  rprintf(F," -B, --block-size=SIZE       force a fixed checksum block-size\n");
  rprintf(F," -e, --rsh=COMMAND           specify the remote shell to use\n");
  rprintf(F,"     --rsync-path=PROGRAM    specify the rsync to run on the remote machine\n");
  rprintf(F,"     --existing              only update files that already exist on receiver\n");
  rprintf(F,"     --ignore-existing       ignore files that already exist on receiving side\n");
  rprintf(F,"     --remove-sent-files     sent files/symlinks are removed from sending side\n");
  rprintf(F,"     --del                   an alias for --delete-during\n");
  rprintf(F,"     --delete                delete files that don't exist on the sending side\n");
  rprintf(F,"     --delete-before         receiver deletes before transfer (default)\n");
  rprintf(F,"     --delete-during         receiver deletes during transfer, not before\n");
  rprintf(F,"     --delete-after          receiver deletes after transfer, not before\n");
  rprintf(F,"     --delete-excluded       also delete excluded files on the receiving side\n");
  rprintf(F,"     --ignore-errors         delete even if there are I/O errors\n");
  rprintf(F,"     --force                 force deletion of directories even if not empty\n");
  rprintf(F,"     --max-delete=NUM        don't delete more than NUM files\n");
  rprintf(F,"     --max-size=SIZE         don't transfer any file larger than SIZE\n");
  rprintf(F,"     --partial               keep partially transferred files\n");
  rprintf(F,"     --partial-dir=DIR       put a partially transferred file into DIR\n");
  rprintf(F,"     --delay-updates         put all updated files into place at transfer's end\n");
  rprintf(F,"     --numeric-ids           don't map uid/gid values by user/group name\n");
  rprintf(F,"     --timeout=TIME          set I/O timeout in seconds\n");
  rprintf(F," -I, --ignore-times          don't skip files that match in size and mod-time\n");
  rprintf(F,"     --size-only             skip files that match in size\n");
  rprintf(F,"     --modify-window=NUM     compare mod-times with reduced accuracy\n");
  rprintf(F," -T, --temp-dir=DIR          create temporary files in directory DIR\n");
  rprintf(F," -y, --fuzzy                 find similar file for basis if no dest file\n");
  rprintf(F,"     --compare-dest=DIR      also compare destination files relative to DIR\n");
  rprintf(F,"     --copy-dest=DIR         ... and include copies of unchanged files\n");
  rprintf(F,"     --link-dest=DIR         hardlink to files in DIR when unchanged\n");
  rprintf(F," -z, --compress              compress file data during the transfer\n");
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
  rprintf(F,"     --address=ADDRESS       bind address for outgoing socket to daemon\n");
  rprintf(F,"     --port=PORT             specify double-colon alternate port number\n");
  rprintf(F,"     --blocking-io           use blocking I/O for the remote shell\n");
  rprintf(F,"     --no-blocking-io        turn off blocking I/O when it is the default\n");
  rprintf(F,"     --stats                 give some file-transfer stats\n");
  rprintf(F,"     --progress              show progress during transfer\n");
  rprintf(F," -P                          same as --partial --progress\n");
  rprintf(F," -i, --itemize-changes       output a change-summary for all updates\n");
  rprintf(F,"     --log-format=FORMAT     output filenames using the specified format\n");
  rprintf(F,"     --password-file=FILE    read password from FILE\n");
  rprintf(F,"     --list-only             list the files instead of copying them\n");
  rprintf(F,"     --bwlimit=KBPS          limit I/O bandwidth; KBytes per second\n");
  rprintf(F,"     --write-batch=FILE      write a batched update to FILE\n");
  rprintf(F,"     --only-write-batch=FILE like --write-batch but w/o updating destination\n");
  rprintf(F,"     --read-batch=FILE       read a batched update from FILE\n");
  rprintf(F,"     --protocol=NUM          force an older protocol version to be used\n");
#ifdef INET6
  rprintf(F," -4, --ipv4                  prefer IPv4\n");
  rprintf(F," -6, --ipv6                  prefer IPv6\n");
#endif
  rprintf(F,"     --version               print version number\n");
  rprintf(F," -h, --help                  show this help screen\n");

  rprintf(F,"\nUse \"rsync --daemon --help\" to see the daemon-mode command-line options.\n");
  rprintf(F,"Please see the rsync(1) and rsyncd.conf(5) man pages for full documentation.\n");
  rprintf(F,"See http://rsync.samba.org/ for updates, bug reports, and answers\n");
}

enum {OPT_VERSION = 1000, OPT_DAEMON, OPT_SENDER, OPT_EXCLUDE, OPT_EXCLUDE_FROM,
      OPT_FILTER, OPT_COMPARE_DEST, OPT_COPY_DEST, OPT_LINK_DEST,
      OPT_INCLUDE, OPT_INCLUDE_FROM, OPT_MODIFY_WINDOW,
      OPT_READ_BATCH, OPT_WRITE_BATCH, OPT_ONLY_WRITE_BATCH, OPT_MAX_SIZE,
      OPT_REFUSED_BASE = 9000};

static struct poptOption long_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"version",          0,  POPT_ARG_NONE,   0, OPT_VERSION, 0, 0},
  {"suffix",           0,  POPT_ARG_STRING, &backup_suffix, 0, 0, 0 },
  {"rsync-path",       0,  POPT_ARG_STRING, &rsync_path, 0, 0, 0 },
  {"password-file",    0,  POPT_ARG_STRING, &password_file, 0, 0, 0 },
  {"ignore-times",    'I', POPT_ARG_NONE,   &ignore_times, 0, 0, 0 },
  {"size-only",        0,  POPT_ARG_NONE,   &size_only, 0, 0, 0 },
  {"modify-window",    0,  POPT_ARG_INT,    &modify_window, OPT_MODIFY_WINDOW, 0, 0 },
  {"one-file-system", 'x', POPT_ARG_NONE,   &one_file_system, 0, 0, 0 },
  {"existing",         0,  POPT_ARG_NONE,   &only_existing, 0, 0, 0 },
  {"ignore-existing",  0,  POPT_ARG_NONE,   &opt_ignore_existing, 0, 0, 0 },
  {"del",              0,  POPT_ARG_NONE,   &delete_during, 0, 0, 0 },
  {"delete",           0,  POPT_ARG_NONE,   &delete_mode, 0, 0, 0 },
  {"delete-before",    0,  POPT_ARG_VAL,    &delete_before, 2, 0, 0 },
  {"delete-during",    0,  POPT_ARG_NONE,   &delete_during, 0, 0, 0 },
  {"delete-after",     0,  POPT_ARG_NONE,   &delete_after, 0, 0, 0 },
  {"delete-excluded",  0,  POPT_ARG_NONE,   &delete_excluded, 0, 0, 0 },
  {"remove-sent-files",0,  POPT_ARG_NONE,   &remove_sent_files, 0, 0, 0 },
  {"force",            0,  POPT_ARG_NONE,   &force_delete, 0, 0, 0 },
  {"numeric-ids",      0,  POPT_ARG_NONE,   &numeric_ids, 0, 0, 0 },
  {"filter",          'f', POPT_ARG_STRING, 0, OPT_FILTER, 0, 0 },
  {"exclude",          0,  POPT_ARG_STRING, 0, OPT_EXCLUDE, 0, 0 },
  {"include",          0,  POPT_ARG_STRING, 0, OPT_INCLUDE, 0, 0 },
  {"exclude-from",     0,  POPT_ARG_STRING, 0, OPT_EXCLUDE_FROM, 0, 0 },
  {"include-from",     0,  POPT_ARG_STRING, 0, OPT_INCLUDE_FROM, 0, 0 },
  {"safe-links",       0,  POPT_ARG_NONE,   &safe_symlinks, 0, 0, 0 },
  {"help",            'h', POPT_ARG_NONE,   0, 'h', 0, 0 },
  {"backup",          'b', POPT_ARG_NONE,   &make_backups, 0, 0, 0 },
  {"dry-run",         'n', POPT_ARG_NONE,   &dry_run, 0, 0, 0 },
  {"sparse",          'S', POPT_ARG_NONE,   &sparse_files, 0, 0, 0 },
  {"cvs-exclude",     'C', POPT_ARG_NONE,   &cvs_exclude, 0, 0, 0 },
  {"update",          'u', POPT_ARG_NONE,   &update_only, 0, 0, 0 },
  {"inplace",          0,  POPT_ARG_NONE,   &inplace, 0, 0, 0 },
  {"dirs",            'd', POPT_ARG_VAL,    &xfer_dirs, 2, 0, 0 },
  {"links",           'l', POPT_ARG_NONE,   &preserve_links, 0, 0, 0 },
  {"copy-links",      'L', POPT_ARG_NONE,   &copy_links, 0, 0, 0 },
  {"keep-dirlinks",   'K', POPT_ARG_NONE,   &keep_dirlinks, 0, 0, 0 },
  {"whole-file",      'W', POPT_ARG_VAL,    &whole_file, 1, 0, 0 },
  {"no-whole-file",    0,  POPT_ARG_VAL,    &whole_file, 0, 0, 0 },
  {"copy-unsafe-links",0,  POPT_ARG_NONE,   &copy_unsafe_links, 0, 0, 0 },
  {"perms",           'p', POPT_ARG_NONE,   &preserve_perms, 0, 0, 0 },
  {"owner",           'o', POPT_ARG_NONE,   &preserve_uid, 0, 0, 0 },
  {"group",           'g', POPT_ARG_NONE,   &preserve_gid, 0, 0, 0 },
  {"devices",         'D', POPT_ARG_NONE,   &preserve_devices, 0, 0, 0 },
  {"times",           't', POPT_ARG_NONE,   &preserve_times, 0, 0, 0 },
  {"omit-dir-times",  'O', POPT_ARG_VAL,    &omit_dir_times, 2, 0, 0 },
  {"checksum",        'c', POPT_ARG_NONE,   &always_checksum, 0, 0, 0 },
  {"verbose",         'v', POPT_ARG_NONE,   0, 'v', 0, 0 },
  {"quiet",           'q', POPT_ARG_NONE,   0, 'q', 0, 0 },
  {"archive",         'a', POPT_ARG_NONE,   &archive_mode, 0, 0, 0 },
  {"server",           0,  POPT_ARG_NONE,   &am_server, 0, 0, 0 },
  {"sender",           0,  POPT_ARG_NONE,   0, OPT_SENDER, 0, 0 },
  {"recursive",       'r', POPT_ARG_NONE,   &recurse, 0, 0, 0 },
  {"list-only",        0,  POPT_ARG_VAL,    &list_only, 2, 0, 0 },
  {"relative",        'R', POPT_ARG_VAL,    &relative_paths, 1, 0, 0 },
  {"no-relative",      0,  POPT_ARG_VAL,    &relative_paths, 0, 0, 0 },
  {"rsh",             'e', POPT_ARG_STRING, &shell_cmd, 0, 0, 0 },
  {"block-size",      'B', POPT_ARG_LONG,   &block_size, 0, 0, 0 },
  {"max-delete",       0,  POPT_ARG_INT,    &max_delete, 0, 0, 0 },
  {"max-size",         0,  POPT_ARG_STRING, &max_size_arg,  OPT_MAX_SIZE, 0, 0 },
  {"timeout",          0,  POPT_ARG_INT,    &io_timeout, 0, 0, 0 },
  {"temp-dir",        'T', POPT_ARG_STRING, &tmpdir, 0, 0, 0 },
  {"compare-dest",     0,  POPT_ARG_STRING, 0, OPT_COMPARE_DEST, 0, 0 },
  {"copy-dest",        0,  POPT_ARG_STRING, 0, OPT_COPY_DEST, 0, 0 },
  {"link-dest",        0,  POPT_ARG_STRING, 0, OPT_LINK_DEST, 0, 0 },
  {"fuzzy",           'y', POPT_ARG_NONE,   &fuzzy_basis, 0, 0, 0 },
  /* TODO: Should this take an optional int giving the compression level? */
  {"compress",        'z', POPT_ARG_NONE,   &do_compression, 0, 0, 0 },
  {"stats",            0,  POPT_ARG_NONE,   &do_stats, 0, 0, 0 },
  {"progress",         0,  POPT_ARG_NONE,   &do_progress, 0, 0, 0 },
  {"partial",          0,  POPT_ARG_NONE,   &keep_partial, 0, 0, 0 },
  {"partial-dir",      0,  POPT_ARG_STRING, &partial_dir, 0, 0, 0 },
  {"delay-updates",    0,  POPT_ARG_NONE,   &delay_updates, 0, 0, 0 },
  {"ignore-errors",    0,  POPT_ARG_NONE,   &ignore_errors, 0, 0, 0 },
  {"blocking-io",      0,  POPT_ARG_VAL,    &blocking_io, 1, 0, 0 },
  {"no-blocking-io",   0,  POPT_ARG_VAL,    &blocking_io, 0, 0, 0 },
  {0,                 'F', POPT_ARG_NONE,   0, 'F', 0, 0 },
  {0,                 'P', POPT_ARG_NONE,   0, 'P', 0, 0 },
  {"address",          0,  POPT_ARG_STRING, &bind_address, 0, 0, 0 },
  {"port",             0,  POPT_ARG_INT,    &rsync_port, 0, 0, 0 },
  {"log-format",       0,  POPT_ARG_STRING, &log_format, 0, 0, 0 },
  {"itemize-changes", 'i', POPT_ARG_NONE,   &itemize_changes, 0, 0, 0 },
  {"bwlimit",          0,  POPT_ARG_INT,    &bwlimit, 0, 0, 0 },
  {"backup-dir",       0,  POPT_ARG_STRING, &backup_dir, 0, 0, 0 },
  {"hard-links",      'H', POPT_ARG_NONE,   &preserve_hard_links, 0, 0, 0 },
  {"read-batch",       0,  POPT_ARG_STRING, &batch_name, OPT_READ_BATCH, 0, 0 },
  {"write-batch",      0,  POPT_ARG_STRING, &batch_name, OPT_WRITE_BATCH, 0, 0 },
  {"only-write-batch", 0,  POPT_ARG_STRING, &batch_name, OPT_ONLY_WRITE_BATCH, 0, 0 },
  {"files-from",       0,  POPT_ARG_STRING, &files_from, 0, 0, 0 },
  {"from0",           '0', POPT_ARG_NONE,   &eol_nulls, 0, 0, 0},
  {"no-implied-dirs",  0,  POPT_ARG_VAL,    &implied_dirs, 0, 0, 0 },
  {"protocol",         0,  POPT_ARG_INT,    &protocol_version, 0, 0, 0 },
  {"checksum-seed",    0,  POPT_ARG_INT,    &checksum_seed, 0, 0, 0 },
#ifdef INET6
  {"ipv4",            '4', POPT_ARG_VAL,    &default_af_hint, AF_INET, 0, 0 },
  {"ipv6",            '6', POPT_ARG_VAL,    &default_af_hint, AF_INET6, 0, 0 },
#endif
  /* All these options switch us into daemon-mode option-parsing. */
  {"config",           0,  POPT_ARG_STRING, 0, OPT_DAEMON, 0, 0 },
  {"daemon",           0,  POPT_ARG_NONE,   0, OPT_DAEMON, 0, 0 },
  {"detach",           0,  POPT_ARG_NONE,   0, OPT_DAEMON, 0, 0 },
  {"no-detach",        0,  POPT_ARG_NONE,   0, OPT_DAEMON, 0, 0 },
  {0,0,0,0, 0, 0, 0}
};

static void daemon_usage(enum logcode F)
{
  print_rsync_version(F);

  rprintf(F,"\nUsage: rsync --daemon [OPTION]...\n");
  rprintf(F,"     --address=ADDRESS       bind to the specified address\n");
  rprintf(F,"     --bwlimit=KBPS          limit I/O bandwidth; KBytes per second\n");
  rprintf(F,"     --config=FILE           specify alternate rsyncd.conf file\n");
  rprintf(F,"     --no-detach             do not detach from the parent\n");
  rprintf(F,"     --port=PORT             listen on alternate port number\n");
  rprintf(F," -v, --verbose               increase verbosity\n");
#ifdef INET6
  rprintf(F," -4, --ipv4                  prefer IPv4\n");
  rprintf(F," -6, --ipv6                  prefer IPv6\n");
#endif
  rprintf(F," -h, --help                  show this help screen\n");

  rprintf(F,"\nIf you were not trying to invoke rsync as a daemon, avoid using any of the\n");
  rprintf(F,"daemon-specific rsync options.  See also the rsyncd.conf(5) man page.\n");
}

static struct poptOption long_daemon_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"address",          0,  POPT_ARG_STRING, &bind_address, 0, 0, 0 },
  {"bwlimit",          0,  POPT_ARG_INT,    &daemon_bwlimit, 0, 0, 0 },
  {"config",           0,  POPT_ARG_STRING, &config_file, 0, 0, 0 },
  {"daemon",           0,  POPT_ARG_NONE,   &daemon_opt, 0, 0, 0 },
#ifdef INET6
  {"ipv4",            '4', POPT_ARG_VAL,    &default_af_hint, AF_INET, 0, 0 },
  {"ipv6",            '6', POPT_ARG_VAL,    &default_af_hint, AF_INET6, 0, 0 },
#endif
  {"detach",           0,  POPT_ARG_VAL,    &no_detach, 0, 0, 0 },
  {"no-detach",        0,  POPT_ARG_VAL,    &no_detach, 1, 0, 0 },
  {"port",             0,  POPT_ARG_INT,    &rsync_port, 0, 0, 0 },
  {"protocol",         0,  POPT_ARG_INT,    &protocol_version, 0, 0, 0 },
  {"server",           0,  POPT_ARG_NONE,   &am_server, 0, 0, 0 },
  {"verbose",         'v', POPT_ARG_NONE,   0, 'v', 0, 0 },
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
		strcpy(err_buf, "Error parsing options: "
		    "option may be supported on client but not on server?\n");
	}

	rprintf(FERROR, RSYNC_NAME ": %s", err_buf);
}


/**
 * Tweak the option table to disable all options that the rsyncd.conf
 * file has told us to refuse.
 **/
static void set_refuse_options(char *bp)
{
	struct poptOption *op;
	char *cp, shortname[2];
	int is_wild, found_match;

	shortname[1] = '\0';

	while (1) {
		while (*bp == ' ') bp++;
		if (!*bp)
			break;
		if ((cp = strchr(bp, ' ')) != NULL)
			*cp= '\0';
		is_wild = strpbrk(bp, "*?[") != NULL;
		found_match = 0;
		for (op = long_options; ; op++) {
			*shortname = op->shortName;
			if (!op->longName && !*shortname)
				break;
			if ((op->longName && wildmatch(bp, op->longName))
			    || (*shortname && wildmatch(bp, shortname))) {
				if (op->argInfo == POPT_ARG_VAL)
					op->argInfo = POPT_ARG_NONE;
				op->val = (op - long_options) + OPT_REFUSED_BASE;
				found_match = 1;
				/* These flags are set to let us easily check
				 * an implied option later in the code. */
				switch (*shortname) {
				case 'r': case 'd': case 'l': case 'p':
				case 't': case 'g': case 'o': case 'D':
					refused_archive_part = op->val;
					break;
				case '\0':
					if (wildmatch("delete", op->longName))
						refused_delete = op->val;
					else if (wildmatch("delete-before", op->longName))
						refused_delete_before = op->val;
					else if (wildmatch("partial", op->longName))
						refused_partial = op->val;
					else if (wildmatch("progress", op->longName))
						refused_progress = op->val;
					break;
				}
				if (!is_wild)
					break;
			}
		}
		if (!found_match) {
			rprintf(FLOG, "No match for refuse-options string \"%s\"\n",
				bp);
		}
		if (!cp)
			break;
		*cp = ' ';
		bp = cp + 1;
	}

	for (op = long_options; ; op++) {
		*shortname = op->shortName;
		if (!op->longName && !*shortname)
			break;
		if (op->val == OPT_DAEMON) {
			if (op->argInfo == POPT_ARG_VAL)
				op->argInfo = POPT_ARG_NONE;
			op->val = (op - long_options) + OPT_REFUSED_BASE;
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


static OFF_T parse_size_arg(const char *size_arg)
{
	const char *arg;
	OFF_T size;

	for (arg = size_arg; isdigit(*(uchar*)arg); arg++) {}
	if (*arg == '.')
		for (arg++; isdigit(*(uchar*)arg); arg++) {}
	switch (*arg) {
	case 'k': case 'K':
		size = atof(size_arg) * 1024;
		break;
	case 'm': case 'M':
		size = atof(size_arg) * 1024*1024;
		break;
	case 'g': case 'G':
		size = atof(size_arg) * 1024*1024*1024;
		break;
	case '\0':
		size = atof(size_arg);
		break;
	default:
		size = 0;
		break;
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


/**
 * Process command line arguments.  Called on both local and remote.
 *
 * @retval 1 if all options are OK; with globals set to appropriate
 * values
 *
 * @retval 0 on error, with err_buf containing an explanation
 **/
int parse_arguments(int *argc, const char ***argv, int frommain)
{
	int opt;
	char *ref = lp_refuse_options(module_id);
	const char *arg;
	poptContext pc;

	if (ref && *ref)
		set_refuse_options(ref);

	/* TODO: Call poptReadDefaultConfig; handle errors. */

	/* The context leaks in case of an error, but if there's a
	 * problem we always exit anyhow. */
	pc = poptGetContext(RSYNC_NAME, *argc, *argv, long_options, 0);
	poptReadDefaultConfig(pc, 0);

	while ((opt = poptGetNextOpt(pc)) != -1) {
		/* most options are handled automatically by popt;
		 * only special cases are returned and listed here. */

		switch (opt) {
		case OPT_VERSION:
			print_rsync_version(FINFO);
			exit_cleanup(0);

		case OPT_DAEMON:
			if (am_daemon) {
				strcpy(err_buf, "Attempt to hack rsync thwarted!\n");
				return 0;
			}
			poptFreeContext(pc);
			pc = poptGetContext(RSYNC_NAME, *argc, *argv,
					    long_daemon_options, 0);
			while ((opt = poptGetNextOpt(pc)) != -1) {
				switch (opt) {
				case 'h':
					daemon_usage(FINFO);
					exit_cleanup(0);

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
			if (!daemon_opt) {
				rprintf(FERROR, "Daemon option(s) used without --daemon.\n");
			    daemon_error:
				rprintf(FERROR,
				    "(Type \"rsync --daemon --help\" for assistance with daemon mode.)\n");
				exit_cleanup(RERR_SYNTAX);
			}
			*argv = poptGetArgs(pc);
			*argc = count_args(*argv);
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
			parse_rule(&filter_list, poptGetOptArg(pc), 0, 0);
			break;

		case OPT_EXCLUDE:
			parse_rule(&filter_list, poptGetOptArg(pc),
				   0, XFLG_OLD_PREFIXES);
			break;

		case OPT_INCLUDE:
			parse_rule(&filter_list, poptGetOptArg(pc),
				   MATCHFLG_INCLUDE, XFLG_OLD_PREFIXES);
			break;

		case OPT_EXCLUDE_FROM:
		case OPT_INCLUDE_FROM:
			arg = poptGetOptArg(pc);
			if (sanitize_paths)
				arg = sanitize_path(NULL, arg, NULL, 0);
			if (server_filter_list.head) {
				char *cp = (char *)arg;
				if (!*cp)
					goto options_rejected;
				clean_fname(cp, 1);
				if (check_filter(&server_filter_list, cp, 0) < 0)
					goto options_rejected;
			}
			parse_filter_file(&filter_list, arg,
				opt == OPT_INCLUDE_FROM ? MATCHFLG_INCLUDE : 0,
				XFLG_FATAL_ERRORS | XFLG_OLD_PREFIXES);
			break;

		case 'h':
			usage(FINFO);
			exit_cleanup(0);

		case 'v':
			verbose++;
			break;

		case 'q':
			if (frommain)
				quiet++;
			break;

		case OPT_SENDER:
			if (!am_server) {
				usage(FERROR);
				exit_cleanup(RERR_SYNTAX);
			}
			am_sender = 1;
			break;

		case 'F':
			switch (++F_option_cnt) {
			case 1:
				parse_rule(&filter_list,": /.rsync-filter",0,0);
				break;
			case 2:
				parse_rule(&filter_list,"- .rsync-filter",0,0);
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

		case OPT_MAX_SIZE:
			if ((max_size = parse_size_arg(max_size_arg)) <= 0) {
				snprintf(err_buf, sizeof err_buf,
					"--max-size value is invalid: %s\n",
					max_size_arg);
				return 0;
			}
			break;

		case OPT_LINK_DEST:
#ifdef HAVE_LINK
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
			arg = poptGetOptArg(pc);
			if (sanitize_paths)
				arg = sanitize_path(NULL, arg, NULL, 0);
			basis_dir[basis_dir_cnt++] = (char *)arg;
			break;

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
	}
	if (read_batch && files_from) {
		snprintf(err_buf, sizeof err_buf,
			"--read-batch cannot be used with --files-from\n");
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

	if (compare_dest + copy_dest + link_dest > 1) {
		snprintf(err_buf, sizeof err_buf,
			"You may not mix --compare-dest, --copy-dest, and --link-dest.\n");
		return 0;
	}

	if (archive_mode) {
		if (refused_archive_part) {
			create_refuse_error(refused_archive_part);
			return 0;
		}
		if (!files_from)
			recurse = 1;
#ifdef SUPPORT_LINKS
		preserve_links = 1;
#endif
		preserve_perms = 1;
		preserve_times = 1;
		preserve_gid = 1;
		preserve_uid = 1;
		preserve_devices = 1;
	}

	if (recurse || list_only || files_from)
		xfer_dirs |= 1;

	if (relative_paths < 0)
		relative_paths = files_from? 1 : 0;

	if (!!delete_before + delete_during + delete_after > 1) {
		snprintf(err_buf, sizeof err_buf,
			"You may not combine multiple --delete-WHEN options.\n");
		return 0;
	}
	if (!recurse) {
		delete_before = delete_during = delete_after = 0;
		delete_mode = delete_excluded = 0;
	} else if (delete_before || delete_during || delete_after)
		delete_mode = 1;
	else if (delete_mode || delete_excluded) {
		if (refused_delete_before) {
			create_refuse_error(refused_delete_before);
			return 0;
		}
		delete_mode = delete_before = 1;
	}

	if (delete_mode && refused_delete) {
		create_refuse_error(refused_delete);
		return 0;
	}

	if (remove_sent_files) {
		/* We only want to infer this refusal of --remove-sent-files
		 * via the refusal of "delete", not any of the "delete-FOO"
		 * options. */
		if (refused_delete && am_sender) {
			create_refuse_error(refused_delete);
			return 0;
		}
		need_messages_from_generator = 1;
	}

	*argv = poptGetArgs(pc);
	*argc = count_args(*argv);

	if (sanitize_paths) {
		int i;
		for (i = *argc; i-- > 0; )
			(*argv)[i] = sanitize_path(NULL, (*argv)[i], "", 0);
		if (tmpdir)
			tmpdir = sanitize_path(NULL, tmpdir, NULL, 0);
		if (partial_dir)
			partial_dir = sanitize_path(NULL, partial_dir, NULL, 0);
		if (backup_dir)
			backup_dir = sanitize_path(NULL, backup_dir, NULL, 0);
	}
	if (server_filter_list.head && !am_sender) {
		struct filter_list_struct *elp = &server_filter_list;
		int i;
		if (tmpdir) {
			if (!*tmpdir)
				goto options_rejected;
			clean_fname(tmpdir, 1);
			if (check_filter(elp, tmpdir, 1) < 0)
				goto options_rejected;
		}
		if (partial_dir && *partial_dir) {
			clean_fname(partial_dir, 1);
			if (check_filter(elp, partial_dir, 1) < 0)
				goto options_rejected;
		}
		for (i = 0; i < basis_dir_cnt; i++) {
			if (!*basis_dir[i])
				goto options_rejected;
			clean_fname(basis_dir[i], 1);
			if (check_filter(elp, basis_dir[i], 1) < 0)
				goto options_rejected;
		}
		if (backup_dir) {
			if (!*backup_dir)
				goto options_rejected;
			clean_fname(backup_dir, 1);
			if (check_filter(elp, backup_dir, 1) < 0) {
			    options_rejected:
				snprintf(err_buf, sizeof err_buf,
				    "Your options have been rejected by the server.\n");
				return 0;
			}
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
		backup_dir_len = strlcpy(backup_dir_buf, backup_dir, sizeof backup_dir_buf);
		backup_dir_remainder = sizeof backup_dir_buf - backup_dir_len;
		if (backup_dir_remainder < 32) {
			snprintf(err_buf, sizeof err_buf,
				"the --backup-dir path is WAY too long.\n");
			return 0;
		}
		if (backup_dir_buf[backup_dir_len - 1] != '/') {
			backup_dir_buf[backup_dir_len++] = '/';
			backup_dir_buf[backup_dir_len] = '\0';
		}
		if (verbose > 1 && !am_sender) {
			rprintf(FINFO, "backup_dir is %s\n",
				safe_fname(backup_dir_buf));
		}
	} else if (!backup_suffix_len && (!am_server || !am_sender)) {
		snprintf(err_buf, sizeof err_buf,
			"--suffix cannot be a null string without --backup-dir\n");
		return 0;
	}
	if (make_backups && !backup_dir)
		omit_dir_times = 1;

	if (log_format) {
		if (log_format_has(log_format, 'i'))
			log_format_has_i = 1;
		if (!log_format_has(log_format, 'b')
		 && !log_format_has(log_format, 'c'))
			log_before_transfer = !am_server;
	} else if (itemize_changes) {
		log_format = "%i %n%L";
		log_format_has_i = 1;
		log_before_transfer = !am_server;
	}

	if ((do_progress || dry_run) && !verbose && !log_before_transfer
	    && !am_server)
		verbose = 1;

	if (dry_run)
		do_xfers = 0;

	set_io_timeout(io_timeout);

	if (verbose && !log_format) {
		log_format = "%n%L";
		log_before_transfer = !am_server;
	}
	if (log_format_has_i || log_format_has(log_format, 'o'))
		log_format_has_o_or_i = 1;

	if (daemon_bwlimit && (!bwlimit || bwlimit > daemon_bwlimit))
		bwlimit = daemon_bwlimit;
	if (bwlimit) {
		bwlimit_writemax = (size_t)bwlimit * 128;
		if (bwlimit_writemax < 512)
			bwlimit_writemax = 512;
	}

	if (delay_updates && !partial_dir)
		partial_dir = partialdir_for_delayupdate;

	if (inplace) {
#ifdef HAVE_FTRUNCATE
		if (partial_dir) {
			snprintf(err_buf, sizeof err_buf,
				 "--inplace cannot be used with --%s\n",
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
			 "--inplace is not supported on this %s\n",
			 am_server ? "server" : "client");
		return 0;
#endif
	} else {
		if (keep_partial && !partial_dir) {
			if ((arg = getenv("RSYNC_PARTIAL_DIR")) != NULL && *arg)
				partial_dir = strdup(arg);
		}
		if (partial_dir) {
			if (*partial_dir)
				clean_fname(partial_dir, 1);
			if (!*partial_dir || strcmp(partial_dir, ".") == 0)
				partial_dir = NULL;
			else if (*partial_dir != '/') {
				parse_rule(&filter_list, partial_dir,
				    MATCHFLG_NO_PREFIXES|MATCHFLG_DIRECTORY, 0);
			}
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
		if (*argc > 2 || (!am_daemon && *argc == 1)) {
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
				files_from = sanitize_path(NULL, files_from, NULL, 0);
			if (server_filter_list.head) {
				if (!*files_from)
					goto options_rejected;
				clean_fname(files_from, 1);
				if (check_filter(&server_filter_list, files_from, 0) < 0)
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
}


/**
 * Construct a filtered list of options to pass through from the
 * client to the server.
 *
 * This involves setting options that will tell the server how to
 * behave, and also filtering out options that are processed only
 * locally.
 **/
void server_options(char **args,int *argc)
{
	static char argstr[64];
	int ac = *argc;
	char *arg;

	int i, x;

	if (blocking_io == -1)
		blocking_io = 0;

	args[ac++] = "--server";

	if (daemon_over_rsh) {
		args[ac++] = "--daemon";
		*argc = ac;
		/* if we're passing --daemon, we're done */
		return;
	}

	if (!am_sender)
		args[ac++] = "--sender";

	x = 1;
	argstr[0] = '-';
	for (i = 0; i < verbose; i++)
		argstr[x++] = 'v';

	/* the -q option is intentionally left out */
	if (make_backups)
		argstr[x++] = 'b';
	if (update_only)
		argstr[x++] = 'u';
	if (!do_xfers) /* NOT "dry_run"! */
		argstr[x++] = 'n';
	if (preserve_links)
		argstr[x++] = 'l';
	if (copy_links)
		argstr[x++] = 'L';
	if (xfer_dirs > 1)
		argstr[x++] = 'd';
	if (keep_dirlinks && am_sender)
		argstr[x++] = 'K';

	if (whole_file > 0)
		argstr[x++] = 'W';
	/* We don't need to send --no-whole-file, because it's the
	 * default for remote transfers, and in any case old versions
	 * of rsync will not understand it. */

	if (preserve_hard_links)
		argstr[x++] = 'H';
	if (preserve_uid)
		argstr[x++] = 'o';
	if (preserve_gid)
		argstr[x++] = 'g';
	if (preserve_devices)
		argstr[x++] = 'D';
	if (preserve_times)
		argstr[x++] = 't';
	if (omit_dir_times == 2 && am_sender)
		argstr[x++] = 'O';
	if (preserve_perms)
		argstr[x++] = 'p';
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
	if (one_file_system)
		argstr[x++] = 'x';
	if (sparse_files)
		argstr[x++] = 'S';
	if (do_compression)
		argstr[x++] = 'z';

	/* This is a complete hack - blame Rusty.  FIXME!
	 * This hack is only needed for older rsync versions that
	 * don't understand the --list-only option. */
	if (list_only == 1 && !recurse)
		argstr[x++] = 'r';

	argstr[x] = 0;

	if (x != 1)
		args[ac++] = argstr;

	if (list_only > 1)
		args[ac++] = "--list-only";

	/* The server side doesn't use our log-format, but in certain
	 * circumstances they need to know a little about the option. */
	if (log_format && am_sender) {
		if (log_format_has_i)
			args[ac++] = "--log-format=%i";
		else if (log_format_has_o_or_i)
			args[ac++] = "--log-format=%o";
		else if (!verbose)
			args[ac++] = "--log-format=X";
	}

	if (block_size) {
		if (asprintf(&arg, "-B%lu", block_size) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (max_delete && am_sender) {
		if (asprintf(&arg, "--max-delete=%d", max_delete) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (max_size && am_sender) {
		args[ac++] = "--max-size";
		args[ac++] = max_size_arg;
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

	if (am_sender) {
		if (delete_excluded)
			args[ac++] = "--delete-excluded";
		else if (delete_before == 1 || delete_after)
			args[ac++] = "--delete";
		if (delete_before > 1)
			args[ac++] = "--delete-before";
		if (delete_during)
			args[ac++] = "--delete-during";
		if (delete_after)
			args[ac++] = "--delete-after";
		if (force_delete)
			args[ac++] = "--force";
		if (write_batch < 0)
			args[ac++] = "--only-write-batch=X";
	}

	if (size_only)
		args[ac++] = "--size-only";

	if (modify_window_set) {
		if (asprintf(&arg, "--modify-window=%d", modify_window) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (checksum_seed) {
		if (asprintf(&arg, "--checksum-seed=%d", checksum_seed) < 0)
			goto oom;
		args[ac++] = arg;
	}

	if (partial_dir && am_sender) {
		if (partial_dir != partialdir_for_delayupdate) {
			args[ac++] = "--partial-dir";
			args[ac++] = partial_dir;
		}
		if (delay_updates)
			args[ac++] = "--delay-updates";
	} else if (keep_partial)
		args[ac++] = "--partial";

	if (ignore_errors)
		args[ac++] = "--ignore-errors";

	if (copy_unsafe_links)
		args[ac++] = "--copy-unsafe-links";

	if (safe_symlinks)
		args[ac++] = "--safe-links";

	if (numeric_ids)
		args[ac++] = "--numeric-ids";

	if (only_existing && am_sender)
		args[ac++] = "--existing";

	if (opt_ignore_existing && am_sender)
		args[ac++] = "--ignore-existing";

	if (inplace)
		args[ac++] = "--inplace";

	if (tmpdir) {
		args[ac++] = "--temp-dir";
		args[ac++] = tmpdir;
	}

	if (basis_dir[0] && am_sender) {
		/* the server only needs this option if it is not the sender,
		 *   and it may be an older version that doesn't know this
		 *   option, so don't send it if client is the sender.
		 */
		int i;
		for (i = 0; i < basis_dir_cnt; i++) {
			args[ac++] = dest_option;
			args[ac++] = basis_dir[i];
		}
	}

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
	if (!implied_dirs && !am_sender)
		args[ac++] = "--no-implied-dirs";

	if (fuzzy_basis && am_sender)
		args[ac++] = "--fuzzy";

	if (remove_sent_files)
		args[ac++] = "--remove-sent-files";

	*argc = ac;
	return;

    oom:
	out_of_memory("server_options");
}

/* Look for a HOST specfication of the form "HOST:PATH", "HOST::PATH", or
 * "rsync://HOST:PORT/PATH".  If found, *host_ptr will be set to some allocated
 * memory with the HOST.  If a daemon-accessing spec was specified, the value
 * of *port_ptr will contain a non-0 port number, otherwise it will be set to
 * 0.  The return value is a pointer to the PATH.  Note that the HOST spec can
 * be an IPv6 literal address enclosed in '[' and ']' (such as "[::1]" or
 * "[::ffff:127.0.0.1]") which is returned without the '[' and ']'. */
char *check_for_hostspec(char *s, char **host_ptr, int *port_ptr)
{
	char *p;
	int not_host;

	if (port_ptr && strncasecmp(URL_PREFIX, s, strlen(URL_PREFIX)) == 0) {
		char *path;
		int hostlen;
		s += strlen(URL_PREFIX);
		if ((p = strchr(s, '/')) != NULL) {
			hostlen = p - s;
			path = p + 1;
		} else {
			hostlen = strlen(s);
			path = "";
		}
		if (*s == '[' && (p = strchr(s, ']')) != NULL) {
			s++;
			hostlen = p - s;
			if (p[1] == ':')
				*port_ptr = atoi(p+2);
		} else {
			if ((p = strchr(s, ':')) != NULL) {
				hostlen = p - s;
				*port_ptr = atoi(p+1);
			}
		}
		if (!*port_ptr)
			*port_ptr = RSYNC_PORT;
		*host_ptr = new_array(char, hostlen + 1);
		strlcpy(*host_ptr, s, hostlen + 1);
		return path;
	}

	if (*s == '[' && (p = strchr(s, ']')) != NULL && p[1] == ':') {
		s++;
		*p = '\0';
		not_host = strchr(s, '/') || !strchr(s, ':');
		*p = ']';
		if (not_host)
			return NULL;
		p++;
	} else {
		if (!(p = strchr(s, ':')))
			return NULL;
		*p = '\0';
		not_host = strchr(s, '/') != NULL;
		*p = ':';
		if (not_host)
			return NULL;
	}

	*host_ptr = new_array(char, p - s + 1);
	strlcpy(*host_ptr, s, p - s + 1);

	if (p[1] == ':') {
		if (port_ptr && !*port_ptr)
			*port_ptr = RSYNC_PORT;
		return p + 2;
	}
	if (port_ptr)
		*port_ptr = 0;

	return p + 1;
}
