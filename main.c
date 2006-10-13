/*
 * The startup routines, including main(), for rsync.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003, 2004, 2005, 2006 Wayne Davison
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
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
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
extern int remove_source_files;
extern int daemon_over_rsh;
extern int need_messages_from_generator;
extern int kluge_around_eof;
extern int do_stats;
extern int log_got_error;
extern int module_id;
extern int copy_links;
extern int copy_dirlinks;
extern int keep_dirlinks;
extern int preserve_hard_links;
extern int protocol_version;
extern int recurse;
extern int relative_paths;
extern int sanitize_paths;
extern int curr_dir_depth;
extern int curr_dir_len;
extern int module_id;
extern int rsync_port;
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
extern char curr_dir[MAXPATHLEN];
extern struct filter_list_struct server_filter_list;

int local_server = 0;
int new_root_dir = 0;
mode_t orig_umask = 0;
struct file_list *the_file_list;

/* There's probably never more than at most 2 outstanding child processes,
 * but set it higher, just in case. */
#define MAXCHILDPROCS 7

#ifdef HAVE_SIGACTION
# ifdef HAVE_SIGPROCMASK
#  define SIGACTMASK(n,h) SIGACTION(n,h), sigaddset(&sigmask,(n))
# else
#  define SIGACTMASK(n,h) SIGACTION(n,h)
# endif
static struct sigaction sigact;
#endif

struct pid_status {
	pid_t pid;
	int status;
} pid_stat_table[MAXCHILDPROCS];

static time_t starttime, endtime;
static int64 total_read, total_written;

static void show_malloc_stats(void);

/* Works like waitpid(), but if we already harvested the child pid in our
 * remember_children(), we succeed instead of returning an error. */
pid_t wait_process(pid_t pid, int *status_ptr, int flags)
{
	pid_t waited_pid = waitpid(pid, status_ptr, flags);

	if (waited_pid == -1 && errno == ECHILD) {
		/* Status of requested child no longer available:  check to
		 * see if it was processed by remember_children(). */
		int cnt;
		for (cnt = 0; cnt < MAXCHILDPROCS; cnt++) {
			if (pid == pid_stat_table[cnt].pid) {
				*status_ptr = pid_stat_table[cnt].status;
				pid_stat_table[cnt].pid = 0;
				return pid;
			}
		}
	}

	return waited_pid;
}

/* Wait for a process to exit, calling io_flush while waiting. */
static void wait_process_with_flush(pid_t pid, int *exit_code_ptr)
{
	pid_t waited_pid;
	int status;

	while ((waited_pid = wait_process(pid, &status, WNOHANG)) == 0) {
		msleep(20);
		io_flush(FULL_FLUSH);
	}

	/* TODO: If the child exited on a signal, then log an
	 * appropriate error message.  Perhaps we should also accept a
	 * message describing the purpose of the child.  Also indicate
	 * this to the caller so that they know something went wrong. */
	if (waited_pid < 0) {
		rsyserr(FERROR, errno, "waitpid");
		*exit_code_ptr = RERR_WAITCHILD;
	} else if (!WIFEXITED(status)) {
#ifdef WCOREDUMP
		if (WCOREDUMP(status))
			*exit_code_ptr = RERR_CRASHED;
		else
#endif
		if (WIFSIGNALED(status))
			*exit_code_ptr = RERR_TERMINATED;
		else
			*exit_code_ptr = RERR_WAITCHILD;
	} else
		*exit_code_ptr = WEXITSTATUS(status);
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
		rprintf(FCLIENT, "\n");
		rprintf(FINFO,"Number of files: %d\n", stats.num_files);
		rprintf(FINFO,"Number of files transferred: %d\n",
			stats.num_transferred_files);
		rprintf(FINFO,"Total file size: %s bytes\n",
			human_num(stats.total_size));
		rprintf(FINFO,"Total transferred file size: %s bytes\n",
			human_num(stats.total_transferred_size));
		rprintf(FINFO,"Literal data: %s bytes\n",
			human_num(stats.literal_data));
		rprintf(FINFO,"Matched data: %s bytes\n",
			human_num(stats.matched_data));
		rprintf(FINFO,"File list size: %d\n", stats.flist_size);
		if (stats.flist_buildtime) {
			rprintf(FINFO,
				"File list generation time: %.3f seconds\n",
				(double)stats.flist_buildtime / 1000);
			rprintf(FINFO,
				"File list transfer time: %.3f seconds\n",
				(double)stats.flist_xfertime / 1000);
		}
		rprintf(FINFO,"Total bytes sent: %s\n",
			human_num(total_written));
		rprintf(FINFO,"Total bytes received: %s\n",
			human_num(total_read));
	}

	if (verbose || do_stats) {
		rprintf(FCLIENT, "\n");
		rprintf(FINFO,
			"sent %s bytes  received %s bytes  %s bytes/sec\n",
			human_num(total_written), human_num(total_read),
			human_dnum((total_written + total_read)/(0.5 + (endtime - starttime)), 2));
		rprintf(FINFO, "total size is %s  speedup is %.2f\n",
			human_num(stats.total_size),
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

	rprintf(FCLIENT, "\n");
	rprintf(FINFO, RSYNC_NAME "[%d] (%s%s%s) heap statistics:\n",
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
	char *dir = NULL;
	int dash_l_set = 0;

	if (!read_batch && !local_server) {
		char *t, *f, in_quote = '\0';
		char *rsh_env = getenv(RSYNC_RSH_ENV);
		if (!cmd)
			cmd = rsh_env;
		if (!cmd)
			cmd = RSYNC_RSH;
		cmd = strdup(cmd);
		if (!cmd)
			goto oom;

		for (t = f = cmd; *f; f++) {
			if (*f == ' ')
				continue;
			/* Comparison leaves rooms for server_options(). */
			if (argc >= MAX_ARGS - MAX_SERVER_ARGS) {
				rprintf(FERROR, "internal: args[] overflowed in do_cmd()\n");
				exit_cleanup(RERR_SYNTAX);
			}
			args[argc++] = t;
			while (*f != ' ' || in_quote) {
				if (!*f) {
					if (in_quote) {
						rprintf(FERROR,
						    "Missing trailing-%c in remote-shell command.\n",
						    in_quote);
						exit_cleanup(RERR_SYNTAX);
					}
					f--;
					break;
				}
				if (*f == '\'' || *f == '"') {
					if (!in_quote) {
						in_quote = *f++;
						continue;
					}
					if (*f == in_quote && *++f != in_quote) {
						in_quote = '\0';
						continue;
					}
				}
				*t++ = *f++;
			}
			*t++ = '\0';
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
		for (i = 0; i < argc; i++)
			rprintf(FCLIENT, "cmd[%d]=%s ", i, args[i]);
		rprintf(FCLIENT, "\n");
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

/* The receiving side operates in one of two modes:
 *
 * 1. it receives any number of files into a destination directory,
 * placing them according to their names in the file-list.
 *
 * 2. it receives a single file and saves it using the name in the
 * destination path instead of its file-list name.  This requires a
 * "local name" for writing out the destination file.
 *
 * So, our task is to figure out what mode/local-name we need.
 * For mode 1, we change into the destination directory and return NULL.
 * For mode 2, we change into the directory containing the destination
 * file (if we aren't already there) and return the local-name. */
static char *get_local_name(struct file_list *flist, char *dest_path)
{
	STRUCT_STAT st;
	int statret;
	char *cp;

	if (verbose > 2) {
		rprintf(FINFO, "get_local_name count=%d %s\n",
			flist->count, NS(dest_path));
	}

	if (!dest_path || list_only)
		return NULL;

	/* See what currently exists at the destination. */
	if ((statret = do_stat(dest_path, &st)) == 0) {
		/* If the destination is a dir, enter it and use mode 1. */
		if (S_ISDIR(st.st_mode)) {
			if (!push_dir(dest_path, 0)) {
				rsyserr(FERROR, errno, "push_dir#1 %s failed",
					full_fname(dest_path));
				exit_cleanup(RERR_FILESELECT);
			}
			return NULL;
		}
		if (flist->count > 1) {
			rprintf(FERROR,
				"ERROR: destination must be a directory when"
				" copying more than 1 file\n");
			exit_cleanup(RERR_FILESELECT);
		}
		/* Caution: flist->count could be 0! */
		if (flist->count == 1 && S_ISDIR(flist->files[0]->mode)) {
			rprintf(FERROR,
				"ERROR: cannot overwrite non-directory"
				" with a directory\n");
			exit_cleanup(RERR_FILESELECT);
		}
	} else if (errno != ENOENT) {
		/* If we don't know what's at the destination, fail. */
		rsyserr(FERROR, errno, "ERROR: cannot stat destination %s",
			full_fname(dest_path));
		exit_cleanup(RERR_FILESELECT);
	}

	cp = strrchr(dest_path, '/');

	/* If we need a destination directory because the transfer is not
	 * of a single non-directory or the user has requested one via a
	 * destination path ending in a slash, create one and use mode 1. */
	if (flist->count > 1 || (cp && !cp[1])) {
		/* Lop off the final slash (if any). */
		if (cp && !cp[1])
			*cp = '\0';

		if (statret == 0) {
			rprintf(FERROR,
			    "ERROR: destination path is not a directory\n");
			exit_cleanup(RERR_SYNTAX);
		}

		if (mkdir_defmode(dest_path) != 0) {
			rsyserr(FERROR, errno, "mkdir %s failed",
				full_fname(dest_path));
			exit_cleanup(RERR_FILEIO);
		}

		new_root_dir = 1;

		if (verbose)
			rprintf(FINFO, "created directory %s\n", dest_path);

		if (dry_run) {
			/* Indicate that dest dir doesn't really exist. */
			dry_run++;
		}

		if (!push_dir(dest_path, dry_run > 1)) {
			rsyserr(FERROR, errno, "push_dir#2 %s failed",
				full_fname(dest_path));
			exit_cleanup(RERR_FILESELECT);
		}

		return NULL;
	}

	/* Otherwise, we are writing a single file, possibly on top of an
	 * existing non-directory.  Change to the item's parent directory
	 * (if it has a path component), return the basename of the
	 * destination file as the local name, and use mode 2. */
	if (!cp)
		return dest_path;

	if (cp == dest_path)
		dest_path = "/";

	*cp = '\0';
	if (!push_dir(dest_path, 0)) {
		rsyserr(FERROR, errno, "push_dir#3 %s failed",
			full_fname(dest_path));
		exit_cleanup(RERR_FILESELECT);
	}
	*cp = '/';

	return cp + 1;
}

/* Call this if the destination dir (which is assumed to be in curr_dir)
 * does not yet exist and we can't create it due to being in dry-run
 * mode.  We'll fix dirs that can be relative to the non-existent dir. */
static void fix_basis_dirs(void)
{
	char **dir, *new, *slash;
	int len;

	if (dry_run <= 1)
		return;

	slash = strrchr(curr_dir, '/');

	for (dir = basis_dir; *dir; dir++) {
		if (**dir == '/')
			continue;
		len = curr_dir_len + 1 + strlen(*dir) + 1;
		if (!(new = new_array(char, len)))
			out_of_memory("fix_basis_dirs");
		if (slash && strncmp(*dir, "../", 3) == 0) {
		    /* We want to remove only one leading "../" prefix for
		     * the directory we couldn't create in dry-run mode:
		     * this ensures that any other ".." references get
		     * evaluated the same as they would for a live copy. */
		    *slash = '\0';
		    pathjoin(new, len, curr_dir, *dir + 3);
		    *slash = '/';
		} else
		    pathjoin(new, len, curr_dir, *dir);
		*dir = new;
	}
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


static void do_server_sender(int f_in, int f_out, int argc, char *argv[])
{
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
	if (am_daemon && lp_read_only(module_id) && remove_source_files) {
		rprintf(FERROR,
		    "ERROR: --remove-%s-files cannot be used with a read-only module\n",
		    remove_source_files == 1 ? "source" : "sent");
		exit_cleanup(RERR_SYNTAX);
		return;
	}

	if (!relative_paths) {
		if (!push_dir(dir, 0)) {
			rsyserr(FERROR, errno, "push_dir#3 %s failed",
				full_fname(dir));
			exit_cleanup(RERR_FILESELECT);
		}
	}
	argc--;
	argv++;

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
	int exit_code = 0;
	int error_pipe[2];

	/* The receiving side mustn't obey this, or an existing symlink that
	 * points to an identical file won't be replaced by the referent. */
	copy_links = copy_dirlinks = 0;

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
	wait_process_with_flush(pid, &exit_code);
	return exit_code;
}


static void do_server_recv(int f_in, int f_out, int argc,char *argv[])
{
	int exit_code;
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
		if (!am_daemon && !push_dir(dir, 0)) {
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

	if (argc > 0)
		local_name = get_local_name(flist,argv[0]);

	/* Now that we know what our destination directory turned out to be,
	 * we can sanitize the --link-/copy-/compare-dest args correctly. */
	if (sanitize_paths) {
		char **dir;
		for (dir = basis_dir; *dir; dir++) {
			*dir = sanitize_path(NULL, *dir, NULL, curr_dir_depth, NULL);
		}
		if (partial_dir) {
			partial_dir = sanitize_path(NULL, partial_dir, NULL, curr_dir_depth, NULL);
		}
	}
	fix_basis_dirs();

	if (server_filter_list.head) {
		char **dir;
		struct filter_list_struct *elp = &server_filter_list;

		for (dir = basis_dir; *dir; dir++) {
			if (check_filter(elp, *dir, 1) < 0)
				goto options_rejected;
		}
		if (partial_dir && *partial_dir == '/'
		 && check_filter(elp, partial_dir, 1) < 0) {
		    options_rejected:
			rprintf(FERROR,
				"Your options have been rejected by the server.\n");
			exit_cleanup(RERR_SYNTAX);
		}
	}

	exit_code = do_recv(f_in,f_out,flist,local_name);
	exit_cleanup(exit_code);
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
#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
	setup_iconv();
#endif

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
	int exit_code = 0, exit_code2 = 0;
	char *local_name = NULL;

	cleanup_child_pid = pid;
	if (!read_batch) {
		set_nonblocking(f_in);
		set_nonblocking(f_out);
	}

	io_set_sock_fds(f_in, f_out);
	setup_protocol(f_out,f_in);
#if defined HAVE_ICONV_OPEN && defined HAVE_ICONV_H
	setup_iconv();
#endif

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
			wait_process_with_flush(pid, &exit_code);
		}
		output_summary();
		io_flush(FULL_FLUSH);
		exit_cleanup(exit_code);
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

		fix_basis_dirs();

		exit_code2 = do_recv(f_in, f_out, flist, local_name);
	} else {
		handle_stats(-1);
		output_summary();
	}

	if (pid != -1) {
		if (verbose > 3)
			rprintf(FINFO,"client_run2 waiting on %d\n", (int) pid);
		io_flush(FULL_FLUSH);
		wait_process_with_flush(pid, &exit_code);
	}

	return MAX(exit_code, exit_code2);
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
		shell_path = check_for_hostspec(argv[0], &shell_machine, &rsync_port);
		if (shell_path) { /* source is remote */
			char *dummy1;
			int dummy2;
			if (--argc
			 && check_for_hostspec(argv[argc], &dummy1, &dummy2)) {
				rprintf(FERROR,
					"The source and destination cannot both be remote.\n");
				exit_cleanup(RERR_SYNTAX);
			}
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

			if (argc > 1)
				p = argv[--argc];
			else {
				p = ".";
				list_only = 1;
			}

			shell_path = check_for_hostspec(p, &shell_machine, &rsync_port);
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
				shell_path = p;
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
			shell_cmd ? shell_cmd : "",
			shell_machine ? shell_machine : "",
			shell_user ? shell_user : "",
			shell_path ? shell_path : "");
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
	exit_cleanup(RERR_SIGNAL1);
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

RETSIGTYPE remember_children(UNUSED(int val))
{
#ifdef WNOHANG
	int cnt, status;
	pid_t pid;
	/* An empty waitpid() loop was put here by Tridge and we could never
	 * get him to explain why he put it in, so rather than taking it
	 * out we're instead saving the child exit statuses for later use.
	 * The waitpid() loop presumably eliminates all possibility of leaving
	 * zombie children, maybe that's why he did it. */
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
#ifndef HAVE_SIGACTION
	signal(SIGCHLD, remember_children);
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

	snprintf(cmd_buf, sizeof cmd_buf, get_panic_action(),
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
#ifdef HAVE_SIGACTION
# ifdef HAVE_SIGPROCMASK
	sigset_t sigmask;

	sigemptyset(&sigmask);
# endif
	sigact.sa_flags = SA_NOCLDSTOP;
#endif
	SIGACTMASK(SIGUSR1, sigusr1_handler);
	SIGACTMASK(SIGUSR2, sigusr2_handler);
	SIGACTMASK(SIGCHLD, remember_children);
#ifdef MAINTAINER_MODE
	SIGACTMASK(SIGSEGV, rsync_panic_handler);
	SIGACTMASK(SIGFPE, rsync_panic_handler);
	SIGACTMASK(SIGABRT, rsync_panic_handler);
	SIGACTMASK(SIGBUS, rsync_panic_handler);
#endif

	starttime = time(NULL);
	am_root = (MY_UID() == 0);

	memset(&stats, 0, sizeof(stats));

	if (argc < 2) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

	/* we set a 0 umask so that correct file permissions can be
	 * carried across */
	orig_umask = umask(0);

#if defined CONFIG_LOCALE && defined HAVE_SETLOCALE
	setlocale(LC_CTYPE, "");
#endif

	if (!parse_arguments(&argc, (const char ***) &argv, 1)) {
		/* FIXME: We ought to call the same error-handling
		 * code here, rather than relying on getopt. */
		option_error();
		exit_cleanup(RERR_SYNTAX);
	}

	SIGACTMASK(SIGINT, sig_int);
	SIGACTMASK(SIGHUP, sig_int);
	SIGACTMASK(SIGTERM, sig_int);
#if defined HAVE_SIGACTION && HAVE_SIGPROCMASK
	sigprocmask(SIG_UNBLOCK, &sigmask, NULL);
#endif

	/* Ignore SIGPIPE; we consistently check error codes and will
	 * see the EPIPE. */
	SIGACTION(SIGPIPE, SIG_IGN);
#ifdef SIGXFSZ
	SIGACTION(SIGXFSZ, SIG_IGN);
#endif

	/* Initialize push_dir here because on some old systems getcwd
	 * (implemented by forking "pwd" and reading its output) doesn't
	 * work when there are other child processes.  Also, on all systems
	 * that implement getcwd that way "pwd" can't be found after chroot. */
	push_dir(NULL, 0);

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
