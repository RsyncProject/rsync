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

struct stats stats;

extern int csum_length;

extern int verbose;

static void report(int f)
{
	time_t t = time(NULL);
	extern int am_server;
	extern int am_sender;
	extern int am_daemon;
	extern int do_stats;
	extern int remote_version;
	int send_stats;

	if (am_daemon) {
		log_exit(0, __FILE__, __LINE__);
		if (f == -1 || !am_sender) return;
	}

	send_stats = verbose || (remote_version >= 20);
	if (am_server) {
		if (am_sender && send_stats) {
			int64 w;
			/* store total_written in a temporary
			    because write_longint changes it */
			w = stats.total_written;
			write_longint(f,stats.total_read);
			write_longint(f,w);
			write_longint(f,stats.total_size);
		}
		return;
	}

	/* this is the client */
	    
	if (!am_sender && send_stats) {
		int64 r;
		stats.total_written = read_longint(f);
		/* store total_read in a temporary, read_longint changes it */
		r = read_longint(f);
		stats.total_size = read_longint(f);
		stats.total_read = r;
	}

	if (do_stats) {
		if (!am_sender && !send_stats) {
		    /* missing the bytes written by the generator */
		    rprintf(FINFO, "\nCannot show stats as receiver because remote protocol version is less than 20\n");
		    rprintf(FINFO, "Use --stats -v to show stats\n");
		    return;
		}
		rprintf(FINFO,"\nNumber of files: %d\n", stats.num_files);
		rprintf(FINFO,"Number of files transferred: %d\n", 
		       stats.num_transferred_files);
		rprintf(FINFO,"Total file size: %.0f bytes\n", 
		       (double)stats.total_size);
		rprintf(FINFO,"Total transferred file size: %.0f bytes\n", 
		       (double)stats.total_transferred_size);
		rprintf(FINFO,"Literal data: %.0f bytes\n", 
		       (double)stats.literal_data);
		rprintf(FINFO,"Matched data: %.0f bytes\n", 
		       (double)stats.matched_data);
		rprintf(FINFO,"File list size: %d\n", stats.flist_size);
		rprintf(FINFO,"Total bytes written: %.0f\n", 
		       (double)stats.total_written);
		rprintf(FINFO,"Total bytes read: %.0f\n\n", 
		       (double)stats.total_read);
	}
	
	if (verbose || do_stats) {
		rprintf(FINFO,"wrote %.0f bytes  read %.0f bytes  %.2f bytes/sec\n",
		       (double)stats.total_written,
		       (double)stats.total_read,
		       (stats.total_written+stats.total_read)/(0.5 + (t-starttime)));
		rprintf(FINFO,"total size is %.0f  speedup is %.2f\n",
		       (double)stats.total_size,
		       (1.0*stats.total_size)/(stats.total_written+stats.total_read));
	}

	fflush(stdout);
	fflush(stderr);
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

	if (verbose > 2)
		rprintf(FINFO,"get_local_name count=%d %s\n", 
			flist->count, NS(name));

	if (!name) 
		return NULL;

	if (do_stat(name,&st) == 0) {
		if (S_ISDIR(st.st_mode)) {
			if (!push_dir(name, 0)) {
				rprintf(FERROR,"push_dir %s : %s (1)\n",
					name,strerror(errno));
				exit_cleanup(RERR_FILESELECT);
			}
			return NULL;
		}
		if (flist->count > 1) {
			rprintf(FERROR,"ERROR: destination must be a directory when copying more than 1 file\n");
			exit_cleanup(RERR_FILESELECT);
		}
		return name;
	}

	if (flist->count <= 1)
		return name;

	if (do_mkdir(name,0777 & ~orig_umask) != 0) {
		rprintf(FERROR,"mkdir %s : %s (1)\n",name,strerror(errno));
		exit_cleanup(RERR_FILEIO);
	} else {
		if (verbose > 0)
			rprintf(FINFO,"created directory %s\n",name);
	}

	if (!push_dir(name, 0)) {
		rprintf(FERROR,"push_dir %s : %s (2)\n",
			name,strerror(errno));
		exit_cleanup(RERR_FILESELECT);
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
  
	if (!relative_paths && !push_dir(dir, 0)) {
		rprintf(FERROR,"push_dir %s: %s (3)\n",dir,strerror(errno));
		exit_cleanup(RERR_FILESELECT);
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
	
	set_nonblocking(f_out);
	if (f_in != f_out)
		set_nonblocking(f_in);
		
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

	if (preserve_hard_links)
		init_hard_links(flist);

	if (pipe(recv_pipe) < 0) {
		rprintf(FERROR,"pipe failed in do_recv\n");
		exit_cleanup(RERR_SOCKETIO);
	}
  
	io_flush();

	if ((pid=do_fork()) == 0) {
		close(recv_pipe[0]);
		if (f_in != f_out) close(f_out);

		set_nonblocking(f_in);
		set_nonblocking(recv_pipe[1]);

		recv_files(f_in,flist,local_name,recv_pipe[1]);
		report(f_in);

		io_flush();
		_exit(0);
	}

	close(recv_pipe[1]);
	io_close_input(f_in);
	if (f_in != f_out) close(f_in);

	set_nonblocking(f_out);
	set_nonblocking(recv_pipe[0]);

	io_start_buffering(f_out);

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
	extern int delete_excluded;
	extern int am_daemon;

	if (verbose > 2)
		rprintf(FINFO,"server_recv(%d) starting pid=%d\n",argc,(int)getpid());
	
	if (argc > 0) {
		dir = argv[0];
		argc--;
		argv++;
		if (!am_daemon && !push_dir(dir, 0)) {
			rprintf(FERROR,"push_dir %s : %s (4)\n",
				dir,strerror(errno));
			exit_cleanup(RERR_FILESELECT);
		}    
	}

	if (delete_mode && !delete_excluded)
		recv_exclude_list(f_in);

	flist = recv_file_list(f_in);
	if (!flist) {
		rprintf(FERROR,"server_recv: recv_file_list error\n");
		exit_cleanup(RERR_FILESELECT);
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

	set_nonblocking(f_out);
	if (f_in != f_out)
		set_nonblocking(f_in);
			
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
	extern int list_only;

	setup_protocol(f_out,f_in);
	
	if (am_sender) {
		extern int cvs_exclude;
		extern int delete_mode;
		extern int delete_excluded;
		if (cvs_exclude)
			add_cvs_excludes();
		if (delete_mode && !delete_excluded) 
			send_exclude_list(f_out);
		flist = send_file_list(f_out,argc,argv);
		if (verbose > 3) 
			rprintf(FINFO,"file list sent\n");

		set_nonblocking(f_out);
		if (f_in != f_out)
			set_nonblocking(f_in);

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

	if (argc == 0) list_only = 1;
	
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

static char *find_colon(char *s)
{
	char *p, *p2;

	p = strchr(s,':');
	if (!p) return NULL;
	
	/* now check to see if there is a / in the string before the : - if there is then
	   discard the colon on the assumption that the : is part of a filename */
	p2 = strchr(s,'/');
	if (p2 && p2 < p) return NULL;

	return p;
}

static int start_client(int argc, char *argv[])
{
	char *p;
	char *shell_machine = NULL;
	char *shell_path = NULL;
	char *shell_user = NULL;
	int pid, ret;
	int f_in,f_out;
	extern int local_server;
	extern int am_sender;
	extern char *shell_cmd;
	extern int rsync_port;

	if (strncasecmp(URL_PREFIX, argv[0], strlen(URL_PREFIX)) == 0) {
		char *host, *path;

		host = argv[0] + strlen(URL_PREFIX);
		p = strchr(host,'/');
		if (p) {
			*p = 0;
			path = p+1;
		} else {
			path="";
		}
		p = strchr(host,':');
		if (p) {
			rsync_port = atoi(p+1);
			*p = 0;
		}
		return start_socket_client(host, path, argc-1, argv+1);
	}

	p = find_colon(argv[0]);

	if (p) {
		if (p[1] == ':') {
			*p = 0;
			return start_socket_client(argv[0], p+2, argc-1, argv+1);
		}

		if (argc < 1) {
			usage(FERROR);
			exit_cleanup(RERR_SYNTAX);
		}

		am_sender = 0;
		*p = 0;
		shell_machine = argv[0];
		shell_path = p+1;
		argc--;
		argv++;
	} else {
		am_sender = 1;

		p = find_colon(argv[argc-1]);
		if (!p) {
			local_server = 1;
		} else if (p[1] == ':') {
			*p = 0;
			return start_socket_client(argv[argc-1], p+2, argc-1, argv);
		}

		if (argc < 2) {
			usage(FERROR);
			exit_cleanup(RERR_SYNTAX);
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
	
	if (!am_sender && argc > 1) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}
	
	pid = do_cmd(shell_cmd,shell_machine,shell_user,shell_path,&f_in,&f_out);
	
	ret = client_run(f_in, f_out, pid, argc, argv);

	fflush(stdout);
	fflush(stderr);

	return ret;
}


static RETSIGTYPE sigusr1_handler(int val) {
	exit_cleanup(RERR_SIGNAL);
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

	memset(&stats, 0, sizeof(stats));

	if (argc < 2) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

	/* we set a 0 umask so that correct file permissions can be
	   carried across */
	orig_umask = (int)umask(0);

	if (!parse_arguments(argc, argv, 1)) {
		exit_cleanup(RERR_SYNTAX);
	}

	argc -= optind;
	argv += optind;
	optind = 0;

	signal(SIGCHLD,SIG_IGN);
	signal(SIGINT,SIGNAL_CAST sig_int);
	signal(SIGPIPE,SIGNAL_CAST sig_int);
	signal(SIGHUP,SIGNAL_CAST sig_int);
	signal(SIGTERM,SIGNAL_CAST sig_int);

	/* Initialize push_dir here because on some old systems getcwd
	   (implemented by forking "pwd" and reading its output) doesn't
	   work when there are other child processes.  Also, on all systems
	   that implement getcwd that way "pwd" can't be found after chroot. */
	push_dir(NULL,0);

	if (am_daemon) {
		return daemon_main();
	}

	if (argc < 1) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

	if (dry_run)
		verbose = MAX(verbose,1);

#ifndef SUPPORT_LINKS
	if (!am_server && preserve_links) {
		rprintf(FERROR,"ERROR: symbolic links not supported\n");
		exit_cleanup(RERR_UNSUPPORTED);
	}
#endif

	if (am_server) {
		start_server(STDIN_FILENO, STDOUT_FILENO, argc, argv);
	}

	return start_client(argc, argv);
}

