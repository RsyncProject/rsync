/* -*- c-file-style: "linux" -*-
   
   Copyright (C) 1996-2001 by Andrew Tridgell <tridge@samba.org>
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
   
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

extern struct stats stats;
extern int verbose;

/* there's probably never more than at most 2 outstanding child processes,
 * but set it higher just in case.
 */
#define MAXCHILDPROCS 5

struct pid_status {
	pid_t pid;
	int   status;
} pid_stat_table[MAXCHILDPROCS];

static void show_malloc_stats(void);

/****************************************************************************
wait for a process to exit, calling io_flush while waiting
****************************************************************************/
void wait_process(pid_t pid, int *status)
{
	pid_t waited_pid;
	int cnt;

	while ((waited_pid = waitpid(pid, status, WNOHANG)) == 0) {
		msleep(20);
		io_flush();
	}
        
	if ((waited_pid == -1) && (errno == ECHILD)) {
		/* status of requested child no longer available.
		 * check to see if it was processed by the sigchld_handler.
		 */
		for (cnt = 0;  cnt < MAXCHILDPROCS; cnt++) {
			if (pid == pid_stat_table[cnt].pid) {
				*status = pid_stat_table[cnt].status;
				pid_stat_table[cnt].pid = 0;
				break;
			}
		}
	}

        /* TODO: If the child exited on a signal, then log an
         * appropriate error message.  Perhaps we should also accept a
         * message describing the purpose of the child.  Also indicate
         * this to the caller so that thhey know something went
         * wrong.  */
	*status = WEXITSTATUS(*status);
}

static void report(int f)
{
	time_t t = time(NULL);
	extern int am_server;
	extern int am_sender;
	extern int am_daemon;
	extern int do_stats;
	extern int remote_version;
	int send_stats;

	if (do_stats) {
		/* These come out from every process */
		show_malloc_stats();
		show_flist_stats();
	}

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


/**
 * If our C library can get malloc statistics, then show them to FINFO
 **/
static void show_malloc_stats(void)
{
#ifdef HAVE_MALLINFO
	struct mallinfo mi;
	extern int am_server;
	extern int am_sender;
	extern int am_daemon;

	mi = mallinfo();

	rprintf(FINFO, RSYNC_NAME "[%d] (%s%s%s) heap statistics:\n",
		getpid(),
		am_server ? "server " : "",
		am_daemon ? "daemon " : "",
		am_sender ? "sender" : "receiver");
	rprintf(FINFO, "  arena:     %10d   (bytes from sbrk)\n", mi.arena);
	rprintf(FINFO, "  ordblks:   %10d   (chunks not in use)\n", mi.ordblks);
	rprintf(FINFO, "  smblks:    %10d\n", mi.smblks);
	rprintf(FINFO, "  hblks:     %10d   (chunks from mmap)\n", mi.hblks);
	rprintf(FINFO, "  hblkhd:    %10d   (bytes from mmap)\n", mi.hblkhd);
	rprintf(FINFO, "  usmblks:   %10d\n", mi.usmblks);
	rprintf(FINFO, "  fsmblks:   %10d\n", mi.fsmblks);
	rprintf(FINFO, "  uordblks:  %10d   (bytes used)\n", mi.uordblks);
	rprintf(FINFO, "  fordblks:  %10d   (bytes free)\n", mi.fordblks);
	rprintf(FINFO, "  keepcost:  %10d   (bytes in releasable chunk)\n", mi.keepcost);
#endif /* HAVE_MALLINFO */
}


/* Start the remote shell.   cmd may be NULL to use the default. */
static pid_t do_cmd(char *cmd,char *machine,char *user,char *path,int *f_in,int *f_out)
{
	char *args[100];
	int i,argc=0;
	pid_t ret;
	char *tok,*dir=NULL;
	int dash_l_set = 0;
	extern int local_server;
	extern char *rsync_path;
	extern int blocking_io;
	extern int daemon_over_rsh;
	extern int read_batch;

	if (!read_batch && !local_server) {
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

		/* check to see if we've already been given '-l user' in 
		   the remote-shell command */
		for (i = 0; i < argc-1; i++) {
			if (!strcmp(args[i], "-l") && args[i+1][0] != '-')
				dash_l_set = 1;
		}

#if HAVE_REMSH
		/* remsh (on HPUX) takes the arguments the other way around */
		args[argc++] = machine;
		if (user && !(daemon_over_rsh && dash_l_set)) {
			args[argc++] = "-l";
			args[argc++] = user;
		}
#else
		if (user && !(daemon_over_rsh && dash_l_set)) {
			args[argc++] = "-l";
			args[argc++] = user;
		}
		args[argc++] = machine;
#endif

		args[argc++] = rsync_path;

		if ((blocking_io == -1) && (strcmp(cmd, RSYNC_RSH) == 0))
			blocking_io = 1;

		server_options(args,&argc);

	}

	args[argc++] = ".";

	if (!daemon_over_rsh && path && *path) 
		args[argc++] = path;

	args[argc] = NULL;

	if (verbose > 3) {
		rprintf(FINFO,"cmd=");
		for (i=0;i<argc;i++)
			rprintf(FINFO,"%s ",args[i]);
		rprintf(FINFO,"\n");
	}

	if (local_server) {
		if (read_batch)
		    create_flist_from_batch(); /* sets batch_flist */
		ret = local_child(argc, args, f_in, f_out, child_main);
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
		rprintf(FERROR, RSYNC_NAME ": mkdir %s: %s\n",
			name, strerror(errno));
		exit_cleanup(RERR_FILEIO);
	} else {
		if (verbose > 0)
			rprintf(FINFO,"created directory %s\n",name);
	}

	if (!push_dir(name, 0)) {
		rprintf(FERROR, RSYNC_NAME ": push_dir %s: %s\n",
			name, strerror(errno));
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
	extern int remote_version;

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
	
	flist = send_file_list(f_out,argc,argv);
	if (!flist || flist->count == 0) {
		exit_cleanup(0);
	}

	send_files(flist,f_out,f_in);
	io_flush();
	report(f_out);
	if (remote_version >= 24) {
		/* final goodbye message */		
 		read_int(f_in);
 	}
	io_flush();
	exit_cleanup(0);
}


static int do_recv(int f_in,int f_out,struct file_list *flist,char *local_name)
{
	int pid;
	int status=0;
	int recv_pipe[2];
	int error_pipe[2];
	extern int preserve_hard_links;
	extern int delete_after;
	extern int recurse;
	extern int delete_mode;
	extern int remote_version;

	if (preserve_hard_links)
		init_hard_links(flist);

	if (!delete_after) {
		/* I moved this here from recv_files() to prevent a race condition */
		if (recurse && delete_mode && !local_name && flist->count>0) {
			delete_files(flist);
		}
	}

	if (fd_pair(recv_pipe) < 0) {
		rprintf(FERROR,"pipe failed in do_recv\n");
		exit_cleanup(RERR_SOCKETIO);
	}

	if (fd_pair(error_pipe) < 0) {
		rprintf(FERROR,"error pipe failed in do_recv\n");
		exit_cleanup(RERR_SOCKETIO);
	}
  
	io_flush();

	if ((pid=do_fork()) == 0) {
		close(recv_pipe[0]);
		close(error_pipe[0]);
		if (f_in != f_out) close(f_out);

		/* we can't let two processes write to the socket at one time */
		io_multiplexing_close();

		/* set place to send errors */
		set_error_fd(error_pipe[1]);

		recv_files(f_in,flist,local_name,recv_pipe[1]);
		io_flush();
		report(f_in);

		write_int(recv_pipe[1],1);
		close(recv_pipe[1]);
		io_flush();
		/* finally we go to sleep until our parent kills us
		   with a USR2 signal. We sleep for a short time as on
		   some OSes a signal won't interrupt a sleep! */
		while (msleep(20))
			;
	}

	close(recv_pipe[1]);
	close(error_pipe[1]);
	if (f_in != f_out) close(f_in);

	io_start_buffering(f_out);

	io_set_error_fd(error_pipe[0]);

	generate_files(f_out,flist,local_name,recv_pipe[0]);

	read_int(recv_pipe[0]);
	close(recv_pipe[0]);
	if (remote_version >= 24) {
		/* send a final goodbye message */
		write_int(f_out, -1);
	}
	io_flush();

	io_set_error_fd(-1);
	kill(pid, SIGUSR2);
	wait_process(pid, &status);
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
	extern int module_id;
	extern int am_sender;
	extern int read_batch;
	extern struct file_list *batch_flist;

	if (verbose > 2)
		rprintf(FINFO,"server_recv(%d) starting pid=%d\n",argc,(int)getpid());

	if (am_daemon && lp_read_only(module_id) && !am_sender) {
		rprintf(FERROR,"ERROR: module is read only\n");
		exit_cleanup(RERR_SYNTAX);
		return;
	}

	
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

	if (read_batch)
	    flist = batch_flist;
	else
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


int child_main(int argc, char *argv[])
{
	start_server(STDIN_FILENO, STDOUT_FILENO, argc, argv);
	return 0;
}


void start_server(int f_in, int f_out, int argc, char *argv[])
{
	extern int cvs_exclude;
	extern int am_sender;
	extern int remote_version;
	extern int read_batch;

	setup_protocol(f_out, f_in);

	set_nonblocking(f_in);
	set_nonblocking(f_out);

	if (remote_version >= 23)
		io_start_multiplex_out(f_out);

	if (am_sender) {
		if (!read_batch) {
		    recv_exclude_list(f_in);
		    if (cvs_exclude)
			add_cvs_excludes();
		}
		do_server_sender(f_in, f_out, argc, argv);
	} else {
		do_server_recv(f_in, f_out, argc, argv);
	}
	exit_cleanup(0);
}


/*
 * This is called once the connection has been negotiated.  It is used
 * for rsyncd, remote-shell, and local connections.
 */
int client_run(int f_in, int f_out, pid_t pid, int argc, char *argv[])
{
	struct file_list *flist = NULL;
	int status = 0, status2 = 0;
	char *local_name = NULL;
	extern int am_sender;
	extern int remote_version;
	extern pid_t cleanup_child_pid;
	extern int write_batch;
	extern int read_batch;
	extern struct file_list *batch_flist;

	cleanup_child_pid = pid;
	if (read_batch)
	    flist = batch_flist;

	set_nonblocking(f_in);
	set_nonblocking(f_out);

	setup_protocol(f_out,f_in);

	if (remote_version >= 23)
		io_start_multiplex_in(f_in);
	
	if (am_sender) {
		extern int cvs_exclude;
		extern int delete_mode;
		extern int delete_excluded;
		if (cvs_exclude)
			add_cvs_excludes();
		if (delete_mode && !delete_excluded) 
			send_exclude_list(f_out);
		if (!read_batch) /*  dw -- don't write to pipe */
		    flist = send_file_list(f_out,argc,argv);
		if (verbose > 3) 
			rprintf(FINFO,"file list sent\n");

		send_files(flist,f_out,f_in);
		if (remote_version >= 24) {
			/* final goodbye message */		
			read_int(f_in);
		}
		if (pid != -1) {
			if (verbose > 3)
				rprintf(FINFO,"client_run waiting on %d\n", (int) pid);
			io_flush();
			wait_process(pid, &status);
		}
		report(-1);
		exit_cleanup(status);
	}

	if (argc == 0) {
		extern int list_only;
		list_only = 1;
	}
	
	if (!write_batch)
	    send_exclude_list(f_out);
	
	flist = recv_file_list(f_in);
	if (!flist || flist->count == 0) {
		rprintf(FINFO, "client: nothing to do: "
                        "perhaps you need to specify some filenames or "
                        "the --recursive option?\n");
		exit_cleanup(0);
	}
	
	local_name = get_local_name(flist,argv[0]);
	
	status2 = do_recv(f_in,f_out,flist,local_name);
	
	if (pid != -1) {
		if (verbose > 3)
			rprintf(FINFO,"client_run2 waiting on %d\n", (int) pid);
		io_flush();
		wait_process(pid, &status);
	}
	
	return MAX(status, status2);
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


static int copy_argv (char *argv[])
{
	int i;

	for (i = 0; argv[i]; i++) {
		if (!(argv[i] = strdup(argv[i]))) {
			rprintf (FERROR, "out of memory at %s(%d)\n",
				 __FILE__, __LINE__);
			return RERR_MALLOC;
		}
	}

	return 0;
}


/**
 * Start a client for either type of remote connection.  Work out
 * whether the arguments request a remote shell or rsyncd connection,
 * and call the appropriate connection function, then run_client.
 *
 * Calls either start_socket_client (for sockets) or do_cmd and
 * client_run (for ssh).
 **/
static int start_client(int argc, char *argv[])
{
	char *p;
	char *shell_machine = NULL;
	char *shell_path = NULL;
	char *shell_user = NULL;
	int ret;
	pid_t pid;
	int f_in,f_out;
	extern int local_server;
	extern int am_sender;
	extern char *shell_cmd;
	extern int rsync_port;
	extern int daemon_over_rsh;
	extern int read_batch;
	int rc;

	/* Don't clobber argv[] so that ps(1) can still show the right
           command line. */
	if ((rc = copy_argv(argv)))
		return rc;

	/* rsync:// always uses rsync server over direct socket connection */
	if (strncasecmp(URL_PREFIX, argv[0], strlen(URL_PREFIX)) == 0) {
		char *host, *path;

		host = argv[0] + strlen(URL_PREFIX);
		p = strchr(host,'/');
		if (p) {
			*p = 0;
			path = p+1;
		} else {
			path = "";
		}
		p = strchr(host,':');
		if (p) {
			rsync_port = atoi(p+1);
			*p = 0;
		}
		return start_socket_client(host, path, argc-1, argv+1);
	}

	if (!read_batch) {
		p = find_colon(argv[0]);

	if (p) {
		if (p[1] == ':') { /* double colon */
			*p = 0;
			if (!shell_cmd) {
				return start_socket_client(argv[0], p+2,
							   argc-1, argv+1);
			}
			p++;
			daemon_over_rsh = 1;
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

		/* rsync:// destination uses rsync server over direct socket */
		if (strncasecmp(URL_PREFIX, argv[argc-1], strlen(URL_PREFIX)) == 0) {
			char *host, *path;

			host = argv[argc-1] + strlen(URL_PREFIX);
			p = strchr(host,'/');
			if (p) {
				*p = 0;
				path = p+1;
			} else {
				path = "";
			}
			p = strchr(host,':');
			if (p) {
				rsync_port = atoi(p+1);
				*p = 0;
			}
			return start_socket_client(host, path, argc-1, argv);
		}

		p = find_colon(argv[argc-1]);
		if (!p) {
			local_server = 1;
		} else if (p[1] == ':') { /* double colon */
			*p = 0;
			if (!shell_cmd) {
				return start_socket_client(argv[argc-1], p+2,
							   argc-1, argv);
			}
			p++;
			daemon_over_rsh = 1;
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
	} else {
	    am_sender = 1;
	    local_server = 1;
	    shell_path = argv[argc-1];
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

	if (argc == 0 && !am_sender) {
		extern int list_only;
		list_only = 1;
	}
	
	pid = do_cmd(shell_cmd,shell_machine,shell_user,shell_path,
		     &f_in,&f_out);

	/* if we're running an rsync server on the remote host over a
	   remote shell command, we need to do the RSYNCD protocol first */
	if (daemon_over_rsh) {
		int tmpret;
		tmpret = start_inband_exchange(shell_user, shell_path,
					       f_in, f_out, argc);
		if (tmpret < 0)
			return tmpret;
	}

	ret = client_run(f_in, f_out, pid, argc, argv);

	fflush(stdout);
	fflush(stderr);

	return ret;
}


static RETSIGTYPE sigusr1_handler(int UNUSED(val)) {
	exit_cleanup(RERR_SIGNAL);
}

static RETSIGTYPE sigusr2_handler(int UNUSED(val)) {
	extern int log_got_error;
	if (log_got_error) _exit(RERR_PARTIAL);
	_exit(0);
}

static RETSIGTYPE sigchld_handler(int UNUSED(val)) {
#ifdef WNOHANG
	int cnt, status;
	pid_t pid;
	/* An empty waitpid() loop was put here by Tridge and we could never
	 * get him to explain why he put it in, so rather than taking it 
	 * out we're instead saving the child exit statuses for later use.
	 * The waitpid() loop presumably eliminates all possibility of leaving
	 * zombie children, maybe that's why he did it.
	 */
	while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
		 /* save the child's exit status */
		 for (cnt = 0; cnt < MAXCHILDPROCS; cnt++) {
			  if (pid_stat_table[cnt].pid == 0) {
				   pid_stat_table[cnt].pid = pid;
				   pid_stat_table[cnt].status = status;
				   break;
			  }
		 }
	}
#endif
}


/**
 * This routine catches signals and tries to send them to gdb.
 *
 * Because it's called from inside a signal handler it ought not to
 * use too many library routines.
 *
 * @todo Perhaps use "screen -X" instead/as well, to help people
 * debugging without easy access to X.  Perhaps use an environment
 * variable, or just call a script?
 *
 * @todo The /proc/ magic probably only works on Linux (and
 * Solaris?)  Can we be more portable?
 **/
#ifdef MAINTAINER_MODE
const char *get_panic_action(void)
{
	const char *cmd_fmt = getenv("RSYNC_PANIC_ACTION");

	if (cmd_fmt)
		return cmd_fmt;
	else
		return "xterm -display :0 -T Panic -n Panic "
			"-e gdb /proc/%d/exe %d";
}


/**
 * Handle a fatal signal by launching a debugger, controlled by $RSYNC_PANIC_ACTION.
 *
 * This signal handler is only installed if we were configured with
 * --enable-maintainer-mode.  Perhaps it should always be on and we
 * should just look at the environment variable, but I'm a bit leery
 * of a signal sending us into a busy loop.
 **/
static RETSIGTYPE rsync_panic_handler(int UNUSED(whatsig))
{
	char cmd_buf[300];
	int ret;

	sprintf(cmd_buf, get_panic_action(),
		getpid(), getpid());

	/* Unless we failed to execute gdb, we allow the process to
	 * continue.  I'm not sure if that's right. */
	ret = system(cmd_buf);
	if (ret)
		_exit(ret);
}
#endif


int main(int argc,char *argv[])
{       
	extern int am_root;
	extern int orig_umask;
	extern int dry_run;
	extern int am_daemon;
	extern int am_server;
	int ret;
	extern int write_batch;
	int orig_argc;
	char **orig_argv;

	orig_argc = argc;
	orig_argv = argv;

	signal(SIGUSR1, sigusr1_handler);
	signal(SIGUSR2, sigusr2_handler);
	signal(SIGCHLD, sigchld_handler);
#ifdef MAINTAINER_MODE
	signal(SIGSEGV, rsync_panic_handler);
	signal(SIGFPE, rsync_panic_handler);
	signal(SIGABRT, rsync_panic_handler);
	signal(SIGBUS, rsync_panic_handler);
#endif /* def MAINTAINER_MODE */

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

	if (!parse_arguments(&argc, (const char ***) &argv, 1)) {
                /* FIXME: We ought to call the same error-handling
                 * code here, rather than relying on getopt. */
		option_error();
		exit_cleanup(RERR_SYNTAX);
	}

	signal(SIGINT,SIGNAL_CAST sig_int);
	signal(SIGHUP,SIGNAL_CAST sig_int);
	signal(SIGTERM,SIGNAL_CAST sig_int);

	/* Ignore SIGPIPE; we consistently check error codes and will
	 * see the EPIPE. */
	signal(SIGPIPE, SIG_IGN);

	/* Initialize push_dir here because on some old systems getcwd
	   (implemented by forking "pwd" and reading its output) doesn't
	   work when there are other child processes.  Also, on all systems
	   that implement getcwd that way "pwd" can't be found after chroot. */
	push_dir(NULL,0);

	if (write_batch && !am_server) {
	    write_batch_argvs_file(orig_argc, orig_argv);
	}

	if (am_daemon && !am_server)
		return daemon_main();

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
		set_nonblocking(STDIN_FILENO);
		set_nonblocking(STDOUT_FILENO);
		if (am_daemon)
			return start_daemon(STDIN_FILENO, STDOUT_FILENO);
		start_server(STDIN_FILENO, STDOUT_FILENO, argc, argv);
	}

	ret = start_client(argc, argv);
	if (ret == -1) 
		exit_cleanup(RERR_STARTCLIENT);
	else
		exit_cleanup(ret);

	exit(ret);
	/* NOTREACHED */
}
