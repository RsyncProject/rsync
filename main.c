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

time_t starttime = 0;
int64 total_size = 0;

extern int csum_length;

extern int verbose;

static void report(int f)
{
	int64 in,out,tsize;
	time_t t = time(NULL);
	extern int am_server;
	extern int am_sender;
	extern int am_daemon;

	if (am_daemon) {
		syslog(LOG_INFO,"wrote %.0f bytes  read %.0f bytes  total size %.0f\n",
		       (double)write_total(),(double)read_total(),
		       (double)total_size);
		if (f == -1 || !am_sender) return;
	}

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
		out = read_longint(f);
		in = read_longint(f);
		tsize = read_longint(f);
	}
	
	printf("wrote %.0f bytes  read %.0f bytes  %.2f bytes/sec\n",
	       (double)out,(double)in,(in+out)/(0.5 + (t-starttime)));
	printf("total size is %.0f  speedup is %.2f\n",
	       (double)tsize,(1.0*tsize)/(in+out));
}


static int do_cmd(char *cmd,char *machine,char *user,char *path,int *f_in,int *f_out)
{
	char *args[100];
	int i,argc=0, ret;
	char *tok,*dir=NULL;
	extern int local_server;
	extern char *rsync_path;

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
	extern int orig_umask;

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
	extern int relative_paths;
	extern int recurse;

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

	if (argc == 0 && recurse) {
		argc=1;
		argv--;
		argv[0] = ".";
	}
	
	flist = send_file_list(f_out,argc,argv);
	if (!flist || flist->count == 0) {
		exit_cleanup(0);
	}

	send_files(flist,f_out,f_in);
	report(f_out);
	io_flush();
	exit_cleanup(0);
}


static int do_recv(int f_in,int f_out,struct file_list *flist,char *local_name)
{
	int pid;
	int status=0;
	int recv_pipe[2];
	extern int preserve_hard_links;
	extern int am_server;

	if (preserve_hard_links)
		init_hard_links(flist);

	if (pipe(recv_pipe) < 0) {
		rprintf(FERROR,"pipe failed in do_recv\n");
		exit_cleanup(1);
	}
  
	io_flush();

	if ((pid=do_fork()) == 0) {
		close(recv_pipe[0]);
		if (f_in != f_out) close(f_out);

		recv_files(f_in,flist,local_name,recv_pipe[1]);
		if (!am_server)
			report(f_in);

		if (verbose > 3)
			rprintf(FINFO,"do_recv waiting on %d\n",pid);

		io_flush();
		_exit(0);
	}

	close(recv_pipe[1]);
	io_close_input(f_in);
	if (f_in != f_out) close(f_in);
	generate_files(f_out,flist,local_name,recv_pipe[0]);

	io_flush();
	waitpid(pid, &status, 0);
	return status;
}


static void do_server_recv(int f_in, int f_out, int argc,char *argv[])
{
	int status;
	struct file_list *flist;
	char *local_name=NULL;
	char *dir = NULL;
	extern int delete_mode;
	extern int am_daemon;

	if (verbose > 2)
		rprintf(FINFO,"server_recv(%d) starting pid=%d\n",argc,(int)getpid());
	
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
		rprintf(FERROR,"server_recv: nothing to do\n");
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
	extern int cvs_exclude;
	extern int am_sender;

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

int client_run(int f_in, int f_out, int pid, int argc, char *argv[])
{
	struct file_list *flist;
	int status = 0, status2 = 0;
	char *local_name = NULL;
	extern int am_sender;

	setup_protocol(f_out,f_in);
	
	if (am_sender) {
		extern int cvs_exclude;
		extern int delete_mode;
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
				rprintf(FINFO,"client_run waiting on %d\n",pid);
			io_flush();
			waitpid(pid, &status, 0);
		}
		report(-1);
		exit_cleanup(status);
	}
	
	send_exclude_list(f_out);
	
	flist = recv_file_list(f_in);
	if (!flist || flist->count == 0) {
		rprintf(FINFO,"client: nothing to do\n");
		exit_cleanup(0);
	}
	
	local_name = get_local_name(flist,argv[0]);
	
	status2 = do_recv(f_in,f_out,flist,local_name);
	
	if (pid != -1) {
		if (verbose > 3)
			rprintf(FINFO,"client_run2 waiting on %d\n",pid);
		io_flush();
		waitpid(pid, &status, 0);
	}
	
	return status | status2;
}


int start_client(int argc, char *argv[])
{
	char *p;
	char *shell_machine = NULL;
	char *shell_path = NULL;
	char *shell_user = NULL;
	int pid;
	int f_in,f_out;
	extern int local_server;
	extern int am_sender;
	extern char *shell_cmd;

	p = strchr(argv[0],':');

	if (p) {
		if (p[1] == ':') {
			*p = 0;
			return start_socket_client(argv[0], p+2, argc-1, argv+1);
		}

		if (argc < 2) {
			usage(FERROR);
			exit_cleanup(1);
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

		if (argc < 2) {
			usage(FERROR);
			exit_cleanup(1);
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


RETSIGTYPE sigusr1_handler(int val) {
	exit_cleanup(1);
}

int main(int argc,char *argv[])
{       
	extern int am_root;
	extern int orig_umask;
	extern int dry_run;
	extern int am_daemon;
	extern int am_server;

	signal(SIGUSR1, sigusr1_handler);

	starttime = time(NULL);
	am_root = (getuid() == 0);

	if (argc < 2) {
		usage(FERROR);
		exit_cleanup(1);
	}

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

	if (argc < 1) {
		usage(FERROR);
		exit_cleanup(1);
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

	return start_client(argc, argv);
}

