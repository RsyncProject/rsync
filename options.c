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

/* options parsing code */

#include "rsync.h"


int make_backups = 0;
int whole_file = 0;
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
int am_sender=0;
int recurse = 0;
int am_daemon=0;
int do_stats=0;
int do_progress=0;
int keep_partial=0;
int safe_symlinks=0;
int copy_unsafe_links=0;
int block_size=BLOCK_SIZE;
int size_only=0;

char *backup_suffix = BACKUP_SUFFIX;
char *tmpdir = NULL;
char *compare_dest = NULL;
char *config_file = RSYNCD_CONF;
char *shell_cmd = NULL;
char *log_format = NULL;
char *password_file = NULL;
char *rsync_path = RSYNC_NAME;
int rsync_port = RSYNC_PORT;

int verbose = 0;
int quiet = 0;
int always_checksum = 0;
int list_only = 0;

void usage(int F)
{
  rprintf(F,"rsync version %s Copyright Andrew Tridgell and Paul Mackerras\n\n",
	  VERSION);

  rprintf(F,"rsync is a file transfer program capable of efficient remote update\nvia a fast differencing algorithm.\n\n");

  rprintf(F,"Usage: rsync [OPTION]... SRC [SRC]... [USER@]HOST:DEST\n");
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST:SRC DEST\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... DEST\n");
  rprintf(F,"  or   rsync [OPTION]... [USER@]HOST::SRC [DEST]\n");
  rprintf(F,"  or   rsync [OPTION]... SRC [SRC]... [USER@]HOST::DEST\n");
  rprintf(F,"  or   rsync [OPTION]... rsync://[USER@]HOST[:PORT]/SRC [DEST]\n");
  rprintf(F,"SRC on single-colon remote HOST will be expanded by remote shell\n");
  rprintf(F,"SRC on server remote HOST may contain shell wildcards or multiple\n");
  rprintf(F,"  sources separated by space as long as they have same top-level\n");
  rprintf(F,"\nOptions\n");
  rprintf(F," -v, --verbose               increase verbosity\n");
  rprintf(F," -q, --quiet                 decrease verbosity\n");
  rprintf(F," -c, --checksum              always checksum\n");
  rprintf(F," -a, --archive               archive mode\n");
  rprintf(F," -r, --recursive             recurse into directories\n");
  rprintf(F," -R, --relative              use relative path names\n");
  rprintf(F," -b, --backup                make backups (default %s suffix)\n",BACKUP_SUFFIX);
  rprintf(F,"     --suffix=SUFFIX         override backup suffix\n");  
  rprintf(F," -u, --update                update only (don't overwrite newer files)\n");
  rprintf(F," -l, --links                 preserve soft links\n");
  rprintf(F," -L, --copy-links            treat soft links like regular files\n");
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
  rprintf(F," -x, --one-file-system       don't cross filesystem boundaries\n");
  rprintf(F," -B, --block-size=SIZE       checksum blocking size (default %d)\n",BLOCK_SIZE);  
  rprintf(F," -e, --rsh=COMMAND           specify rsh replacement\n");
  rprintf(F,"     --rsync-path=PATH       specify path to rsync on the remote machine\n");
  rprintf(F," -C, --cvs-exclude           auto ignore files in the same way CVS does\n");
  rprintf(F,"     --delete                delete files that don't exist on the sending side\n");
  rprintf(F,"     --delete-excluded       also delete excluded files on the receiving side\n");
  rprintf(F,"     --partial               keep partially transferred files\n");
  rprintf(F,"     --force                 force deletion of directories even if not empty\n");
  rprintf(F,"     --numeric-ids           don't map uid/gid values by user/group name\n");
  rprintf(F,"     --timeout=TIME          set IO timeout in seconds\n");
  rprintf(F," -I, --ignore-times          don't exclude files that match length and time\n");
  rprintf(F,"     --size-only             only use file size when determining if a file should be transferred\n");
  rprintf(F," -T  --temp-dir=DIR          create temporary files in directory DIR\n");
  rprintf(F,"     --compare-dest=DIR      also compare destination files relative to DIR\n");
  rprintf(F," -z, --compress              compress file data\n");
  rprintf(F,"     --exclude=PATTERN       exclude files matching PATTERN\n");
  rprintf(F,"     --exclude-from=FILE     exclude patterns listed in FILE\n");
  rprintf(F,"     --include=PATTERN       don't exclude files matching PATTERN\n");
  rprintf(F,"     --include-from=FILE     don't exclude patterns listed in FILE\n");
  rprintf(F,"     --version               print version number\n");  
  rprintf(F,"     --daemon                run as a rsync daemon\n");  
  rprintf(F,"     --config=FILE           specify alternate rsyncd.conf file\n");  
  rprintf(F,"     --port=PORT             specify alternate rsyncd port number\n");
  rprintf(F,"     --stats                 give some file transfer stats\n");  
  rprintf(F,"     --progress              show progress during transfer\n");  
  rprintf(F,"     --log-format=FORMAT     log file transfers using specified format\n");  
  rprintf(F,"     --password-file=FILE    get password from FILE\n");
  rprintf(F," -h, --help                  show this help screen\n");

  rprintf(F,"\n");

  rprintf(F,"\nPlease see the rsync(1) and rsyncd.conf(5) man pages for full documentation\n");
  rprintf(F,"See http://rsync.samba.org/ for updates and bug reports\n");
}

enum {OPT_VERSION, OPT_SUFFIX, OPT_SENDER, OPT_SERVER, OPT_EXCLUDE,
      OPT_EXCLUDE_FROM, OPT_DELETE, OPT_DELETE_EXCLUDED, OPT_NUMERIC_IDS,
      OPT_RSYNC_PATH, OPT_FORCE, OPT_TIMEOUT, OPT_DAEMON, OPT_CONFIG, OPT_PORT,
      OPT_INCLUDE, OPT_INCLUDE_FROM, OPT_STATS, OPT_PARTIAL, OPT_PROGRESS,
      OPT_COPY_UNSAFE_LINKS, OPT_SAFE_LINKS, OPT_COMPARE_DEST,
      OPT_LOG_FORMAT, OPT_PASSWORD_FILE, OPT_SIZE_ONLY};

static char *short_options = "oblLWHpguDCtcahvqrRIxnSe:B:T:z";

static struct option long_options[] = {
  {"version",     0,     0,    OPT_VERSION},
  {"server",      0,     0,    OPT_SERVER},
  {"sender",      0,     0,    OPT_SENDER},
  {"delete",      0,     0,    OPT_DELETE},
  {"delete-excluded", 0, 0,    OPT_DELETE_EXCLUDED},
  {"force",       0,     0,    OPT_FORCE},
  {"numeric-ids", 0,     0,    OPT_NUMERIC_IDS},
  {"exclude",     1,     0,    OPT_EXCLUDE},
  {"exclude-from",1,     0,    OPT_EXCLUDE_FROM},
  {"include",     1,     0,    OPT_INCLUDE},
  {"include-from",1,     0,    OPT_INCLUDE_FROM},
  {"rsync-path",  1,     0,    OPT_RSYNC_PATH},
  {"password-file", 1,	0,     OPT_PASSWORD_FILE},
  {"one-file-system",0,  0,    'x'},
  {"ignore-times",0,     0,    'I'},
  {"size-only",   0,     0,    OPT_SIZE_ONLY},
  {"help",        0,     0,    'h'},
  {"dry-run",     0,     0,    'n'},
  {"sparse",      0,     0,    'S'},
  {"cvs-exclude", 0,     0,    'C'},
  {"archive",     0,     0,    'a'},
  {"checksum",    0,     0,    'c'},
  {"backup",      0,     0,    'b'},
  {"update",      0,     0,    'u'},
  {"verbose",     0,     0,    'v'},
  {"quiet",       0,     0,    'q'},
  {"recursive",   0,     0,    'r'},
  {"relative",    0,     0,    'R'},
  {"devices",     0,     0,    'D'},
  {"perms",       0,     0,    'p'},
  {"links",       0,     0,    'l'},
  {"copy-links",  0,     0,    'L'},
  {"copy-unsafe-links", 0, 0,  OPT_COPY_UNSAFE_LINKS},
  {"safe-links",  0,     0,    OPT_SAFE_LINKS},
  {"whole-file",  0,     0,    'W'},
  {"hard-links",  0,     0,    'H'},
  {"owner",       0,     0,    'o'},
  {"group",       0,     0,    'g'},
  {"times",       0,     0,    't'},
  {"rsh",         1,     0,    'e'},
  {"suffix",      1,     0,    OPT_SUFFIX},
  {"block-size",  1,     0,    'B'},
  {"timeout",     1,     0,    OPT_TIMEOUT},
  {"temp-dir",    1,     0,    'T'},
  {"compare-dest", 1,    0,    OPT_COMPARE_DEST},
  {"compress",	  0,	 0,    'z'},
  {"daemon",      0,     0,    OPT_DAEMON},
  {"stats",       0,     0,    OPT_STATS},
  {"progress",    0,     0,    OPT_PROGRESS},
  {"partial",     0,     0,    OPT_PARTIAL},
  {"config",      1,     0,    OPT_CONFIG},
  {"port",        1,     0,    OPT_PORT},
  {"log-format",  1,     0,    OPT_LOG_FORMAT},
  {0,0,0,0}};


static char err_buf[100];

void option_error(void)
{
	if (err_buf[0]) {
		rprintf(FLOG,"%s", err_buf);
		rprintf(FERROR,"%s", err_buf);
	} else {
		rprintf(FLOG,"Error parsing options - unsupported option?\n");
		rprintf(FERROR,"Error parsing options - unsupported option?\n");
	}
	exit_cleanup(RERR_UNSUPPORTED);
}

/* check to see if we should refuse this option */
static int check_refuse_options(char *ref, int opt)
{
	int i, len;
	char *p;
	const char *name;

	for (i=0; long_options[i].name; i++) {
		if (long_options[i].val == opt) break;
	}
	
	if (!long_options[i].name) return 0;

	name = long_options[i].name;
	len = strlen(name);

	while ((p = strstr(ref,name))) {
		if ((p==ref || p[-1]==' ') &&
		    (p[len] == ' ' || p[len] == 0)) {
			slprintf(err_buf,sizeof(err_buf),
				 "The '%s' option is not supported by this server\n", name);
			return 1;
		}
		ref += len;
	}
	return 0;
}


int parse_arguments(int argc, char *argv[], int frommain)
{
	int opt;
	int option_index;
	char *ref = lp_refuse_options(module_id);

	while ((opt = getopt_long(argc, argv, 
				  short_options, long_options, &option_index)) 
	       != -1) {

		if (ref) {
			if (check_refuse_options(ref, opt)) return 0;
		}

		switch (opt) {
		case OPT_VERSION:
			rprintf(FINFO,"rsync version %s  protocol version %d\n\n",
				VERSION,PROTOCOL_VERSION);
			rprintf(FINFO,"Written by Andrew Tridgell and Paul Mackerras\n");
			exit_cleanup(0);
			
		case OPT_SUFFIX:
			backup_suffix = optarg;
			break;
			
		case OPT_RSYNC_PATH:
			rsync_path = optarg;
			break;
	
		case OPT_PASSWORD_FILE:
			password_file =optarg;
			break;		

		case 'I':
			ignore_times = 1;
			break;

		case OPT_SIZE_ONLY:
			size_only = 1;
			break;

		case 'x':
			one_file_system=1;
			break;

		case OPT_DELETE:
			delete_mode = 1;
			break;

		case OPT_DELETE_EXCLUDED:
			delete_excluded = 1;
			delete_mode = 1;
			break;

		case OPT_FORCE:
			force_delete = 1;
			break;

		case OPT_NUMERIC_IDS:
			numeric_ids = 1;
			break;

		case OPT_EXCLUDE:
			add_exclude(optarg, 0);
			break;

		case OPT_INCLUDE:
			add_exclude(optarg, 1);
			break;

		case OPT_EXCLUDE_FROM:
			add_exclude_file(optarg,1, 0);
			break;

		case OPT_INCLUDE_FROM:
			add_exclude_file(optarg,1, 1);
			break;

		case OPT_COPY_UNSAFE_LINKS:
			copy_unsafe_links=1;
			break;

		case OPT_SAFE_LINKS:
			safe_symlinks=1;
			break;

		case 'h':
			usage(FINFO);
			exit_cleanup(0);

		case 'b':
			make_backups=1;
			break;

		case 'n':
			dry_run=1;
			break;

		case 'S':
			sparse_files=1;
			break;

		case 'C':
			cvs_exclude=1;
			break;

		case 'u':
			update_only=1;
			break;

		case 'l':
			preserve_links=1;
			break;

		case 'L':
			copy_links=1;
			break;

		case 'W':
			whole_file=1;
			break;

		case 'H':
#if SUPPORT_HARD_LINKS
			preserve_hard_links=1;
#else 
			slprintf(err_buf,sizeof(err_buf),"hard links are not supported on this server\n");
			rprintf(FERROR,"ERROR: hard links not supported on this platform\n");
			return 0;
#endif
			break;

		case 'p':
			preserve_perms=1;
			break;

		case 'o':
			preserve_uid=1;
			break;

		case 'g':
			preserve_gid=1;
			break;

		case 'D':
			preserve_devices=1;
			break;

		case 't':
			preserve_times=1;
			break;

		case 'c':
			always_checksum=1;
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
			if (am_root) {
				preserve_devices=1;
				preserve_uid=1;
			}
			break;

		case OPT_SERVER:
			am_server = 1;
			break;

		case OPT_SENDER:
			if (!am_server) {
				usage(FERROR);
				exit_cleanup(RERR_SYNTAX);
			}
			am_sender = 1;
			break;

		case 'r':
			recurse = 1;
			break;

		case 'R':
			relative_paths = 1;
			break;

		case 'e':
			shell_cmd = optarg;
			break;

		case 'B':
			block_size = atoi(optarg);
			break;

		case OPT_TIMEOUT:
			io_timeout = atoi(optarg);
			break;

		case 'T':
			tmpdir = optarg;
			break;

		case OPT_COMPARE_DEST:
			compare_dest = optarg;
			break;

		case 'z':
			do_compression = 1;
			break;

		case OPT_DAEMON:
			am_daemon = 1;
			break;

		case OPT_STATS:
			do_stats = 1;
			break;

		case OPT_PROGRESS:
			do_progress = 1;
			break;

		case OPT_PARTIAL:
			keep_partial = 1;
			break;

		case OPT_CONFIG:
			config_file = optarg;
			break;

		case OPT_PORT:
			rsync_port = atoi(optarg);
			break;

		case OPT_LOG_FORMAT:
			log_format = optarg;
			break;

		default:
			slprintf(err_buf,sizeof(err_buf),"unrecognised option\n");
			return 0;
		}
	}
	return 1;
}


void server_options(char **args,int *argc)
{
	int ac = *argc;
	static char argstr[50];
	static char bsize[30];
	static char iotime[30];
	int i, x;

	args[ac++] = "--server";

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
	if (whole_file)
		argstr[x++] = 'W';
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
	argstr[x] = 0;

	if (x != 1) args[ac++] = argstr;

	if (block_size != BLOCK_SIZE) {
		sprintf(bsize,"-B%d",block_size);
		args[ac++] = bsize;
	}    

	if (io_timeout) {
		sprintf(iotime,"--timeout=%d",io_timeout);
		args[ac++] = iotime;
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

	if (keep_partial)
		args[ac++] = "--partial";

	if (force_delete)
		args[ac++] = "--force";

	if (copy_unsafe_links)
		args[ac++] = "--copy-unsafe-links";

	if (safe_symlinks)
		args[ac++] = "--safe-links";

	if (numeric_ids)
		args[ac++] = "--numeric-ids";

	if (tmpdir) {
		args[ac++] = "--temp-dir";
		args[ac++] = tmpdir;
	}

	if (compare_dest && am_sender) {
		/* the server only needs this option if it is not the sender,
		 *   and it may be an older version that doesn't know this
		 *   option, so don't send it if client is the sender.
		 */
		args[ac++] = "--compare-dest";
		args[ac++] = compare_dest;
	}


	*argc = ac;
}

