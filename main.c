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
#if defined CONFIG_LOCALE && defined HAVE_LOCALE_H
#include <locale.h>
#endif

extern int verbose;
extern int dry_run;
extern int list_only;
extern int am_root;
extern int am_server;
extern int am_sender;
extern int am_generator;
extern int am_daemon;
extern int blocking_io;
extern int remove_sent_files;
extern int daemon_over_rsh;
extern int need_messages_from_generator;
extern int kluge_around_eof;
extern int do_stats;
extern int log_got_error;
extern int module_id;
extern int orig_umask;
extern int copy_links;
extern int keep_dirlinks;
extern int preserve_hard_links;
extern int protocol_version;
extern int recurse;
extern int fuzzy_basis;
extern int relative_paths;
extern int rsync_port;
extern int inplace;
extern int make_backups;
extern int whole_file;
extern int read_batch;
extern int write_batch;
extern int batch_fd;
extern int batch_gen_fd;
extern int filesfrom_fd;
extern pid_t cleanup_child_pid;
extern struct stats stats;
extern char *filesfrom_host;
extern char *partial_dir;
extern char *basis_dir[];
extern char *rsync_path;
extern char *shell_cmd;
extern char *batch_name;

int local_server = 0;
struct file_list *the_file_list;

/* There's probably never more than at most 2 outstanding child processes,
 * but set it higher, just in case. */
#define MAXCHILDPROCS 5

struct pid_status {
	pid_t pid;
	int   status;
} pid_stat_table[MAXCHILDPROCS];

static time_t starttime, endtime;
static int64 total_read, total_written;

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
		io_flush(FULL_FLUSH);
	}

	if (waited_pid == -1 && errno == ECHILD) {
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

/* This function gets called from all 3 processes.  We want the client side
 * to actually output the text, but the sender is the only process that has
 * all the stats we need.  So, if we're a client sender, we do the report.
 * If we're a server sender, we write the stats on the supplied fd.  If
 * we're the client receiver we read the stats from the supplied fd and do
 * the report.  All processes might also generate a set of debug stats, if
 * the verbose level is high enough (this is the only thing that the
 * generator process and the server receiver ever do here). */
static void handle_stats(int f)
{
	endtime = time(NULL);

	/* Cache two stats because the read/write code can change it. */
	total_read = stats.total_read;
	total_written = stats.total_written;

	if (do_stats && verbose > 1) {
		/* These come out from every process */
		show_malloc_stats();
		show_flist_stats();
	}

	if (am_generator)
		return;

	if (am_daemon) {
		log_exit(0, __FILE__, __LINE__);
		if (f == -1 || !am_sender)
			return;
	}

	if (am_server) {
		if (am_sender) {
			write_longint(f, total_read);
			write_longint(f, total_written);
			write_longint(f, stats.total_size);
			if (protocol_version >= 29) {
				write_longint(f, stats.flist_buildtime);
				write_longint(f, stats.flist_xfertime);
			}
		}
		return;
	}

	/* this is the client */

	if (f < 0 && !am_sender) /* e.g. when we got an empty file list. */
		; 
	else if (!am_sender) {
		/* Read the first two in opposite order because the meaning of
		 * read/write swaps when switching from sender to receiver. */
		total_written = read_longint(f);
		total_read = read_longint(f);
		stats.total_size = read_longint(f);
		if (protocol_version >= 29) {
			stats.flist_buildtime = read_longint(f);
			stats.flist_xfertime = read_longint(f);
		}
	} else if (write_batch) {
		/* The --read-batch process is going to be a client
		 * receiver, so we need to give it the stats. */
		write_longint(batch_fd, total_read);
		write_longint(batch_fd, total_written);
		write_longint(batch_fd, stats.total_size);
		if (protocol_version >= 29) {
			write_longint(batch_fd, stats.flist_buildtime);
			write_longint(batch_fd, stats.flist_xfertime);
		}
	}
}

static void output_summary(void)
{
	if (do_stats) {
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
		if (stats.flist_buildtime) {
			rprintf(FINFO,
				"File list generation time: %.3f seconds\n",
				(double)stats.flist_buildtime / 1000);
			rprintf(FINFO,
				"File list transfer time: %.3f seconds\n",
				(double)stats.flist_xfertime / 1000);
		}
		rprintf(FINFO,"Total bytes sent: %.0f\n",
			(double)total_written);
		rprintf(FINFO,"Total bytes received: %.0f\n",
			(double)total_read);
	}

	if (verbose || do_stats) {
		rprintf(FINFO,
			"\nsent %.0f bytes  received %.0f bytes  %.2f bytes/sec\n",
			(double)total_written, (double)total_read,
			(total_written + total_read)/(0.5 + (endtime - starttime)));
		rprintf(FINFO, "total size is %.0f  speedup is %.2f\n",
			(double)stats.total_size,
			(double)stats.total_size / (total_written+total_read));
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

	mi = mallinfo();

	rprintf(FINFO, "\n" RSYNC_NAME "[%d] (%s%s%s) heap statistics:\n",
		getpid(), am_server ? "server " : "",
		am_daemon ? "daemon " : "", who_am_i());
	rprintf(FINFO, "  arena:     %10ld   (bytes from sbrk)\n",
		(long)mi.arena);
	rprintf(FINFO, "  ordblks:   %10ld   (chunks not in use)\n",
		(long)mi.ordblks);
	rprintf(FINFO, "  smblks:    %10ld\n",
		(long)mi.smblks);
	rprintf(FINFO, "  hblks:     %10ld   (chunks from mmap)\n",
		(long)mi.hblks);
	rprintf(FINFO, "  hblkhd:    %10ld   (bytes from mmap)\n",
		(long)mi.hblkhd);
	rprintf(FINFO, "  allmem:    %10ld   (bytes from sbrk + mmap)\n",
		(long)mi.arena + mi.hblkhd);
	rprintf(FINFO, "  usmblks:   %10ld\n",
		(long)mi.usmblks);
	rprintf(FINFO, "  fsmblks:   %10ld\n",
		(long)mi.fsmblks);
	rprintf(FINFO, "  uordblks:  %10ld   (bytes used)\n",
		(long)mi.uordblks);
	rprintf(FINFO, "  fordblks:  %10ld   (bytes free)\n",
		(long)mi.fordblks);
	rprintf(FINFO, "  keepcost:  %10ld   (bytes in releasable chunk)\n",
		(long)mi.keepcost);
#endif /* HAVE_MALLINFO */
}


/* Start the remote shell.   cmd may be NULL to use the default. */
static pid_t do_cmd(char *cmd, char *machine, char *user, char *path,
		    int *f_in, int *f_out)
{
	int i, argc = 0;
	char *args[MAX_ARGS];
	pid_t ret;
	char *tok, *dir = NULL;
	int dash_l_set = 0;

	if (!read_batch && !local_server) {
		char *rsh_env = getenv(RSYNC_RSH_ENV);
		if (!cmd)
			cmd = rsh_env;
		if (!cmd)
			cmd = RSYNC_RSH;
		cmd = strdup(cmd);
		if (!cmd)
			goto oom;

		for (tok = strtok(cmd, " "); tok; tok = strtok(NULL, " ")) {
			/* Comparison leaves rooms for server_options(). */
			if (argc >= MAX_ARGS - MAX_SERVER_ARGS) {
				rprintf(FERROR, "internal: args[] overflowed in do_cmd()\n");
				exit_cleanup(RERR_SYNTAX);
			}
			args[argc++] = tok;
		}

		/* check to see if we've already been given '-l user' in
		 * the remote-shell command */
		for (i = 0; i < argc-1; i++) {
			if (!strcmp(args[i], "-l") && args[i+1][0] != '-')
				dash_l_set = 1;
		}

#ifdef HAVE_REMSH
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

		if (blocking_io < 0) {
			char *cp;
			if ((cp = strrchr(cmd, '/')) != NULL)
				cp++;
			else
				cp = cmd;
			if (strcmp(cp, "rsh") == 0 || strcmp(cp, "remsh") == 0)
				blocking_io = 1;
		}

		server_options(args,&argc);

		if (argc >= MAX_ARGS - 2) {
			rprintf(FERROR, "internal: args[] overflowed in do_cmd()\n");
			exit_cleanup(RERR_SYNTAX);
		}
	}

	args[argc++] = ".";

	if (!daemon_over_rsh && path && *path)
		args[argc++] = path;

	args[argc] = NULL;

	if (verbose > 3) {
		rprintf(FINFO,"cmd=");
		for (i = 0; i < argc; i++)
			rprintf(FINFO, "%s ", safe_fname(args[i]));
		rprintf(FINFO,"\n");
	}

	if (read_batch) {
		int from_gen_pipe[2];
		if (fd_pair(from_gen_pipe) < 0) {
			rsyserr(FERROR, errno, "pipe");
			exit_cleanup(RERR_IPC);
		}
		batch_gen_fd = from_gen_pipe[0];
		*f_out = from_gen_pipe[1];
		*f_in = batch_fd;
		ret = -1; /* no child pid */
	} else if (local_server) {
		/* If the user didn't request --[no-]whole-file, force
		 * it on, but only if we're not batch processing. */
		if (whole_file < 0 && !write_batch)
			whole_file = 1;
		ret = local_child(argc, args, f_in, f_out, child_main);
	} else
		ret = piped_child(args,f_in,f_out);

	if (dir)
		free(dir);

	return ret;

oom:
	out_of_memory("do_cmd");
	return 0; /* not reached */
}


static char *get_local_name(struct file_list *flist,char *name)
{
	STRUCT_STAT st;
	int e;

	if (verbose > 2)
		rprintf(FINFO,"get_local_name count=%d %s\n",
			flist->count, NS(name));

	if (!name)
		return NULL;

	if (do_stat(name,&st) == 0) {
		if (S_ISDIR(st.st_mode)) {
			if (!push_dir(name)) {
				rsyserr(FERROR, errno, "push_dir#1 %s failed",
					full_fname(name));
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

	if (flist->count <= 1 && ((e = strlen(name)) <= 1 || name[e-1] != '/'))
		return name;

	if (do_mkdir(name,0777 & ~orig_umask) != 0) {
		rsyserr(FERROR, errno, "mkdir %s failed", full_fname(name));
		exit_cleanup(RERR_FILEIO);
	}
	if (verbose > 0)
		rprintf(FINFO, "created directory %s\n", safe_fname(name));

	if (dry_run) {
		dry_run++;
		return NULL;
	}

	if (!push_dir(name)) {
		rsyserr(FERROR, errno, "push_dir#2 %s failed",
			full_fname(name));
		exit_cleanup(RERR_FILESELECT);
	}

	return NULL;
}


/* This is only called by the sender. */
static void read_final_goodbye(int f_in, int f_out)
{
	int i;

	if (protocol_version < 29)
		i = read_int(f_in);
	else {
		while ((i = read_int(f_in)) == the_file_list->count
		    && read_shortint(f_in) == ITEM_IS_NEW) {
			/* Forward the keep-alive (no-op) to the receiver. */
			write_int(f_out, the_file_list->count);
			write_shortint(f_out, ITEM_IS_NEW);
		}
	}

	if (i != -1) {
		rprintf(FERROR, "Invalid packet at end of run (%d) [%s]\n",
			i, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
}


static void do_server_sender(int f_in, int f_out, int argc,char *argv[])
{
	int i;
	struct file_list *flist;
	char *dir = argv[0];

	if (verbose > 2) {
		rprintf(FINFO, "server_sender starting pid=%ld\n",
			(long)getpid());
	}

	if (am_daemon && lp_write_only(module_id)) {
		rprintf(FERROR, "ERROR: module is write only\n");
		exit_cleanup(RERR_SYNTAX);
		return;
	}
	if (am_daemon && lp_read_only(module_id) && remove_sent_files) {
		rprintf(FERROR,
		    "ERROR: --remove-sent-files cannot be used with a read-only module\n");
		exit_cleanup(RERR_SYNTAX);
		return;
	}

	if (!relative_paths && !push_dir(dir)) {
		rsyserr(FERROR, errno, "push_dir#3 %s failed",
			full_fname(dir));
		exit_cleanup(RERR_FILESELECT);
	}
	argc--;
	argv++;

	if (strcmp(dir,".")) {
		int l = strlen(dir);
		if (strcmp(dir,"/") == 0)
			l = 0;
		for (i = 0; i < argc; i++)
			argv[i] += l+1;
	}

	if (argc == 0 && (recurse || list_only)) {
		argc = 1;
		argv--;
		argv[0] = ".";
	}

	flist = send_file_list(f_out,argc,argv);
	if (!flist || flist->count == 0) {
		exit_cleanup(0);
	}
	the_file_list = flist;

	io_start_buffering_in();
	io_start_buffering_out();

	send_files(flist,f_out,f_in);
	io_flush(FULL_FLUSH);
	handle_stats(f_out);
	if (protocol_version >= 24)
		read_final_goodbye(f_in, f_out);
	io_flush(FULL_FLUSH);
	exit_cleanup(0);
}


static int do_recv(int f_in,int f_out,struct file_list *flist,char *local_name)
{
	int pid;
	int status = 0;
	int error_pipe[2];

	/* The receiving side mustn't obey this, or an existing symlink that
	 * points to an identical file won't be replaced by the referent. */
	copy_links = 0;

	if (preserve_hard_links)
		init_hard_links();

	if (fd_pair(error_pipe) < 0) {
		rsyserr(FERROR, errno, "pipe failed in do_recv");
		exit_cleanup(RERR_IPC);
	}

	io_flush(NORMAL_FLUSH);

	if ((pid = do_fork()) == -1) {
		rsyserr(FERROR, errno, "fork failed in do_recv");
		exit_cleanup(RERR_IPC);
	}

	if (pid == 0) {
		close(error_pipe[0]);
		if (f_in != f_out)
			close(f_out);

		/* we can't let two processes write to the socket at one time */
		close_multiplexing_out();

		/* set place to send errors */
		set_msg_fd_out(error_pipe[1]);

		recv_files(f_in, flist, local_name);
		io_flush(FULL_FLUSH);
		handle_stats(f_in);

		send_msg(MSG_DONE, "", 0);
		io_flush(FULL_FLUSH);

		/* Handle any keep-alive packets from the post-processing work
		 * that the generator does. */
		if (protocol_version >= 29) {
			kluge_around_eof = -1;

			/* This should only get stopped via a USR2 signal. */
			while (read_int(f_in) == flist->count
			    && read_shortint(f_in) == ITEM_IS_NEW) {}

			rprintf(FERROR, "Invalid packet at end of run [%s]\n",
				who_am_i());
			exit_cleanup(RERR_PROTOCOL);
		}

		/* Finally, we go to sleep until our parent kills us with a
		 * USR2 signal.  We sleep for a short time, as on some OSes
		 * a signal won't interrupt a sleep! */
		while (1)
			msleep(20);
	}

	am_generator = 1;
	close_multiplexing_in();
	if (write_batch && !am_server)
		stop_write_batch();

	close(error_pipe[1]);
	if (f_in != f_out)
		close(f_in);

	io_start_buffering_out();

	set_msg_fd_in(error_pipe[0]);

	generate_files(f_out, flist, local_name);

	handle_stats(-1);
	io_flush(FULL_FLUSH);
	if (protocol_version >= 24) {
		/* send a final goodbye message */
		write_int(f_out, -1);
	}
	io_flush(FULL_FLUSH);

	set_msg_fd_in(-1);
	kill(pid, SIGUSR2);
	wait_process(pid, &status);
	return status;
}


static void do_server_recv(int f_in, int f_out, int argc,char *argv[])
{
	int status;
	struct file_list *flist;
	char *local_name = NULL;
	char *dir = NULL;
	int save_verbose = verbose;

	if (filesfrom_fd >= 0) {
		/* We can't mix messages with files-from data on the socket,
		 * so temporarily turn off verbose messages. */
		verbose = 0;
	}

	if (verbose > 2) {
		rprintf(FINFO, "server_recv(%d) starting pid=%ld\n",
			argc, (long)getpid());
	}

	if (am_daemon && lp_read_only(module_id)) {
		rprintf(FERROR,"ERROR: module is read only\n");
		exit_cleanup(RERR_SYNTAX);
		return;
	}


	if (argc > 0) {
		dir = argv[0];
		argc--;
		argv++;
		if (!am_daemon && !push_dir(dir)) {
			rsyserr(FERROR, errno, "push_dir#4 %s failed",
				full_fname(dir));
			exit_cleanup(RERR_FILESELECT);
		}
	}

	io_start_buffering_in();
	recv_filter_list(f_in);

	if (filesfrom_fd >= 0) {
		/* We need to send the files-from names to the sender at the
		 * same time that we receive the file-list from them, so we
		 * need the IO routines to automatically write out the names
		 * onto our f_out socket as we read the file-list.  This
		 * avoids both deadlock and extra delays/buffers. */
		io_set_filesfrom_fds(filesfrom_fd, f_out);
		filesfrom_fd = -1;
	}

	flist = recv_file_list(f_in);
	verbose = save_verbose;
	if (!flist) {
		rprintf(FERROR,"server_recv: recv_file_list error\n");
		exit_cleanup(RERR_FILESELECT);
	}
	the_file_list = flist;

	if (argc > 0) {
		if (strcmp(dir,".")) {
			argv[0] += strlen(dir);
			if (argv[0][0] == '/')
				argv[0]++;
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
	set_nonblocking(f_in);
	set_nonblocking(f_out);

	io_set_sock_fds(f_in, f_out);
	setup_protocol(f_out, f_in);

	if (protocol_version >= 23)
		io_start_multiplex_out();

	if (am_sender) {
		keep_dirlinks = 0; /* Must be disabled on the sender. */
		if (need_messages_from_generator)
			io_start_multiplex_in();

		recv_filter_list(f_in);
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

	cleanup_child_pid = pid;
	if (!read_batch) {
		set_nonblocking(f_in);
		set_nonblocking(f_out);
	}

	io_set_sock_fds(f_in, f_out);
	setup_protocol(f_out,f_in);

	if (protocol_version >= 23 && !read_batch)
		io_start_multiplex_in();

	/* We set our stderr file handle to blocking because ssh might have
	 * set it to non-blocking.  This can be particularly troublesome if
	 * stderr is a clone of stdout, because ssh would have set our stdout
	 * to non-blocking at the same time (which can easily cause us to lose
	 * output from our print statements).  This kluge shouldn't cause ssh
	 * any problems for how we use it.  Note also that we delayed setting
	 * this until after the above protocol setup so that we know for sure
	 * that ssh is done twiddling its file descriptors.  */
	set_blocking(STDERR_FILENO);

	if (am_sender) {
		keep_dirlinks = 0; /* Must be disabled on the sender. */
		io_start_buffering_out();
		if (!filesfrom_host)
			set_msg_fd_in(f_in);
		send_filter_list(f_out);
		if (filesfrom_host)
			filesfrom_fd = f_in;

		if (write_batch && !am_server)
			start_write_batch(f_out);
		flist = send_file_list(f_out, argc, argv);
		set_msg_fd_in(-1);
		if (verbose > 3)
			rprintf(FINFO,"file list sent\n");
		the_file_list = flist;

		io_flush(NORMAL_FLUSH);
		send_files(flist,f_out,f_in);
		io_flush(FULL_FLUSH);
		handle_stats(-1);
		if (protocol_version >= 24)
			read_final_goodbye(f_in, f_out);
		if (pid != -1) {
			if (verbose > 3)
				rprintf(FINFO,"client_run waiting on %d\n", (int) pid);
			io_flush(FULL_FLUSH);
			wait_process(pid, &status);
		}
		output_summary();
		io_flush(FULL_FLUSH);
		exit_cleanup(status);
	}

	if (need_messages_from_generator && !read_batch)
		io_start_multiplex_out();

	if (argc == 0)
		list_only |= 1;

	send_filter_list(read_batch ? -1 : f_out);

	if (filesfrom_fd >= 0) {
		io_set_filesfrom_fds(filesfrom_fd, f_out);
		filesfrom_fd = -1;
	}

	if (write_batch && !am_server)
		start_write_batch(f_in);
	flist = recv_file_list(f_in);
	the_file_list = flist;

	if (flist && flist->count > 0) {
		local_name = get_local_name(flist, argv[0]);

		status2 = do_recv(f_in, f_out, flist, local_name);
	} else {
		handle_stats(-1);
		output_summary();
	}

	if (pid != -1) {
		if (verbose > 3)
			rprintf(FINFO,"client_run2 waiting on %d\n", (int) pid);
		io_flush(FULL_FLUSH);
		wait_process(pid, &status);
	}

	return MAX(status, status2);
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
	int rc;

	/* Don't clobber argv[] so that ps(1) can still show the right
	 * command line. */
	if ((rc = copy_argv(argv)))
		return rc;

	if (!read_batch) { /* for read_batch, NO source is specified */
		argc--;
		shell_path = check_for_hostspec(argv[0], &shell_machine, &rsync_port);
		if (shell_path) { /* source is remote */
			argv++;
			if (filesfrom_host && *filesfrom_host
			    && strcmp(filesfrom_host, shell_machine) != 0) {
				rprintf(FERROR,
					"--files-from hostname is not the same as the transfer hostname\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (rsync_port) {
				if (!shell_cmd) {
					return start_socket_client(shell_machine,
								   shell_path,
								   argc, argv);
				}
				daemon_over_rsh = 1;
			}

			am_sender = 0;
		} else { /* source is local, check dest arg */
			am_sender = 1;

			if (argc < 1) { /* destination required */
				usage(FERROR);
				exit_cleanup(RERR_SYNTAX);
			}

			shell_path = check_for_hostspec(argv[argc], &shell_machine, &rsync_port);
			if (shell_path && filesfrom_host && *filesfrom_host
			    && strcmp(filesfrom_host, shell_machine) != 0) {
				rprintf(FERROR,
					"--files-from hostname is not the same as the transfer hostname\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (!shell_path) { /* no hostspec found, so src & dest are local */
				local_server = 1;
				if (filesfrom_host) {
					rprintf(FERROR,
						"--files-from cannot be remote when the transfer is local\n");
					exit_cleanup(RERR_SYNTAX);
				}
				shell_machine = NULL;
				shell_path = argv[argc];
			} else if (rsync_port) {
				if (!shell_cmd) {
					return start_socket_client(shell_machine,
								   shell_path,
								   argc, argv);
				}
				daemon_over_rsh = 1;
			}
		}
	} else {  /* read_batch */
		local_server = 1;
		shell_path = argv[argc-1];
		if (check_for_hostspec(shell_path, &shell_machine, &rsync_port)) {
			rprintf(FERROR, "remote destination is not allowed with --read-batch\n");
			exit_cleanup(RERR_SYNTAX);
		}
	}

	if (shell_machine) {
		p = strrchr(shell_machine,'@');
		if (p) {
			*p = 0;
			shell_user = shell_machine;
			shell_machine = p+1;
		}
	}

	if (verbose > 3) {
		rprintf(FINFO,"cmd=%s machine=%s user=%s path=%s\n",
			shell_cmd ? safe_fname(shell_cmd) : "",
			shell_machine ? safe_fname(shell_machine) : "",
			shell_user ? safe_fname(shell_user) : "",
			shell_path ? safe_fname(shell_path) : "");
	}

	/* for remote source, only single dest arg can remain ... */
	if (!am_sender && argc > 1) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

	/* ... or no dest at all */
	if (!am_sender && argc == 0)
		list_only |= 1;

	pid = do_cmd(shell_cmd,shell_machine,shell_user,shell_path,
		     &f_in,&f_out);

	/* if we're running an rsync server on the remote host over a
	 * remote shell command, we need to do the RSYNCD protocol first */
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


static RETSIGTYPE sigusr1_handler(UNUSED(int val))
{
	exit_cleanup(RERR_SIGNAL);
}

static RETSIGTYPE sigusr2_handler(UNUSED(int val))
{
	if (!am_server)
		output_summary();
	close_all();
	if (log_got_error)
		_exit(RERR_PARTIAL);
	_exit(0);
}

static RETSIGTYPE sigchld_handler(UNUSED(int val))
{
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
static RETSIGTYPE rsync_panic_handler(UNUSED(int whatsig))
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
	int ret;
	int orig_argc = argc;
	char **orig_argv = argv;

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
	am_root = (MY_UID() == 0);

	memset(&stats, 0, sizeof(stats));

	if (argc < 2) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

	/* we set a 0 umask so that correct file permissions can be
	 * carried across */
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

#if defined CONFIG_LOCALE && defined HAVE_SETLOCALE
	setlocale(LC_CTYPE, "");
#endif

	/* Initialize push_dir here because on some old systems getcwd
	 * (implemented by forking "pwd" and reading its output) doesn't
	 * work when there are other child processes.  Also, on all systems
	 * that implement getcwd that way "pwd" can't be found after chroot. */
	push_dir(NULL);

	init_flist();

	if ((write_batch || read_batch) && !am_server) {
		if (write_batch)
			write_batch_shell_file(orig_argc, orig_argv, argc);

		if (read_batch && strcmp(batch_name, "-") == 0)
			batch_fd = STDIN_FILENO;
		else {
			batch_fd = do_open(batch_name,
				   write_batch ? O_WRONLY | O_CREAT | O_TRUNC
				   : O_RDONLY, S_IRUSR | S_IWUSR);
		}
		if (batch_fd < 0) {
			rsyserr(FERROR, errno, "Batch file %s open error",
				full_fname(batch_name));
			exit_cleanup(RERR_FILEIO);
		}
		if (read_batch)
			read_stream_flags(batch_fd);
	}
	if (write_batch < 0)
		dry_run = 1;

	if (am_daemon && !am_server)
		return daemon_main();

	if (argc < 1) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

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

	return ret;
}
