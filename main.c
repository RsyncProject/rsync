/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
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

#include "rsync.h"

int verbose = 0;
int always_checksum = 0;
time_t starttime = 0;
int64 total_size = 0;
int block_size=BLOCK_SIZE;

char *backup_suffix = BACKUP_SUFFIX;
char *tmpdir = NULL;

static char *rsync_path = RSYNC_NAME;

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
static int module_id;

static int port = RSYNC_PORT;

static char *shell_cmd;

extern int csum_length;

int am_server = 0;
int am_sender=0;
int recurse = 0;
int am_daemon=0;

static void usage(int fd);

static void report(int f)
{
  int64 in,out,tsize;
  time_t t = time(NULL);
  
  if (!verbose) return;

  if (am_server && am_sender) {
    write_longint(f,read_total());
    write_longint(f,write_total());
    write_longint(f,total_size);
    write_flush(f);
    return;
  }
    
  if (am_sender) {
    in = read_total();
    out = write_total();
    tsize = total_size;
  } else {
    in = read_longint(f);
    out = read_longint(f);
    tsize = read_longint(f);
  }

  printf("wrote %.0f bytes  read %.0f bytes  %.2f bytes/sec\n",
	 (double)out,(double)in,(in+out)/(0.5 + (t-starttime)));
  printf("total size is %.0f  speedup is %.2f\n",
	 (double)tsize,(1.0*tsize)/(in+out));
}


static void server_options(char **args,int *argc)
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

  if (delete_mode)
    args[ac++] = "--delete";

  if (force_delete)
    args[ac++] = "--force";

  if (numeric_ids)
    args[ac++] = "--numeric-ids";

  if (tmpdir) {
	  args[ac++] = "--temp-dir";
	  args[ac++] = tmpdir;
  }

  *argc = ac;
}



static int do_cmd(char *cmd,char *machine,char *user,char *path,int *f_in,int *f_out)
{
	char *args[100];
	int i,argc=0, ret;
	char *tok,*dir=NULL;

	if (!local_server) {
		if (!cmd)
			cmd = getenv(RSYNC_RSH_ENV);
		if (!cmd)
			cmd = RSYNC_RSH;
		cmd = strdup(cmd);
		if (!cmd) 
			goto oom;

		for (tok=strtok(cmd," ");tok;tok=strtok(NULL," ")) {
			args[argc++] = tok;
		}

#if HAVE_REMSH
		/* remsh (on HPUX) takes the arguments the other way around */
		args[argc++] = machine;
		if (user) {
			args[argc++] = "-l";
			args[argc++] = user;
		}
#else
		if (user) {
			args[argc++] = "-l";
			args[argc++] = user;
		}
		args[argc++] = machine;
#endif

		args[argc++] = rsync_path;

		server_options(args,&argc);
	}

	args[argc++] = ".";

	if (path && *path) 
		args[argc++] = path;

	args[argc] = NULL;

	if (verbose > 3) {
		rprintf(FINFO,"cmd=");
		for (i=0;i<argc;i++)
			rprintf(FINFO,"%s ",args[i]);
		rprintf(FINFO,"\n");
	}

	if (local_server) {
		ret = local_child(argc, args, f_in, f_out);
	} else {
		ret = piped_child(args,f_in,f_out);
	}

	if (dir) free(dir);

	return ret;

oom:
	out_of_memory("do_cmd");
	return 0; /* not reached */
}




static char *get_local_name(struct file_list *flist,char *name)
{
  STRUCT_STAT st;

  if (do_stat(name,&st) == 0) {
    if (S_ISDIR(st.st_mode)) {
      if (chdir(name) != 0) {
	rprintf(FERROR,"chdir %s : %s (1)\n",name,strerror(errno));
	exit_cleanup(1);
      }
      return NULL;
    }
    if (flist->count > 1) {
      rprintf(FERROR,"ERROR: destination must be a directory when copying more than 1 file\n");
      exit_cleanup(1);
    }
    return name;
  }

  if (flist->count == 1)
    return name;

  if (!name) 
    return NULL;

  if (do_mkdir(name,0777 & ~orig_umask) != 0) {
    rprintf(FERROR,"mkdir %s : %s (1)\n",name,strerror(errno));
    exit_cleanup(1);
  } else {
    rprintf(FINFO,"created directory %s\n",name);
  }

  if (chdir(name) != 0) {
    rprintf(FERROR,"chdir %s : %s (2)\n",name,strerror(errno));
    exit_cleanup(1);
  }

  return NULL;
}




static void do_server_sender(int f_in, int f_out, int argc,char *argv[])
{
  int i;
  struct file_list *flist;
  char *dir = argv[0];

  if (verbose > 2)
    rprintf(FINFO,"server_sender starting pid=%d\n",(int)getpid());
  
  if (!relative_paths && chdir(dir) != 0) {
	  rprintf(FERROR,"chdir %s: %s (3)\n",dir,strerror(errno));
	  exit_cleanup(1);
  }
  argc--;
  argv++;
  
  if (strcmp(dir,".")) {
	  int l = strlen(dir);
	  if (strcmp(dir,"/") == 0) 
		  l = 0;
	  for (i=0;i<argc;i++)
		  argv[i] += l+1;
  }

  if (am_daemon) {
	  char *name = lp_name(module_id);
	  int l = strlen(name);
	  for (i=0;i<argc;i++) {
		  if (strncmp(argv[i], name, l) == 0) {
			  argv[i] += l;
			  if (!*argv[i]) argv[i] = ".";
		  }
	  }
  }

  if (argc == 0 && recurse) {
	  argc=1;
	  argv--;
	  argv[0] = ".";
  }

  rprintf(FINFO,"sending file list\n");

  flist = send_file_list(f_out,argc,argv);
  send_files(flist,f_out,f_in);
  report(f_out);
  exit_cleanup(0);
}


static int do_recv(int f_in,int f_out,struct file_list *flist,char *local_name)
{
  int pid;
  int status=0;
  int recv_pipe[2];

  if (preserve_hard_links)
    init_hard_links(flist);

  if (pipe(recv_pipe) < 0) {
    rprintf(FERROR,"pipe failed in do_recv\n");
    exit(1);
  }
  

  if ((pid=do_fork()) == 0) {
    recv_files(f_in,flist,local_name,recv_pipe[1]);
    if (verbose > 2)
      rprintf(FINFO,"receiver read %ld\n",(long)read_total());
    exit_cleanup(0);
  }

  generate_files(f_out,flist,local_name,recv_pipe[0]);

  waitpid(pid, &status, 0);

  return status;
}


static void do_server_recv(int f_in, int f_out, int argc,char *argv[])
{
  int status;
  struct file_list *flist;
  char *local_name=NULL;
  char *dir = NULL;
  
  if (verbose > 2)
    rprintf(FINFO,"server_recv(%d) starting pid=%d\n",argc,(int)getpid());

  if (am_daemon) {
	  char *name = lp_name(module_id);
	  int i, l = strlen(name);
	  for (i=0;i<argc;i++) {
		  if (strncmp(argv[i], name, l) == 0) {
			  argv[i] += l;
			  if (!*argv[i]) argv[i] = ".";
		  }
		  rprintf(FINFO,"argv[%d]=%s\n", i, argv[i]);
	  }
  }

  if (argc > 0) {
	  dir = argv[0];
	  argc--;
	  argv++;
	  if (!am_daemon && chdir(dir) != 0) {
		  rprintf(FERROR,"chdir %s : %s (4)\n",
			  dir,strerror(errno));
		  exit_cleanup(1);
	  }    
  }

  if (delete_mode)
    recv_exclude_list(f_in);

  flist = recv_file_list(f_in);
  if (!flist || flist->count == 0) {
    rprintf(FERROR,"nothing to do\n");
    exit_cleanup(1);
  }

  if (argc > 0) {    
	  if (strcmp(dir,".")) {
		  argv[0] += strlen(dir);
		  if (argv[0][0] == '/') argv[0]++;
	  }
	  local_name = get_local_name(flist,argv[0]);
  }

  status = do_recv(f_in,f_out,flist,local_name);
  exit_cleanup(status);
}


void start_server(int f_in, int f_out, int argc, char *argv[])
{
      setup_protocol(f_out, f_in);
	
      if (am_sender) {
	      recv_exclude_list(f_in);
	      if (cvs_exclude)
		      add_cvs_excludes();
	      do_server_sender(f_in, f_out, argc, argv);
      } else {
	      do_server_recv(f_in, f_out, argc, argv);
      }
      exit_cleanup(0);
}

static int client_run(int f_in, int f_out, int pid, int argc, char *argv[])
{
	struct file_list *flist;
	int status = 0, status2 = 0;
	char *local_name = NULL;

	setup_protocol(f_out,f_in);
	
	if (am_sender) {
		if (cvs_exclude)
			add_cvs_excludes();
		if (delete_mode) 
			send_exclude_list(f_out);
		flist = send_file_list(f_out,argc,argv);
		if (verbose > 3) 
			rprintf(FINFO,"file list sent\n");
		send_files(flist,f_out,f_in);
		if (pid != -1) {
			if (verbose > 3)
				rprintf(FINFO,"waiting on %d\n",pid);
			waitpid(pid, &status, 0);
		}
		report(-1);
		exit_cleanup(status);
	}
	
	send_exclude_list(f_out);
	
	flist = recv_file_list(f_in);
	if (!flist || flist->count == 0) {
		rprintf(FINFO,"nothing to do\n");
		exit_cleanup(0);
	}
	
	local_name = get_local_name(flist,argv[0]);
	
	status2 = do_recv(f_in,f_out,flist,local_name);
	
	report(f_in);
	
	if (pid != -1) {
		waitpid(pid, &status, 0);
	}
	
	return status | status2;
}


int start_socket_client(char *host, char *path, int argc, char *argv[])
{
	int fd, i;
	char *sargs[MAX_ARGS];
	int sargc=0;
	char line[1024];
	char *p;
	int version;

	fd = open_socket_out(host, port);
	if (fd == -1) {
		exit_cleanup(1);
	}
	
	server_options(sargs,&sargc);

	sargs[sargc++] = ".";

	if (path && *path) 
		sargs[sargc++] = path;

	sargs[sargc] = NULL;

	p = strchr(path,'/');
	if (p) *p = 0;
	io_printf(fd,"%s\n",path);
	if (p) *p = '/';

	if (!read_line(fd, line, sizeof(line)-1)) {
		return -1;
	}

	if (sscanf(line,"RSYNCD %d", &version) != 1) {
		return -1;
	}

	while (1) {
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}
		if (strcmp(line,"RSYNCD: OK") == 0) break;
		rprintf(FINFO,"%s\n", line);
	}

	for (i=0;i<sargc;i++) {
		io_printf(fd,"%s\n", sargs[i]);
	}
	io_printf(fd,"\n");

#if 0
	while (1) {
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}
		rprintf(FINFO,"%s\n", line);
	}
#endif

	return client_run(fd, fd, -1, argc, argv);
}

int start_client(int argc, char *argv[])
{
	char *p;
	char *shell_machine = NULL;
	char *shell_path = NULL;
	char *shell_user = NULL;
	int pid;
	int f_in,f_out;

	p = strchr(argv[0],':');

	if (p) {
		if (p[1] == ':') {
			*p = 0;
			return start_socket_client(argv[0], p+2, argc-1, argv+1);
		}
		am_sender = 0;
		*p = 0;
		shell_machine = argv[0];
		shell_path = p+1;
		argc--;
		argv++;
	} else {
		am_sender = 1;

		p = strchr(argv[argc-1],':');
		if (!p) {
			local_server = 1;
		} else if (p[1] == ':') {
			*p = 0;
			return start_socket_client(argv[argc-1], p+2, argc-1, argv);
		}
		
		if (local_server) {
			shell_machine = NULL;
			shell_path = argv[argc-1];
		} else {
			*p = 0;
			shell_machine = argv[argc-1];
			shell_path = p+1;
		}
		argc--;
	}
	
	if (shell_machine) {
		p = strchr(shell_machine,'@');
		if (p) {
			*p = 0;
			shell_user = shell_machine;
			shell_machine = p+1;
		}
	}

	if (verbose > 3) {
		rprintf(FINFO,"cmd=%s machine=%s user=%s path=%s\n",
			shell_cmd?shell_cmd:"",
			shell_machine?shell_machine:"",
			shell_user?shell_user:"",
			shell_path?shell_path:"");
	}
	
	if (!am_sender && argc != 1) {
		usage(FERROR);
		exit_cleanup(1);
	}
	
	pid = do_cmd(shell_cmd,shell_machine,shell_user,shell_path,&f_in,&f_out);
	
#if HAVE_SETLINEBUF
	setlinebuf(stdout);
	setlinebuf(stderr);
#endif

	return client_run(f_in, f_out, pid, argc, argv);
}


static void usage(int F)
{
  rprintf(F,"rsync version %s Copyright Andrew Tridgell and Paul Mackerras\n\n",
	  VERSION);
  rprintf(F,"Usage:\t%s [options] src user@host:dest\nOR",RSYNC_NAME);
  rprintf(F,"\t%s [options] user@host:src dest\n\n",RSYNC_NAME);
  rprintf(F,"Options:\n");
  rprintf(F,"-v, --verbose            increase verbosity\n");
  rprintf(F,"-c, --checksum           always checksum\n");
  rprintf(F,"-a, --archive            archive mode (same as -rlptDog)\n");
  rprintf(F,"-r, --recursive          recurse into directories\n");
  rprintf(F,"-R, --relative           use relative path names\n");
  rprintf(F,"-b, --backup             make backups (default ~ extension)\n");
  rprintf(F,"-u, --update             update only (don't overwrite newer files)\n");
  rprintf(F,"-l, --links              preserve soft links\n");
  rprintf(F,"-L, --copy-links         treat soft links like regular files\n");
  rprintf(F,"-H, --hard-links         preserve hard links\n");
  rprintf(F,"-p, --perms              preserve permissions\n");
  rprintf(F,"-o, --owner              preserve owner (root only)\n");
  rprintf(F,"-g, --group              preserve group\n");
  rprintf(F,"-D, --devices            preserve devices (root only)\n");
  rprintf(F,"-t, --times              preserve times\n");  
  rprintf(F,"-S, --sparse             handle sparse files efficiently\n");
  rprintf(F,"-n, --dry-run            show what would have been transferred\n");
  rprintf(F,"-W, --whole-file         copy whole files, no incremental checks\n");
  rprintf(F,"-x, --one-file-system    don't cross filesystem boundaries\n");
  rprintf(F,"-B, --block-size SIZE    checksum blocking size\n");  
  rprintf(F,"-e, --rsh COMMAND        specify rsh replacement\n");
  rprintf(F,"    --rsync-path PATH    specify path to rsync on the remote machine\n");
  rprintf(F,"-C, --cvs-exclude        auto ignore files in the same way CVS does\n");
  rprintf(F,"    --delete             delete files that don't exist on the sending side\n");
  rprintf(F,"    --force              force deletion of directories even if not empty\n");
  rprintf(F,"    --numeric-ids        don't map uid/gid values by user/group name\n");
  rprintf(F,"    --timeout TIME       set IO timeout in seconds\n");
  rprintf(F,"-I, --ignore-times       don't exclude files that match length and time\n");
  rprintf(F,"-T  --temp-dir DIR       create temporary files in directory DIR\n");
  rprintf(F,"-z, --compress           compress file data\n");
  rprintf(F,"    --exclude FILE       exclude file FILE\n");
  rprintf(F,"    --exclude-from FILE  exclude files listed in FILE\n");
  rprintf(F,"    --suffix SUFFIX      override backup suffix\n");  
  rprintf(F,"    --version            print version number\n");  

  rprintf(F,"\n");
  rprintf(F,"the backup suffix defaults to %s\n",BACKUP_SUFFIX);
  rprintf(F,"the block size defaults to %d\n",BLOCK_SIZE);  
}

enum {OPT_VERSION,OPT_SUFFIX,OPT_SENDER,OPT_SERVER,OPT_EXCLUDE,
      OPT_EXCLUDE_FROM,OPT_DELETE,OPT_NUMERIC_IDS,OPT_RSYNC_PATH,
      OPT_FORCE,OPT_TIMEOUT,OPT_DAEMON};

static char *short_options = "oblLWHpguDCtcahvrRIxnSe:B:T:z";

static struct option long_options[] = {
  {"version",     0,     0,    OPT_VERSION},
  {"server",      0,     0,    OPT_SERVER},
  {"sender",      0,     0,    OPT_SENDER},
  {"delete",      0,     0,    OPT_DELETE},
  {"force",       0,     0,    OPT_FORCE},
  {"numeric-ids", 0,     0,    OPT_NUMERIC_IDS},
  {"exclude",     1,     0,    OPT_EXCLUDE},
  {"exclude-from",1,     0,    OPT_EXCLUDE_FROM},
  {"rsync-path",  1,     0,    OPT_RSYNC_PATH},
  {"one-file-system",0,  0,    'x'},
  {"ignore-times",0,     0,    'I'},
  {"help",        0,     0,    'h'},
  {"dry-run",     0,     0,    'n'},
  {"sparse",      0,     0,    'S'},
  {"cvs-exclude", 0,     0,    'C'},
  {"archive",     0,     0,    'a'},
  {"checksum",    0,     0,    'c'},
  {"backup",      0,     0,    'b'},
  {"update",      0,     0,    'u'},
  {"verbose",     0,     0,    'v'},
  {"recursive",   0,     0,    'r'},
  {"relative",    0,     0,    'R'},
  {"devices",     0,     0,    'D'},
  {"perms",       0,     0,    'p'},
  {"links",       0,     0,    'l'},
  {"copy-links",  0,     0,    'L'},
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
  {"compress",	  0,	 0,    'z'},
  {"daemon",      0,     0,    OPT_DAEMON},
  {0,0,0,0}};

RETSIGTYPE sigusr1_handler(int val) {
	exit_cleanup(1);
}


static void parse_arguments(int argc, char *argv[])
{
    int opt;
    int option_index;

    while ((opt = getopt_long(argc, argv, 
			      short_options, long_options, &option_index)) 
	   != -1) {
      switch (opt) 
	{
	case OPT_VERSION:
	  printf("rsync version %s  protocol version %d\n",
		 VERSION,PROTOCOL_VERSION);
	  exit_cleanup(0);

	case OPT_SUFFIX:
	  backup_suffix = optarg;
	  break;

	case OPT_RSYNC_PATH:
	  rsync_path = optarg;
	  break;

	case 'I':
	  ignore_times = 1;
	  break;

	case 'x':
	  one_file_system=1;
	  break;

	case OPT_DELETE:
	  delete_mode = 1;
	  break;

	case OPT_FORCE:
	  force_delete = 1;
	  break;

	case OPT_NUMERIC_IDS:
	  numeric_ids = 1;
	  break;

	case OPT_EXCLUDE:
	  add_exclude(optarg);
	  break;

	case OPT_EXCLUDE_FROM:
	  add_exclude_file(optarg,1);
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
	  rprintf(FERROR,"ERROR: hard links not supported on this platform\n");
	  exit_cleanup(1);
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
	    exit_cleanup(1);
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

        case 'z':
	  do_compression = 1;
	  break;

	case OPT_DAEMON:
		am_daemon = 1;
		break;

	default:
	  /* rprintf(FERROR,"bad option -%c\n",opt); */
	  exit_cleanup(1);
	}
    }
}

static int rsync_module(int fd, int i)
{
	int argc=0;
	char *argv[MAX_ARGS];
	char **argp;
	char line[1024];

	module_id = i;

	if (lp_read_only(i))
		read_only = 1;

	rprintf(FERROR,"rsyncd starting\n");

	if (chroot(lp_path(i))) {
		io_printf(fd,"ERROR: chroot failed\n");
		return -1;
	}

	if (chdir("/")) {
		io_printf(fd,"ERROR: chdir failed\n");
		return -1;
	}

	if (setgid(lp_gid(i))) {
		io_printf(fd,"ERROR: setgid failed\n");
		return -1;
	}

	if (setuid(lp_uid(i))) {
		io_printf(fd,"ERROR: setuid failed\n");
		return -1;
	}

	io_printf(fd,"RSYNCD: OK\n");

	argv[argc++] = "rsyncd";

	while (1) {
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}

		if (!*line) break;

		argv[argc] = strdup(line);
		if (!argv[argc]) {
			return -1;
		}

		argc++;
		if (argc == MAX_ARGS) {
			return -1;
		}
	}

	parse_arguments(argc, argv);

	/* don't allow the logs to be flooded too fast */
	if (verbose > 1) verbose = 1;

	argc -= optind;
	argp = argv + optind;
	optind = 0;

	start_server(fd, fd, argc, argp);

	return 0;
}

static void send_listing(int fd)
{
	int n = lp_numservices();
	int i;
	
	for (i=0;i<n;i++)
		if (lp_list(i))
		    io_printf(fd, "%-15s\t%s\n", lp_name(i), lp_comment(i));
}

/* this is called when a socket connection is established to a client
   and we want to start talking. The setup of the system is done from
   here */
static int start_daemon(int fd)
{
	char line[1024];
	char *motd;

	set_socket_options(fd,"SO_KEEPALIVE");

	io_printf(fd,"RSYNCD %d\n", PROTOCOL_VERSION);

	motd = lp_motd_file();
	if (*motd) {
		FILE *f = fopen(motd,"r");
		while (f && !feof(f)) {
			int len = fread(line, 1, sizeof(line)-1, f);
			if (len > 0) {
				line[len] = 0;
				io_printf(fd,"%s", line);
			}
		}
		if (f) fclose(f);
		io_printf(fd,"\n");
	}

	/* read a single line indicating the resource that is wanted */
	while (1) {
		int i;

		line[0] = 0;
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}

		if (!*line || strcmp(line,"#list")==0) {
			send_listing(fd);
			return -1;
		} 

		if (*line == '#') {
			/* it's some sort of command that I don't understand */
			io_printf(fd,"ERROR: Unknown command '%s'\n", line);
			return -1;
		}

		i = lp_number(line);
		if (i == -1) {
			io_printf(fd,"ERROR: Unknown module '%s'\n", line);
			return -1;
		}

		return rsync_module(fd, i);
	}

	return 0;
}


static int daemon_main(void)
{
	if (!lp_load(RSYNCD_CONF)) {
		exit_cleanup(1);
	}

	if (is_a_socket(STDIN_FILENO)) {
		/* we are running via inetd */
		return start_daemon(STDIN_FILENO);
	}

	become_daemon();

	return start_accept_loop(port, start_daemon);
}

int main(int argc,char *argv[])
{

    signal(SIGUSR1, sigusr1_handler);

    starttime = time(NULL);
    am_root = (getuid() == 0);

    /* we set a 0 umask so that correct file permissions can be
       carried across */
    orig_umask = (int)umask(0);

    parse_arguments(argc, argv);

    argc -= optind;
    argv += optind;
    optind = 0;

    signal(SIGCHLD,SIG_IGN);
    signal(SIGINT,SIGNAL_CAST sig_int);
    signal(SIGPIPE,SIGNAL_CAST sig_int);
    signal(SIGHUP,SIGNAL_CAST sig_int);

    if (am_daemon) {
	    return daemon_main();
    }

    if (dry_run)
      verbose = MAX(verbose,1);

#ifndef SUPPORT_LINKS
    if (!am_server && preserve_links) {
	    rprintf(FERROR,"ERROR: symbolic links not supported\n");
	    exit_cleanup(1);
    }
#endif

    if (am_server) {
	    start_server(STDIN_FILENO, STDOUT_FILENO, argc, argv);
    }

    if (argc < 2) {
      usage(FERROR);
      exit_cleanup(1);
    }

    return start_client(argc, argv);
}

