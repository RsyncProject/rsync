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
time_t starttime;
off_t total_size = 0;
int block_size=BLOCK_SIZE;

char *backup_suffix = BACKUP_SUFFIX;

static char *rsync_path = RSYNC_NAME;

int make_backups = 0;
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

extern int csum_length;

int am_server = 0;
static int sender = 0;
int recurse = 0;

static void usage(FILE *f);

static void report(int f)
{
  int in,out,tsize;
  time_t t = time(NULL);
  
  if (!verbose) return;

  if (am_server && sender) {
    write_int(f,read_total());
    write_int(f,write_total());
    write_int(f,total_size);
    write_flush(f);
    return;
  }
    
  if (sender) {
    in = read_total();
    out = write_total();
    tsize = (int)total_size;
  } else {
    in = read_int(f);
    out = read_int(f);
    tsize = read_int(f);
  }

  printf("wrote %d bytes  read %d bytes  %g bytes/sec\n",
	 out,in,(in+out)/(0.5 + (t-starttime)));        
  printf("total size is %d  speedup is %g\n",
	 tsize,(1.0*tsize)/(in+out));
}


static void server_options(char **args,int *argc)
{
  int ac = *argc;
  static char argstr[50];
  static char bsize[30];
  int i, x;

  args[ac++] = "--server";

  if (!sender)
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

  if (delete_mode)
    args[ac++] = "--delete";

  *argc = ac;
}



int do_cmd(char *cmd,char *machine,char *user,char *path,int *f_in,int *f_out)
{
  char *args[100];
  int i,argc=0;
  char *tok,*p;

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
  }

  args[argc++] = rsync_path;

  server_options(args,&argc);

  if (path && *path) {
    char *dir = strdup(path);
    p = strrchr(dir,'/');
    if (p && !relative_paths) {
      *p = 0;
      if (!dir[0])
	args[argc++] = "/";
      else
	args[argc++] = dir;
      p++;
    } else {
      args[argc++] = ".";
      p = dir;
    }
    if (p[0])
      args[argc++] = path;
  }

  args[argc] = NULL;

  if (verbose > 3) {
    fprintf(FERROR,"cmd=");
    for (i=0;i<argc;i++)
      fprintf(FERROR,"%s ",args[i]);
    fprintf(FERROR,"\n");
  }

  return piped_child(args,f_in,f_out);

oom:
  out_of_memory("do_cmd");
  return 0; /* not reached */
}




static char *get_local_name(struct file_list *flist,char *name)
{
  struct stat st;

  if (stat(name,&st) == 0) {
    if (S_ISDIR(st.st_mode)) {
      if (chdir(name) != 0) {
	fprintf(FERROR,"chdir %s : %s (1)\n",name,strerror(errno));
	exit_cleanup(1);
      }
      return NULL;
    }
    if (flist->count > 1) {
      fprintf(FERROR,"ERROR: destination must be a directory when copying more than 1 file\n");
      exit_cleanup(1);
    }
    return name;
  }

  if (flist->count == 1)
    return name;

  if (!name) 
    return NULL;

  if (mkdir(name,0777 & ~orig_umask) != 0) {
    fprintf(FERROR,"mkdir %s : %s (1)\n",name,strerror(errno));
    exit_cleanup(1);
  } else {
    fprintf(FINFO,"created directory %s\n",name);
  }

  if (chdir(name) != 0) {
    fprintf(FERROR,"chdir %s : %s (2)\n",name,strerror(errno));
    exit_cleanup(1);
  }

  return NULL;
}




void do_server_sender(int argc,char *argv[])
{
  int i;
  char *dir = argv[0];
  struct file_list *flist;

  if (verbose > 2)
    fprintf(FERROR,"server_sender starting pid=%d\n",(int)getpid());
  
  if (!relative_paths && chdir(dir) != 0) {
    fprintf(FERROR,"chdir %s: %s (3)\n",dir,strerror(errno));
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

  if (argc == 0 && recurse) {
    argc=1;
    argv--;
    argv[0] = ".";
  }
    

  flist = send_file_list(STDOUT_FILENO,argc,argv);
  send_files(flist,STDOUT_FILENO,STDIN_FILENO);
  report(STDOUT_FILENO);
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
    fprintf(FERROR,"pipe failed in do_recv\n");
    exit(1);
  }
  

  if ((pid=fork()) == 0) {
    recv_files(f_in,flist,local_name,recv_pipe[1]);
    if (verbose > 2)
      fprintf(FERROR,"receiver read %d\n",read_total());
    exit_cleanup(0);
  }

  generate_files(f_out,flist,local_name,recv_pipe[0]);

  waitpid(pid, &status, 0);

  return status;
}


void do_server_recv(int argc,char *argv[])
{
  int status;
  char *dir = NULL;
  struct file_list *flist;
  char *local_name=NULL;
  
  if (verbose > 2)
    fprintf(FERROR,"server_recv(%d) starting pid=%d\n",argc,(int)getpid());

  if (argc > 0) {
    dir = argv[0];
    argc--;
    argv++;
    if (chdir(dir) != 0) {
      fprintf(FERROR,"chdir %s : %s (4)\n",dir,strerror(errno));
      exit_cleanup(1);
    }    
  }

  if (delete_mode)
    recv_exclude_list(STDIN_FILENO);

  flist = recv_file_list(STDIN_FILENO);
  if (!flist || flist->count == 0) {
    fprintf(FERROR,"nothing to do\n");
    exit_cleanup(1);
  }

  if (argc > 0) {    
    if (strcmp(dir,".")) {
      argv[0] += strlen(dir);
      if (argv[0][0] == '/') argv[0]++;
    }
    local_name = get_local_name(flist,argv[0]);
  }

  status = do_recv(STDIN_FILENO,STDOUT_FILENO,flist,local_name);
  exit_cleanup(status);
}


static void usage(FILE *f)
{
  fprintf(f,"rsync version %s Copyright Andrew Tridgell and Paul Mackerras\n\n",
	  VERSION);
  fprintf(f,"Usage:\t%s [options] src user@host:dest\nOR",RSYNC_NAME);
  fprintf(f,"\t%s [options] user@host:src dest\n\n",RSYNC_NAME);
  fprintf(f,"Options:\n");
  fprintf(f,"-v, --verbose            increase verbosity\n");
  fprintf(f,"-c, --checksum           always checksum\n");
  fprintf(f,"-a, --archive            archive mode (same as -rlptDog)\n");
  fprintf(f,"-r, --recursive          recurse into directories\n");
  fprintf(f,"-R, --relative           use relative path names\n");
  fprintf(f,"-b, --backup             make backups (default ~ extension)\n");
  fprintf(f,"-u, --update             update only (don't overwrite newer files)\n");
  fprintf(f,"-l, --links              preserve soft links\n");
  fprintf(f,"-H, --hard-links         preserve hard links\n");
  fprintf(f,"-p, --perms              preserve permissions\n");
  fprintf(f,"-o, --owner              preserve owner (root only)\n");
  fprintf(f,"-g, --group              preserve group\n");
  fprintf(f,"-D, --devices            preserve devices (root only)\n");
  fprintf(f,"-t, --times              preserve times\n");  
  fprintf(f,"-S, --sparse             handle sparse files efficiently\n");
  fprintf(f,"-n, --dry-run            show what would have been transferred\n");
  fprintf(f,"-x, --one-file-system    don't cross filesystem boundaries\n");
  fprintf(f,"-B, --block-size SIZE    checksum blocking size\n");  
  fprintf(f,"-e, --rsh COMMAND        specify rsh replacement\n");
  fprintf(f,"    --rsync-path PATH    specify path to rsync on the remote machine\n");
  fprintf(f,"-C, --cvs-exclude        auto ignore files in the same way CVS does\n");
  fprintf(f,"    --delete             delete files that don't exist on the sending side\n");
  fprintf(f,"-I, --ignore-times       don't exclude files that match length and time\n");
  fprintf(f,"-z, --compress           compress file data\n");
  fprintf(f,"    --exclude FILE       exclude file FILE\n");
  fprintf(f,"    --exclude-from FILE  exclude files listed in FILE\n");
  fprintf(f,"    --suffix SUFFIX      override backup suffix\n");  
  fprintf(f,"    --version            print version number\n");  

  fprintf(f,"\n");
  fprintf(f,"the backup suffix defaults to %s\n",BACKUP_SUFFIX);
  fprintf(f,"the block size defaults to %d\n",BLOCK_SIZE);  
}

enum {OPT_VERSION,OPT_SUFFIX,OPT_SENDER,OPT_SERVER,OPT_EXCLUDE,
      OPT_EXCLUDE_FROM,OPT_DELETE,OPT_RSYNC_PATH};

static char *short_options = "oblHpguDCtcahvrRIxnSe:B:z";

static struct option long_options[] = {
  {"version",     0,     0,    OPT_VERSION},
  {"server",      0,     0,    OPT_SERVER},
  {"sender",      0,     0,    OPT_SENDER},
  {"delete",      0,     0,    OPT_DELETE},
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
  {"hard-links",  0,     0,    'H'},
  {"owner",       0,     0,    'o'},
  {"group",       0,     0,    'g'},
  {"times",       0,     0,    't'},
  {"rsh",         1,     0,    'e'},
  {"suffix",      1,     0,    OPT_SUFFIX},
  {"block-size",  1,     0,    'B'},
  {"compress",	  0,	 0,    'z'},
  {0,0,0,0}};

int main(int argc,char *argv[])
{
    int pid, status = 0, status2 = 0;
    int opt;
    int option_index;
    char *shell_cmd = NULL;
    char *shell_machine = NULL;
    char *shell_path = NULL;
    char *shell_user = NULL;
    char *p;
    int f_in,f_out;
    struct file_list *flist;
    char *local_name = NULL;

    starttime = time(NULL);
    am_root = (getuid() == 0);

    /* we set a 0 umask so that correct file permissions can be
       carried across */
    orig_umask = umask(0);

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
#if SUPPORT_LINKS
	  preserve_links=1;
#endif
	  break;

	case 'H':
#if SUPPORT_HARD_LINKS
	  preserve_hard_links=1;
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
	  sender = 1;
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

        case 'z':
	  do_compression = 1;
	  break;

	default:
	  /* fprintf(FERROR,"bad option -%c\n",opt); */
	  exit_cleanup(1);
	}
    }

    while (optind--) {
      argc--;
      argv++;
    }

    signal(SIGCHLD,SIG_IGN);
    signal(SIGINT,SIGNAL_CAST sig_int);
    signal(SIGPIPE,SIGNAL_CAST sig_int);
    signal(SIGHUP,SIGNAL_CAST sig_int);

    if (dry_run)
      verbose = MAX(verbose,1);

    if (am_server) {
      setup_protocol(STDOUT_FILENO,STDIN_FILENO);
	
      if (sender) {
	recv_exclude_list(STDIN_FILENO);
	if (cvs_exclude)
	  add_cvs_excludes();
	do_server_sender(argc,argv);
      } else {
	do_server_recv(argc,argv);
      }
      exit_cleanup(0);
    }

    if (argc < 2) {
      usage(FERROR);
      exit_cleanup(1);
    }

    p = strchr(argv[0],':');

    if (p) {
      sender = 0;
      *p = 0;
      shell_machine = argv[0];
      shell_path = p+1;
      argc--;
      argv++;
    } else {
      sender = 1;

      p = strchr(argv[argc-1],':');
      if (!p) {
	local_server = 1;
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
      fprintf(FERROR,"cmd=%s machine=%s user=%s path=%s\n",
	      shell_cmd?shell_cmd:"",
	      shell_machine?shell_machine:"",
	      shell_user?shell_user:"",
	      shell_path?shell_path:"");
    }
    
    if (!sender && argc != 1) {
      usage(FERROR);
      exit_cleanup(1);
    }

    pid = do_cmd(shell_cmd,shell_machine,shell_user,shell_path,&f_in,&f_out);

    setup_protocol(f_out,f_in);

    setlinebuf(FINFO);
    setlinebuf(FERROR);

    if (verbose > 3) 
      fprintf(FERROR,"parent=%d child=%d sender=%d recurse=%d\n",
	      (int)getpid(),pid,sender,recurse);

    if (sender) {
      if (cvs_exclude)
	add_cvs_excludes();
      if (delete_mode) 
	send_exclude_list(f_out);
      flist = send_file_list(f_out,argc,argv);
      if (verbose > 3) 
	fprintf(FERROR,"file list sent\n");
      send_files(flist,f_out,f_in);
      if (verbose > 3)
	fprintf(FERROR,"waiting on %d\n",pid);
      waitpid(pid, &status, 0);
      report(-1);
      exit_cleanup(status);
    }

    send_exclude_list(f_out);

    flist = recv_file_list(f_in);
    if (!flist || flist->count == 0) {
      fprintf(FERROR,"nothing to do\n");
      exit_cleanup(0);
    }

    local_name = get_local_name(flist,argv[0]);

    status2 = do_recv(f_in,f_out,flist,local_name);

    report(f_in);

    waitpid(pid, &status, 0);

    return status | status2;
}
