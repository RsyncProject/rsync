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

int make_backups = 0;

/**
 * If True, send the whole file as literal data rather than trying to
 * create an incremental diff.
 *
 * If both are 0, then look at whether we're local or remote and go by
 * that.
 *
 * @sa disable_deltas_p()
 **/
int whole_file = 0;
int no_whole_file = 0;

int copy_links = 0;
int preserve_links = 0;
int preserve_hard_links = 0;
int preserve_perms = 0;
int preserve_devices = 0;
int preserve_uid = 0;
int preserve_gid = 0;
int preserve_times = 0;
int update_only = 0;
int cvs_exclude = 0;
int dry_run=0;
int local_server=0;
int ignore_times=0;
int delete_mode=0;
int delete_excluded=0;
int one_file_system=0;
int remote_version=0;
int sparse_files=0;
int do_compression=0;
int am_root=0;
int orig_umask=0;
int relative_paths=0;
int numeric_ids = 0;
int force_delete = 0;
int io_timeout = 0;
int io_error = 0;
int read_only = 0;
int module_id = -1;
int am_server = 0;
int am_sender = 0;
int recurse = 0;
int am_daemon = 0;
int daemon_over_rsh = 0;
int do_stats=0;
int do_progress=0;
int keep_partial=0;
int safe_symlinks=0;
int copy_unsafe_links=0;
int block_size=BLOCK_SIZE;
int size_only=0;
int bwlimit=0;
int delete_after=0;
int only_existing=0;
int opt_ignore_existing=0;
int max_delete=0;
int ignore_errors=0;
int modify_window=0;
int blocking_io=-1;


/** Network address family. **/
#ifdef INET6
int default_af_hint = 0;	/* Any protocol */
#else
int default_af_hint = AF_INET;	/* Must use IPv4 */
#endif

/** Do not go into the background when run as --daemon.  Good
 * for debugging and required for running as a service on W32,
 * or under Unix process-monitors. **/
int no_detach = 0;

int write_batch = 0;
int read_batch = 0;
int suffix_specified = 0;

char *backup_suffix = BACKUP_SUFFIX;
char *tmpdir = NULL;
char *compare_dest = NULL;
char *config_file = NULL;
char *shell_cmd = NULL;
char *log_format = NULL;
char *password_file = NULL;
char *rsync_path = RSYNC_PATH;
char *backup_dir = NULL;
int rsync_port = RSYNC_PORT;
int link_dest = 0;

int verbose = 0;
int quiet = 0;
int always_checksum = 0;
int list_only = 0;

char *batch_prefix = NULL;

static int modify_window_set;

/** Local address to bind.  As a character string because it's
 * interpreted by the IPv6 layer: should be a numeric IP4 or ip6
 * address, or a hostname. **/
char *bind_address;


static void print_rsync_version(enum logcode f)
{
        char const *got_socketpair = "no ";
        char const *hardlinks = "no ";
        char const *links = "no ";
	char const *ipv6 = "no ";
	STRUCT_STAT *dumstat;

#ifdef HAVE_SOCKETPAIR
        got_socketpair = "";
#endif

#if SUPPORT_HARD_LINKS
        hardlinks = "";
#endif

#if SUPPORT_LINKS
        links = "";
#endif

#if INET6
	ipv6 = "";
#endif       

        rprintf(f, "%s  version %s  protocol version %d\n",
                RSYNC_NAME, RSYNC_VERSION, PROTOCOL_VERSION);
        rprintf(f,
                "Copyright (C) 1996-2002 by Andrew Tridgell and others\n");
	rprintf(f, "<http://rsync.samba.org/>\n");
        rprintf(f, "Capabilities: %d-bit files, %ssocketpairs, "
                "%shard links, %ssymlinks, batchfiles, \n",
                (int) (sizeof(OFF_T) * 8),
                got_socketpair, hardlinks, links);

	/* Note that this field may not have type ino_t.  It depends
	 * on the complicated interaction between largefile feature
	 * macros. */
	rprintf(f, "              %sIPv6, %d-bit system inums, %d-bit internal inums\n",
		ipv6, 
		(int) (sizeof(dumstat->st_ino) * 8),
		(int) (sizeof(INO64_T) * 8));
#ifdef MAINTAINER_MODE
	rprintf(f, "              panic action: \"%s\"\n",
		get_panic_action());
#endif

#ifdef NO_INT64
        rprintf(f, "WARNING: no 64-bit integers on this platform!\n");
#endif

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
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST:SRC DEST\n");
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
  rprintf(F," -q, --quiet                 decrease verbosity\n");
  rprintf(F," -c, --checksum              always checksum\n");
  rprintf(F," -a, --archive               archive mode, equivalent to -rlptgoD\n");
  rprintf(F," -r, --recursive             recurse into directories\n");
  rprintf(F," -R, --relative              use relative path names\n");
  rprintf(F," -b, --backup                make backups (default %s suffix)\n",BACKUP_SUFFIX);
  rprintf(F,"     --backup-dir            make backups into this directory\n");
  rprintf(F,"     --suffix=SUFFIX         override backup suffix\n");  
  rprintf(F," -u, --update                update only (don't overwrite newer files)\n");
  rprintf(F," -l, --links                 copy symlinks as symlinks\n");
  rprintf(F," -L, --copy-links            copy the referent of symlinks\n");
  rprintf(F,"     --copy-unsafe-links     copy links outside the source tree\n");
  rprintf(F,"     --safe-links            ignore links outside the destination tree\n");
  rprintf(F," -H, --hard-links            preserve hard links\n");
  rprintf(F," -p, --perms                 preserve permissions\n");
  rprintf(F," -o, --owner                 preserve owner (root only)\n");
  rprintf(F," -g, --group                 preserve group\n");
  rprintf(F," -D, --devices               preserve devices (root only)\n");
  rprintf(F," -t, --times                 preserve times\n");  
  rprintf(F," -S, --sparse                handle sparse files efficiently\n");
  rprintf(F," -n, --dry-run               show what would have been transferred\n");
  rprintf(F," -W, --whole-file            copy whole files, no incremental checks\n");
  rprintf(F,"     --no-whole-file         turn off --whole-file\n");
  rprintf(F," -x, --one-file-system       don't cross filesystem boundaries\n");
  rprintf(F," -B, --block-size=SIZE       checksum blocking size (default %d)\n",BLOCK_SIZE);  
  rprintf(F," -e, --rsh=COMMAND           specify the remote shell\n");
  rprintf(F,"     --rsync-path=PATH       specify path to rsync on the remote machine\n");
  rprintf(F," -C, --cvs-exclude           auto ignore files in the same way CVS does\n");
  rprintf(F,"     --existing              only update files that already exist\n");
  rprintf(F,"     --ignore-existing       ignore files that already exist on the receiving side\n");
  rprintf(F,"     --delete                delete files that don't exist on the sending side\n");
  rprintf(F,"     --delete-excluded       also delete excluded files on the receiving side\n");
  rprintf(F,"     --delete-after          delete after transferring, not before\n");
  rprintf(F,"     --ignore-errors         delete even if there are IO errors\n");
  rprintf(F,"     --max-delete=NUM        don't delete more than NUM files\n");
  rprintf(F,"     --partial               keep partially transferred files\n");
  rprintf(F,"     --force                 force deletion of directories even if not empty\n");
  rprintf(F,"     --numeric-ids           don't map uid/gid values by user/group name\n");
  rprintf(F,"     --timeout=TIME          set IO timeout in seconds\n");
  rprintf(F," -I, --ignore-times          don't exclude files that match length and time\n");
  rprintf(F,"     --size-only             only use file size when determining if a file should be transferred\n");
  rprintf(F,"     --modify-window=NUM     Timestamp window (seconds) for file match (default=%d)\n",modify_window);
  rprintf(F," -T  --temp-dir=DIR          create temporary files in directory DIR\n");
  rprintf(F,"     --compare-dest=DIR      also compare destination files relative to DIR\n");
  rprintf(F," -P                          equivalent to --partial --progress\n");
  rprintf(F," -z, --compress              compress file data\n");
  rprintf(F,"     --exclude=PATTERN       exclude files matching PATTERN\n");
  rprintf(F,"     --exclude-from=FILE     exclude patterns listed in FILE\n");
  rprintf(F,"     --include=PATTERN       don't exclude files matching PATTERN\n");
  rprintf(F,"     --include-from=FILE     don't exclude patterns listed in FILE\n");
  rprintf(F,"     --version               print version number\n");  
  rprintf(F,"     --daemon                run as a rsync daemon\n");  
  rprintf(F,"     --no-detach             do not detach from the parent\n");  
  rprintf(F,"     --address=ADDRESS       bind to the specified address\n");  
  rprintf(F,"     --config=FILE           specify alternate rsyncd.conf file\n");  
  rprintf(F,"     --port=PORT             specify alternate rsyncd port number\n");
  rprintf(F,"     --blocking-io           use blocking IO for the remote shell\n");  
  rprintf(F,"     --no-blocking-io        turn off --blocking-io\n");  
  rprintf(F,"     --stats                 give some file transfer stats\n");  
  rprintf(F,"     --progress              show progress during transfer\n");  
  rprintf(F,"     --log-format=FORMAT     log file transfers using specified format\n");  
  rprintf(F,"     --password-file=FILE    get password from FILE\n");
  rprintf(F,"     --bwlimit=KBPS          limit I/O bandwidth, KBytes per second\n");
  rprintf(F,"     --write-batch=PREFIX    write batch fileset starting with PREFIX\n");
  rprintf(F,"     --read-batch=PREFIX     read batch fileset starting with PREFIX\n");
  rprintf(F," -h, --help                  show this help screen\n");
#ifdef INET6
  rprintf(F," -4                          prefer IPv4\n");
  rprintf(F," -6                          prefer IPv6\n");
#endif

  rprintf(F,"\n");

  rprintf(F,"\nPlease see the rsync(1) and rsyncd.conf(5) man pages for full documentation\n");
  rprintf(F,"See http://rsync.samba.org/ for updates, bug reports, and answers\n");
}

enum {OPT_VERSION = 1000, OPT_SUFFIX, OPT_SENDER, OPT_SERVER, OPT_EXCLUDE,
      OPT_EXCLUDE_FROM, OPT_DELETE, OPT_DELETE_EXCLUDED, OPT_NUMERIC_IDS,
      OPT_RSYNC_PATH, OPT_FORCE, OPT_TIMEOUT, OPT_DAEMON, OPT_CONFIG, OPT_PORT,
      OPT_INCLUDE, OPT_INCLUDE_FROM, OPT_STATS, OPT_PARTIAL, OPT_PROGRESS,
      OPT_COPY_UNSAFE_LINKS, OPT_SAFE_LINKS, OPT_COMPARE_DEST, OPT_LINK_DEST,
      OPT_LOG_FORMAT, OPT_PASSWORD_FILE, OPT_SIZE_ONLY, OPT_ADDRESS,
      OPT_DELETE_AFTER, OPT_EXISTING, OPT_MAX_DELETE, OPT_BACKUP_DIR, 
      OPT_IGNORE_ERRORS, OPT_BWLIMIT, OPT_BLOCKING_IO,
      OPT_NO_BLOCKING_IO, OPT_WHOLE_FILE, OPT_NO_WHOLE_FILE,
      OPT_MODIFY_WINDOW, OPT_READ_BATCH, OPT_WRITE_BATCH, OPT_IGNORE_EXISTING};

static struct poptOption long_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"version",          0,  POPT_ARG_NONE,   0,             OPT_VERSION, 0, 0},
  {"suffix",           0,  POPT_ARG_STRING, &backup_suffix,	OPT_SUFFIX, 0, 0 },
  {"rsync-path",       0,  POPT_ARG_STRING, &rsync_path,	0, 0, 0 },
  {"password-file",    0,  POPT_ARG_STRING, &password_file,	0, 0, 0 },
  {"ignore-times",    'I', POPT_ARG_NONE,   &ignore_times , 0, 0, 0 },
  {"size-only",        0,  POPT_ARG_NONE,   &size_only , 0, 0, 0 },
  {"modify-window",    0,  POPT_ARG_INT,    &modify_window, OPT_MODIFY_WINDOW, 0, 0 },
  {"one-file-system", 'x', POPT_ARG_NONE,   &one_file_system , 0, 0, 0 },
  {"delete",           0,  POPT_ARG_NONE,   &delete_mode , 0, 0, 0 },
  {"existing",         0,  POPT_ARG_NONE,   &only_existing , 0, 0, 0 },
  {"ignore-existing",  0,  POPT_ARG_NONE,   &opt_ignore_existing , 0, 0, 0 },
  {"delete-after",     0,  POPT_ARG_NONE,   0,              OPT_DELETE_AFTER, 0, 0 },
  {"delete-excluded",  0,  POPT_ARG_NONE,   0,              OPT_DELETE_EXCLUDED, 0, 0 },
  {"force",            0,  POPT_ARG_NONE,   &force_delete , 0, 0, 0 },
  {"numeric-ids",      0,  POPT_ARG_NONE,   &numeric_ids , 0, 0, 0 },
  {"exclude",          0,  POPT_ARG_STRING, 0,              OPT_EXCLUDE, 0, 0 },
  {"include",          0,  POPT_ARG_STRING, 0,              OPT_INCLUDE, 0, 0 },
  {"exclude-from",     0,  POPT_ARG_STRING, 0,              OPT_EXCLUDE_FROM, 0, 0 },
  {"include-from",     0,  POPT_ARG_STRING, 0,              OPT_INCLUDE_FROM, 0, 0 },
  {"safe-links",       0,  POPT_ARG_NONE,   &safe_symlinks , 0, 0, 0 },
  {"help",            'h', POPT_ARG_NONE,   0,              'h', 0, 0 },
  {"backup",          'b', POPT_ARG_NONE,   &make_backups , 0, 0, 0 },
  {"dry-run",         'n', POPT_ARG_NONE,   &dry_run , 0, 0, 0 },
  {"sparse",          'S', POPT_ARG_NONE,   &sparse_files , 0, 0, 0 },
  {"cvs-exclude",     'C', POPT_ARG_NONE,   &cvs_exclude , 0, 0, 0 },
  {"update",          'u', POPT_ARG_NONE,   &update_only , 0, 0, 0 },
  {"links",           'l', POPT_ARG_NONE,   &preserve_links , 0, 0, 0 },
  {"copy-links",      'L', POPT_ARG_NONE,   &copy_links , 0, 0, 0 },
  {"whole-file",      'W', POPT_ARG_NONE,   0,              OPT_WHOLE_FILE, 0, 0 },
  {"no-whole-file",    0,  POPT_ARG_NONE,   0,              OPT_NO_WHOLE_FILE, 0, 0 },
  {"copy-unsafe-links", 0, POPT_ARG_NONE,   &copy_unsafe_links , 0, 0, 0 },
  {"perms",           'p', POPT_ARG_NONE,   &preserve_perms , 0, 0, 0 },
  {"owner",           'o', POPT_ARG_NONE,   &preserve_uid , 0, 0, 0 },
  {"group",           'g', POPT_ARG_NONE,   &preserve_gid , 0, 0, 0 },
  {"devices",         'D', POPT_ARG_NONE,   &preserve_devices , 0, 0, 0 },
  {"times",           't', POPT_ARG_NONE,   &preserve_times , 0, 0, 0 },
  {"checksum",        'c', POPT_ARG_NONE,   &always_checksum , 0, 0, 0 },
  {"verbose",         'v', POPT_ARG_NONE,   0,               'v', 0, 0 },
  {"quiet",           'q', POPT_ARG_NONE,   0,               'q', 0, 0 },
  {"archive",         'a', POPT_ARG_NONE,   0,               'a', 0, 0 }, 
  {"server",           0,  POPT_ARG_NONE,   &am_server , 0, 0, 0 },
  {"sender",           0,  POPT_ARG_NONE,   0,               OPT_SENDER, 0, 0 },
  {"recursive",       'r', POPT_ARG_NONE,   &recurse , 0, 0, 0 },
  {"relative",        'R', POPT_ARG_NONE,   &relative_paths , 0, 0, 0 },
  {"rsh",             'e', POPT_ARG_STRING, &shell_cmd , 0, 0, 0 },
  {"block-size",      'B', POPT_ARG_INT,    &block_size , 0, 0, 0 },
  {"max-delete",       0,  POPT_ARG_INT,    &max_delete , 0, 0, 0 },
  {"timeout",          0,  POPT_ARG_INT,    &io_timeout , 0, 0, 0 },
  {"temp-dir",        'T', POPT_ARG_STRING, &tmpdir , 0, 0, 0 },
  {"compare-dest",     0,  POPT_ARG_STRING, &compare_dest , 0, 0, 0 },
  {"link-dest",        0,  POPT_ARG_STRING, 0,               OPT_LINK_DEST, 0, 0 },
  /* TODO: Should this take an optional int giving the compression level? */
  {"compress",        'z', POPT_ARG_NONE,   &do_compression , 0, 0, 0 },
  {"daemon",           0,  POPT_ARG_NONE,   &am_daemon , 0, 0, 0 },
  {"no-detach",        0,  POPT_ARG_NONE,   &no_detach , 0, 0, 0 },
  {"stats",            0,  POPT_ARG_NONE,   &do_stats , 0, 0, 0 },
  {"progress",         0,  POPT_ARG_NONE,   &do_progress , 0, 0, 0 },
  {"partial",          0,  POPT_ARG_NONE,   &keep_partial , 0, 0, 0 },
  {"ignore-errors",    0,  POPT_ARG_NONE,   &ignore_errors , 0, 0, 0 },
  {"blocking-io",      0,  POPT_ARG_NONE,   &blocking_io , 0, 0, 0 },
  {"no-blocking-io",   0,  POPT_ARG_NONE,   0, 		     OPT_NO_BLOCKING_IO, 0, 0 },
  {0,                 'P', POPT_ARG_NONE,   0,               'P', 0, 0 },
  {"config",           0,  POPT_ARG_STRING, &config_file , 0, 0, 0 },
  {"port",             0,  POPT_ARG_INT,    &rsync_port , 0, 0, 0 },
  {"log-format",       0,  POPT_ARG_STRING, &log_format , 0, 0, 0 },
  {"bwlimit",          0,  POPT_ARG_INT,    &bwlimit , 0, 0, 0 },
  {"address",          0,  POPT_ARG_STRING, &bind_address, 0, 0, 0 },
  {"backup-dir",       0,  POPT_ARG_STRING, &backup_dir , 0, 0, 0 },
  {"hard-links",      'H', POPT_ARG_NONE,   &preserve_hard_links , 0, 0, 0 },
  {"read-batch",       0,  POPT_ARG_STRING, &batch_prefix, OPT_READ_BATCH, 0, 0 },
  {"write-batch",      0,  POPT_ARG_STRING, &batch_prefix, OPT_WRITE_BATCH, 0, 0 },
#ifdef INET6
  {0,		      '4', POPT_ARG_VAL,    &default_af_hint,   AF_INET , 0, 0 },
  {0,		      '6', POPT_ARG_VAL,    &default_af_hint,   AF_INET6 , 0, 0 },
#endif
  {0,0,0,0, 0, 0, 0}
};


static char err_buf[100];


/**
 * Store the option error message, if any, so that we can log the
 * connection attempt (which requires parsing the options), and then
 * show the error later on.
 **/
void option_error(void)
{
	if (err_buf[0]) {
		rprintf(FLOG, "%s", err_buf);
		rprintf(FERROR, "%s: %s", RSYNC_NAME, err_buf);
	} else {
		rprintf (FERROR, "Error parsing options: "
			 "option may be supported on client but not on server?\n");
		rprintf (FERROR, RSYNC_NAME ": Error parsing options: "
			 "option may be supported on client but not on server?\n");
	}
}


/**
 * Check to see if we should refuse this option
 **/
static int check_refuse_options(char *ref, int opt)
{
	int i, len;
	char *p;
	const char *name;

	for (i=0; long_options[i].longName; i++) {
		if (long_options[i].val == opt) break;
	}
	
	if (!long_options[i].longName) return 0;

	name = long_options[i].longName;
	len = strlen(name);

	while ((p = strstr(ref,name))) {
		if ((p==ref || p[-1]==' ') &&
		    (p[len] == ' ' || p[len] == 0)) {
			snprintf(err_buf,sizeof(err_buf),
				 "The '%s' option is not supported by this server\n", name);
			return 1;
		}
		ref += len;
	}
	return 0;
}


static int count_args(char const **argv)
{
        int i = 0;

        while (argv[i] != NULL)
                i++;
        
        return i;
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
        poptContext pc;

        /* TODO: Call poptReadDefaultConfig; handle errors. */

        /* The context leaks in case of an error, but if there's a
         * problem we always exit anyhow. */
        pc = poptGetContext(RSYNC_NAME, *argc, *argv, long_options, 0);

	while ((opt = poptGetNextOpt(pc)) != -1) {
		if (ref) {
			if (check_refuse_options(ref, opt)) return 0;
		}

                /* most options are handled automatically by popt;
                 * only special cases are returned and listed here. */

		switch (opt) {
		case OPT_VERSION:
                        print_rsync_version(FINFO);
			exit_cleanup(0);
			
		case OPT_SUFFIX:
                        /* The value has already been set by popt, but
                         * we need to remember that a suffix was specified
                         * in case a backup-directory is used. */
                        suffix_specified = 1;
			break;
			
		case OPT_MODIFY_WINDOW:
                        /* The value has already been set by popt, but
                         * we need to remember that we're using a
                         * non-default setting. */
			modify_window_set = 1;
			break;

		case OPT_DELETE_AFTER:
			delete_after = 1;
			delete_mode = 1;
			break;

		case OPT_DELETE_EXCLUDED:
			delete_excluded = 1;
			delete_mode = 1;
			break;

		case OPT_EXCLUDE:
			add_exclude(poptGetOptArg(pc), 0);
			break;

		case OPT_INCLUDE:
			add_exclude(poptGetOptArg(pc), 1);
			break;

		case OPT_EXCLUDE_FROM:
			add_exclude_file(poptGetOptArg(pc), 1, 0);
			break;

		case OPT_INCLUDE_FROM:
			add_exclude_file(poptGetOptArg(pc), 1, 1);
			break;

		case OPT_WHOLE_FILE:
			whole_file = 1;
			no_whole_file = 0;
			break;

		case OPT_NO_WHOLE_FILE:
			no_whole_file = 1;
			whole_file = 0;
			break;

		case OPT_NO_BLOCKING_IO:
			blocking_io = 0;
			break;

		case 'h':
			usage(FINFO);
			exit_cleanup(0);

		case 'H':
#if SUPPORT_HARD_LINKS
			preserve_hard_links=1;
#else
                        /* FIXME: Don't say "server" if this is
                         * happening on the client. */
                        /* FIXME: Why do we have the duplicated
                         * rprintf?  Everybody who gets this message
                         * ought to send it to the client and also to
                         * the logs. */
			snprintf(err_buf,sizeof(err_buf),
                                 "hard links are not supported on this %s\n",
				 am_server ? "server" : "client");
			rprintf(FERROR,"ERROR: hard links not supported on this platform\n");
			return 0;
#endif /* SUPPORT_HARD_LINKS */
			break;

		case 'v':
			verbose++;
			break;

		case 'q':
			if (frommain) quiet++;
			break;

		case 'a':
			recurse=1;
#if SUPPORT_LINKS
			preserve_links=1;
#endif
			preserve_perms=1;
			preserve_times=1;
			preserve_gid=1;
			preserve_uid=1;
			preserve_devices=1;
			break;

		case OPT_SENDER:
			if (!am_server) {
				usage(FERROR);
				exit_cleanup(RERR_SYNTAX);
			}
			am_sender = 1;
			break;

		case 'P':
			do_progress = 1;
			keep_partial = 1;
			break;

		case OPT_WRITE_BATCH:
			/* popt stores the filename in batch_prefix for us */
			write_batch = 1;
			break;

		case OPT_READ_BATCH:
			/* popt stores the filename in batch_prefix for us */
			read_batch = 1;
			break;
		case OPT_LINK_DEST:
#if HAVE_LINK
			compare_dest = (char *)poptGetOptArg(pc);
			link_dest = 1;
			break;
#else
			snprintf(err_buf,sizeof(err_buf),
                                 "hard links are not supported on this %s\n",
				 am_server ? "server" : "client");
			rprintf(FERROR,"ERROR: hard links not supported on this platform\n");
			return 0;
#endif


		default:
                        /* FIXME: If --daemon is specified, then errors for later
                         * parameters seem to disappear. */
                        snprintf(err_buf, sizeof(err_buf),
                                 "%s%s: %s\n",
                                 am_server ? "on remote machine: " : "",
                                 poptBadOption(pc, POPT_BADOPTION_NOALIAS),
                                 poptStrerror(opt));
                        return 0;
		}
	}

	if (write_batch && read_batch) {
	    snprintf(err_buf,sizeof(err_buf),
		"write-batch and read-batch can not be used together\n");
	    rprintf(FERROR,"ERROR: write-batch and read-batch"
		" can not be used together\n");
	    return 0;
	}

	if (do_compression && (write_batch || read_batch)) {
	    snprintf(err_buf,sizeof(err_buf),
		"compress can not be used with write-batch or read-batch\n");
	    rprintf(FERROR,"ERROR: compress can not be used with"
		"  write-batch or read-batch\n");
	    return 0;
	}

        *argv = poptGetArgs(pc);
        if (*argv)
                *argc = count_args(*argv);
        else
                *argc = 0;

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
	int ac = *argc;
	static char argstr[50];
	static char bsize[30];
	static char iotime[30];
	static char mdelete[30];
	static char mwindow[30];
	static char bw[50];
	/* Leave room for ``--(write|read)-batch='' */
	static char fext[MAXPATHLEN + 15];

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
	for (i=0;i<verbose;i++)
		argstr[x++] = 'v';

	/* the -q option is intentionally left out */
	if (make_backups)
		argstr[x++] = 'b';
	if (update_only)
		argstr[x++] = 'u';
	if (dry_run)
		argstr[x++] = 'n';
	if (preserve_links)
		argstr[x++] = 'l';
	if (copy_links)
		argstr[x++] = 'L';

	assert(whole_file == 0 || whole_file == 1);
	if (whole_file)
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

	/* this is a complete hack - blame Rusty 

	   this is a hack to make the list_only (remote file list)
	   more useful */
	if (list_only && !recurse) 
		argstr[x++] = 'r';

	argstr[x] = 0;

	if (x != 1) args[ac++] = argstr;

	if (block_size != BLOCK_SIZE) {
		snprintf(bsize,sizeof(bsize),"-B%d",block_size);
		args[ac++] = bsize;
	}    

	if (max_delete && am_sender) {
		snprintf(mdelete,sizeof(mdelete),"--max-delete=%d",max_delete);
		args[ac++] = mdelete;
	}    
	
	if (batch_prefix != NULL) {
		char *fmt = "";
		if (write_batch)
		    fmt = "--write-batch=%s";
		else
		if (read_batch)
		    fmt = "--read-batch=%s";
		snprintf(fext,sizeof(fext),fmt,batch_prefix);
		args[ac++] = fext;
	}

	if (io_timeout) {
		snprintf(iotime,sizeof(iotime),"--timeout=%d",io_timeout);
		args[ac++] = iotime;
	}    

	if (bwlimit) {
		snprintf(bw,sizeof(bw),"--bwlimit=%d",bwlimit);
		args[ac++] = bw;
	}

	if (strcmp(backup_suffix, BACKUP_SUFFIX)) {
		args[ac++] = "--suffix";
		args[ac++] = backup_suffix;
	}

	if (delete_mode && !delete_excluded)
		args[ac++] = "--delete";

	if (delete_excluded)
		args[ac++] = "--delete-excluded";

	if (size_only)
		args[ac++] = "--size-only";

	if (modify_window_set) {
	        snprintf(mwindow,sizeof(mwindow),"--modify-window=%d",
			 modify_window);
		args[ac++] = mwindow;
	}

	if (keep_partial)
		args[ac++] = "--partial";

	if (force_delete)
		args[ac++] = "--force";

	if (delete_after)
		args[ac++] = "--delete-after";

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

	if (tmpdir) {
		args[ac++] = "--temp-dir";
		args[ac++] = tmpdir;
	}

	if (backup_dir && am_sender) {
		/* only the receiver needs this option, if we are the sender
		 *   then we need to send it to the receiver.
		 */
		args[ac++] = "--backup-dir";
		args[ac++] = backup_dir;
	}

	if (compare_dest && am_sender) {
		/* the server only needs this option if it is not the sender,
		 *   and it may be an older version that doesn't know this
		 *   option, so don't send it if client is the sender.
		 */
		args[ac++] = link_dest ? "--link-dest" : "--compare-dest";
		args[ac++] = compare_dest;
	}

	*argc = ac;
}

