/*
 * The startup routines, including main(), for rsync.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2022 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"
#include "inums.h"
#include "ifuncs.h"
#include "io.h"
#if defined CONFIG_LOCALE && defined HAVE_LOCALE_H
#include <locale.h>
#endif
#include <popt.h>
#ifdef __TANDEM
#include <floss.h(floss_execlp)>
#endif

extern int dry_run;
extern int list_only;
extern int io_timeout;
extern int am_root;
extern int am_server;
extern int am_sender;
extern int am_daemon;
extern int inc_recurse;
extern int blocking_io;
extern int always_checksum;
extern int remove_source_files;
extern int output_needs_newline;
extern int called_from_signal_handler;
extern int need_messages_from_generator;
extern int kluge_around_eof;
extern int got_xfer_error;
extern int old_style_args;
extern int msgs2stderr;
extern int module_id;
extern int read_only;
extern int copy_links;
extern int copy_dirlinks;
extern int copy_unsafe_links;
extern int keep_dirlinks;
extern int preserve_hard_links;
extern int protocol_version;
extern int mkpath_dest_arg;
extern int file_total;
extern int recurse;
extern int xfer_dirs;
extern int protect_args;
extern int relative_paths;
extern int sanitize_paths;
extern int curr_dir_depth;
extern unsigned int curr_dir_len;
extern int module_id;
extern int rsync_port;
extern int whole_file;
extern int read_batch;
extern int write_batch;
extern int batch_fd;
extern int sock_f_in;
extern int sock_f_out;
extern int filesfrom_fd;
extern int connect_timeout;
extern int send_msgs_to_gen;
extern dev_t filesystem_dev;
extern pid_t cleanup_child_pid;
extern size_t bwlimit_writemax;
extern unsigned int module_dirlen;
extern BOOL flist_receiving_enabled;
extern BOOL want_progress_now;
extern BOOL shutting_down;
extern int backup_dir_len;
extern int basis_dir_cnt;
extern int default_af_hint;
extern int stdout_format_has_i;
extern int trust_sender_filter;
extern int trust_sender_args;
extern struct stats stats;
extern char *stdout_format;
extern char *logfile_format;
extern char *filesfrom_host;
extern char *partial_dir;
extern char *rsync_path;
extern char *shell_cmd;
extern char *password_file;
extern char *backup_dir;
extern char *copy_as;
extern char *tmpdir;
extern char curr_dir[MAXPATHLEN];
extern char backup_dir_buf[MAXPATHLEN];
extern char *basis_dir[MAX_BASIS_DIRS+1];
extern struct file_list *first_flist;
extern filter_rule_list daemon_filter_list, implied_filter_list;

uid_t our_uid;
gid_t our_gid;
int am_receiver = 0;  /* Only set to 1 after the receiver/generator fork. */
int am_generator = 0; /* Only set to 1 after the receiver/generator fork. */
int local_server = 0;
int daemon_connection = 0; /* 0 = no daemon, 1 = daemon via remote shell, -1 = daemon via socket */
mode_t orig_umask = 0;
int batch_gen_fd = -1;
int sender_keeps_checksum = 0;
int raw_argc, cooked_argc;
char **raw_argv, **cooked_argv;

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
	pid_t waited_pid;

	do {
		waited_pid = waitpid(pid, status_ptr, flags);
	} while (waited_pid == -1 && errno == EINTR);

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

int shell_exec(const char *cmd)
{
	char *shell = getenv("RSYNC_SHELL");
	int status;
	pid_t pid;

	if (!shell)
		return system(cmd);

	if ((pid = fork()) < 0)
		return -1;

	if (pid == 0) {
		execlp(shell, shell, "-c", cmd, NULL);
		_exit(1);
	}

	int ret = wait_process(pid, &status, 0);
	return ret < 0 ? -1 : status;
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

void write_del_stats(int f)
{
	if (read_batch)
		write_int(f, NDX_DEL_STATS);
	else
		write_ndx(f, NDX_DEL_STATS);
	write_varint(f, stats.deleted_files - stats.deleted_dirs
		      - stats.deleted_symlinks - stats.deleted_devices
		      - stats.deleted_specials);
	write_varint(f, stats.deleted_dirs);
	write_varint(f, stats.deleted_symlinks);
	write_varint(f, stats.deleted_devices);
	write_varint(f, stats.deleted_specials);
}

void read_del_stats(int f)
{
	stats.deleted_files = read_varint(f);
	stats.deleted_files += stats.deleted_dirs = read_varint(f);
	stats.deleted_files += stats.deleted_symlinks = read_varint(f);
	stats.deleted_files += stats.deleted_devices = read_varint(f);
	stats.deleted_files += stats.deleted_specials = read_varint(f);
}

static void become_copy_as_user()
{
	char *gname;
	uid_t uid;
	gid_t gid;

	if (!copy_as)
		return;

	if (DEBUG_GTE(CMD, 2))
		rprintf(FINFO, "[%s] copy_as=%s\n", who_am_i(), copy_as);

	if ((gname = strchr(copy_as, ':')) != NULL)
		*gname++ = '\0';

	if (!user_to_uid(copy_as, &uid, True)) {
		rprintf(FERROR, "Invalid copy-as user: %s\n", copy_as);
		exit_cleanup(RERR_SYNTAX);
	}

	if (gname) {
		if (!group_to_gid(gname, &gid, True)) {
			rprintf(FERROR, "Invalid copy-as group: %s\n", gname);
			exit_cleanup(RERR_SYNTAX);
		}
	} else {
		struct passwd *pw;
		if ((pw = getpwuid(uid)) == NULL) {
			rsyserr(FERROR, errno, "getpwuid failed");
			exit_cleanup(RERR_SYNTAX);
		}
		gid = pw->pw_gid;
	}

	if (setgid(gid) < 0) {
		rsyserr(FERROR, errno, "setgid failed");
		exit_cleanup(RERR_SYNTAX);
	}
#ifdef HAVE_SETGROUPS
	if (setgroups(1, &gid)) {
		rsyserr(FERROR, errno, "setgroups failed");
		exit_cleanup(RERR_SYNTAX);
	}
#endif
#ifdef HAVE_INITGROUPS
	if (!gname && initgroups(copy_as, gid) < 0) {
		rsyserr(FERROR, errno, "initgroups failed");
		exit_cleanup(RERR_SYNTAX);
	}
#endif

	if (setuid(uid) < 0
#ifdef HAVE_SETEUID
	 || seteuid(uid) < 0
#endif
	) {
		rsyserr(FERROR, errno, "setuid failed");
		exit_cleanup(RERR_SYNTAX);
	}

	our_uid = MY_UID();
	our_gid = MY_GID();
	am_root = (our_uid == ROOT_UID);

	if (gname)
		gname[-1] = ':';
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

	if (INFO_GTE(STATS, 3)) {
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
			write_varlong30(f, total_read, 3);
			write_varlong30(f, total_written, 3);
			write_varlong30(f, stats.total_size, 3);
			if (protocol_version >= 29) {
				write_varlong30(f, stats.flist_buildtime, 3);
				write_varlong30(f, stats.flist_xfertime, 3);
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
		total_written = read_varlong30(f, 3);
		total_read = read_varlong30(f, 3);
		stats.total_size = read_varlong30(f, 3);
		if (protocol_version >= 29) {
			stats.flist_buildtime = read_varlong30(f, 3);
			stats.flist_xfertime = read_varlong30(f, 3);
		}
	} else if (write_batch) {
		/* The --read-batch process is going to be a client
		 * receiver, so we need to give it the stats. */
		write_varlong30(batch_fd, total_read, 3);
		write_varlong30(batch_fd, total_written, 3);
		write_varlong30(batch_fd, stats.total_size, 3);
		if (protocol_version >= 29) {
			write_varlong30(batch_fd, stats.flist_buildtime, 3);
			write_varlong30(batch_fd, stats.flist_xfertime, 3);
		}
	}
}

static void output_itemized_counts(const char *prefix, int *counts)
{
	static char *labels[] = { "reg", "dir", "link", "dev", "special" };
	char buf[1024], *pre = " (";
	int j, len = 0;
	int total = counts[0];
	if (total) {
		counts[0] -= counts[1] + counts[2] + counts[3] + counts[4];
		for (j = 0; j < 5; j++) {
			if (counts[j]) {
				len += snprintf(buf+len, sizeof buf - len - 2,
					"%s%s: %s",
					pre, labels[j], comma_num(counts[j]));
				pre = ", ";
			}
		}
		buf[len++] = ')';
	}
	buf[len] = '\0';
	rprintf(FINFO, "%s: %s%s\n", prefix, comma_num(total), buf);
}

static const char *bytes_per_sec_human_dnum(void)
{
	if (starttime == (time_t)-1 || endtime == (time_t)-1)
		return "UNKNOWN";
	return human_dnum((total_written + total_read) / (0.5 + (endtime - starttime)), 2);
}

static void output_summary(void)
{
	if (INFO_GTE(STATS, 2)) {
		rprintf(FCLIENT, "\n");
		output_itemized_counts("Number of files", &stats.num_files);
		if (protocol_version >= 29)
			output_itemized_counts("Number of created files", &stats.created_files);
		if (protocol_version >= 31)
			output_itemized_counts("Number of deleted files", &stats.deleted_files);
		rprintf(FINFO,"Number of regular files transferred: %s\n",
			comma_num(stats.xferred_files));
		rprintf(FINFO,"Total file size: %s bytes\n",
			human_num(stats.total_size));
		rprintf(FINFO,"Total transferred file size: %s bytes\n",
			human_num(stats.total_transferred_size));
		rprintf(FINFO,"Literal data: %s bytes\n",
			human_num(stats.literal_data));
		rprintf(FINFO,"Matched data: %s bytes\n",
			human_num(stats.matched_data));
		rprintf(FINFO,"File list size: %s\n",
			human_num(stats.flist_size));
		if (stats.flist_buildtime) {
			rprintf(FINFO,
				"File list generation time: %s seconds\n",
				comma_dnum((double)stats.flist_buildtime / 1000, 3));
			rprintf(FINFO,
				"File list transfer time: %s seconds\n",
				comma_dnum((double)stats.flist_xfertime / 1000, 3));
		}
		rprintf(FINFO,"Total bytes sent: %s\n",
			human_num(total_written));
		rprintf(FINFO,"Total bytes received: %s\n",
			human_num(total_read));
	}

	if (INFO_GTE(STATS, 1)) {
		rprintf(FCLIENT, "\n");
		rprintf(FINFO,
			"sent %s bytes  received %s bytes  %s bytes/sec\n",
			human_num(total_written), human_num(total_read),
			bytes_per_sec_human_dnum());
		rprintf(FINFO, "total size is %s  speedup is %s%s\n",
			human_num(stats.total_size),
			comma_dnum((double)stats.total_size / (total_written+total_read), 2),
			write_batch < 0 ? " (BATCH ONLY)" : dry_run ? " (DRY RUN)" : "");
	}

	fflush(stdout);
	fflush(stderr);
}


/**
 * If our C library can get malloc statistics, then show them to FINFO
 **/
static void show_malloc_stats(void)
{
#ifdef MEM_ALLOC_INFO
	struct MEM_ALLOC_INFO mi = MEM_ALLOC_INFO(); /* mallinfo or mallinfo2 */

	rprintf(FCLIENT, "\n");
	rprintf(FINFO, RSYNC_NAME "[%d] (%s%s%s) heap statistics:\n",
		(int)getpid(), am_server ? "server " : "",
		am_daemon ? "daemon " : "", who_am_i());

#define PRINT_ALLOC_NUM(title, descr, num) \
	rprintf(FINFO, "  %-11s%10" SIZE_T_FMT_MOD "d   (" descr ")\n", \
		title ":", (SIZE_T_FMT_CAST)(num));

	PRINT_ALLOC_NUM("arena", "bytes from sbrk", mi.arena);
	PRINT_ALLOC_NUM("ordblks", "chunks not in use", mi.ordblks);
	PRINT_ALLOC_NUM("smblks", "free fastbin blocks", mi.smblks);
	PRINT_ALLOC_NUM("hblks", "chunks from mmap", mi.hblks);
	PRINT_ALLOC_NUM("hblkhd", "bytes from mmap", mi.hblkhd);
	PRINT_ALLOC_NUM("allmem", "bytes from sbrk + mmap", mi.arena + mi.hblkhd);
	PRINT_ALLOC_NUM("usmblks", "always 0", mi.usmblks);
	PRINT_ALLOC_NUM("fsmblks", "bytes in freed fastbin blocks", mi.fsmblks);
	PRINT_ALLOC_NUM("uordblks", "bytes used", mi.uordblks);
	PRINT_ALLOC_NUM("fordblks", "bytes free", mi.fordblks);
	PRINT_ALLOC_NUM("keepcost", "bytes in releasable chunk", mi.keepcost);

#undef PRINT_ALLOC_NUM

#endif /* MEM_ALLOC_INFO */
}


/* Start the remote shell.   cmd may be NULL to use the default. */
static pid_t do_cmd(char *cmd, char *machine, char *user, char **remote_argv, int remote_argc,
		    int *f_in_p, int *f_out_p)
{
	int i, argc = 0;
	char *args[MAX_ARGS], *need_to_free = NULL;
	pid_t pid;
	int dash_l_set = 0;

	if (!read_batch && !local_server) {
		char *t, *f, in_quote = '\0';
		char *rsh_env = getenv(RSYNC_RSH_ENV);
		if (!cmd)
			cmd = rsh_env;
		if (!cmd)
			cmd = RSYNC_RSH;
		cmd = need_to_free = strdup(cmd);

		for (t = f = cmd; *f; f++) {
			if (*f == ' ')
				continue;
			/* Comparison leaves rooms for server_options(). */
			if (argc >= MAX_ARGS - MAX_SERVER_ARGS)
				goto arg_overflow;
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

		/* NOTE: must preserve t == start of command name until the end of the args handling! */
		if ((t = strrchr(cmd, '/')) != NULL)
			t++;
		else
			t = cmd;

		/* Check to see if we've already been given '-l user' in the remote-shell command. */
		for (i = 0; i < argc-1; i++) {
			if (!strcmp(args[i], "-l") && args[i+1][0] != '-')
				dash_l_set = 1;
		}

#ifdef HAVE_REMSH
		/* remsh (on HPUX) takes the arguments the other way around */
		args[argc++] = machine;
		if (user && !(daemon_connection && dash_l_set)) {
			args[argc++] = "-l";
			args[argc++] = user;
		}
#else
		if (user && !(daemon_connection && dash_l_set)) {
			args[argc++] = "-l";
			args[argc++] = user;
		}
#ifdef AF_INET
		if (default_af_hint == AF_INET && strcmp(t, "ssh") == 0)
			args[argc++] = "-4"; /* we're using ssh so we can add a -4 option */
#endif
#ifdef AF_INET6
		if (default_af_hint == AF_INET6 && strcmp(t, "ssh") == 0)
			args[argc++] = "-6"; /* we're using ssh so we can add a -6 option */
#endif
		args[argc++] = machine;
#endif

		args[argc++] = rsync_path;

		if (blocking_io < 0 && (strcmp(t, "rsh") == 0 || strcmp(t, "remsh") == 0))
			blocking_io = 1;

		if (daemon_connection > 0) {
			args[argc++] = "--server";
			args[argc++] = "--daemon";
		} else
			server_options(args, &argc);

		if (argc >= MAX_ARGS - 2)
			goto arg_overflow;
	}

	args[argc++] = ".";

	if (!daemon_connection) {
		while (remote_argc > 0) {
			if (argc >= MAX_ARGS - 1) {
			  arg_overflow:
				rprintf(FERROR, "internal: args[] overflowed in do_cmd()\n");
				exit_cleanup(RERR_SYNTAX);
			}
			args[argc++] = safe_arg(NULL, *remote_argv++);
			remote_argc--;
		}
	}

	args[argc] = NULL;

	if (DEBUG_GTE(CMD, 2)) {
		for (i = 0; i < argc; i++)
			rprintf(FCLIENT, "cmd[%d]=%s ", i, args[i]);
		rprintf(FCLIENT, "\n");
	}

	if (read_batch) {
		int from_gen_pipe[2];
		set_allow_inc_recurse();
		if (fd_pair(from_gen_pipe) < 0) {
			rsyserr(FERROR, errno, "pipe");
			exit_cleanup(RERR_IPC);
		}
		batch_gen_fd = from_gen_pipe[0];
		*f_out_p = from_gen_pipe[1];
		*f_in_p = batch_fd;
		pid = (pid_t)-1; /* no child pid */
#ifdef ICONV_CONST
		setup_iconv();
#endif
	} else if (local_server) {
		/* If the user didn't request --[no-]whole-file, force
		 * it on, but only if we're not batch processing. */
		if (whole_file < 0 && !write_batch)
			whole_file = 1;
		set_allow_inc_recurse();
		pid = local_child(argc, args, f_in_p, f_out_p, child_main);
#ifdef ICONV_CONST
		setup_iconv();
#endif
	} else {
		pid = piped_child(args, f_in_p, f_out_p);
#ifdef ICONV_CONST
		setup_iconv();
#endif
		if (protect_args && !daemon_connection)
			send_protected_args(*f_out_p, args);
	}

	if (need_to_free)
		free(need_to_free);

	return pid;
}

/* Older versions turn an empty string as a reference to the current directory.
 * We now treat this as an error unless --old-args was used. */
static char *dot_dir_or_error()
{
	if (old_style_args || am_server)
		return ".";
	rprintf(FERROR, "Empty destination arg specified (use \".\" or see --old-args).\n");
	exit_cleanup(RERR_SYNTAX);
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
	int statret, trailing_slash;
	char *cp;

	if (DEBUG_GTE(RECV, 1)) {
		rprintf(FINFO, "get_local_name count=%d %s\n",
			file_total, NS(dest_path));
	}

	if (!dest_path || list_only)
		return NULL;

	if (!*dest_path)
		dest_path = dot_dir_or_error();

	if (daemon_filter_list.head) {
		char *slash = strrchr(dest_path, '/');
		if (slash && (slash[1] == '\0' || (slash[1] == '.' && slash[2] == '\0')))
			*slash = '\0';
		else
			slash = NULL;
		if ((*dest_path != '.' || dest_path[1] != '\0')
		 && (check_filter(&daemon_filter_list, FLOG, dest_path, 0) < 0
		  || check_filter(&daemon_filter_list, FLOG, dest_path, 1) < 0)) {
			rprintf(FERROR, "ERROR: daemon has excluded destination \"%s\"\n",
				dest_path);
			exit_cleanup(RERR_FILESELECT);
		}
		if (slash)
			*slash = '/';
	}

	/* See what currently exists at the destination. */
	statret = do_stat(dest_path, &st);
	cp = strrchr(dest_path, '/');
	trailing_slash = cp && !cp[1];

	if (mkpath_dest_arg && statret < 0 && (cp || file_total > 1)) {
		int save_errno = errno;
		int ret = make_path(dest_path, file_total > 1 && !trailing_slash ? 0 : MKP_DROP_NAME);
		if (ret < 0)
			goto mkdir_error;
		if (ret && (INFO_GTE(NAME, 1) || stdout_format_has_i)) {
			if (file_total == 1 || trailing_slash)
				*cp = '\0';
			rprintf(FINFO, "created %d director%s for %s\n", ret, ret == 1 ? "y" : "ies", dest_path);
			if (file_total == 1 || trailing_slash)
				*cp = '/';
		}
		if (ret)
			statret = do_stat(dest_path, &st);
		else
			errno = save_errno;
	}

	if (statret == 0) {
		/* If the destination is a dir, enter it and use mode 1. */
		if (S_ISDIR(st.st_mode)) {
			if (!change_dir(dest_path, CD_NORMAL)) {
				rsyserr(FERROR, errno, "change_dir#1 %s failed",
					full_fname(dest_path));
				exit_cleanup(RERR_FILESELECT);
			}
			filesystem_dev = st.st_dev; /* ensures --force works right w/-x */
			return NULL;
		}
		if (file_total > 1) {
			rprintf(FERROR,
				"ERROR: destination must be a directory when"
				" copying more than 1 file\n");
			exit_cleanup(RERR_FILESELECT);
		}
		if (file_total == 1 && S_ISDIR(flist->files[0]->mode)) {
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

	/* If we need a destination directory because the transfer is not
	 * of a single non-directory or the user has requested one via a
	 * destination path ending in a slash, create one and use mode 1. */
	if (file_total > 1 || trailing_slash) {
		if (trailing_slash)
			*cp = '\0'; /* Lop off the final slash (if any). */

		if (statret == 0) {
			rprintf(FERROR, "ERROR: destination path is not a directory\n");
			exit_cleanup(RERR_SYNTAX);
		}

		if (do_mkdir(dest_path, ACCESSPERMS) != 0) {
		    mkdir_error:
			rsyserr(FERROR, errno, "mkdir %s failed",
				full_fname(dest_path));
			exit_cleanup(RERR_FILEIO);
		}

		if (flist->high >= flist->low
		 && strcmp(flist->files[flist->low]->basename, ".") == 0)
			flist->files[0]->flags |= FLAG_DIR_CREATED;

		if (INFO_GTE(NAME, 1) || stdout_format_has_i)
			rprintf(FINFO, "created directory %s\n", dest_path);

		if (dry_run) {
			/* Indicate that dest dir doesn't really exist. */
			dry_run++;
		}

		if (!change_dir(dest_path, dry_run > 1 ? CD_SKIP_CHDIR : CD_NORMAL)) {
			rsyserr(FERROR, errno, "change_dir#2 %s failed",
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
	if (!change_dir(dest_path, CD_NORMAL)) {
		rsyserr(FERROR, errno, "change_dir#3 %s failed",
			full_fname(dest_path));
		exit_cleanup(RERR_FILESELECT);
	}
	*cp = '/';

	return cp + 1;
}

/* This function checks on our alternate-basis directories.  If we're in
 * dry-run mode and the destination dir does not yet exist, we'll try to
 * tweak any dest-relative paths to make them work for a dry-run (the
 * destination dir must be in curr_dir[] when this function is called).
 * We also warn about any arg that is non-existent or not a directory. */
static void check_alt_basis_dirs(void)
{
	STRUCT_STAT st;
	char *slash = strrchr(curr_dir, '/');
	int j;

	for (j = 0; j < basis_dir_cnt; j++) {
		char *bdir = basis_dir[j];
		int bd_len = strlen(bdir);
		if (bd_len > 1 && bdir[bd_len-1] == '/')
			bdir[--bd_len] = '\0';
		if (dry_run > 1 && *bdir != '/') {
			int len = curr_dir_len + 1 + bd_len + 1;
			char *new = new_array(char, len);
			if (slash && strncmp(bdir, "../", 3) == 0) {
				/* We want to remove only one leading "../" prefix for
				 * the directory we couldn't create in dry-run mode:
				 * this ensures that any other ".." references get
				 * evaluated the same as they would for a live copy. */
				*slash = '\0';
				pathjoin(new, len, curr_dir, bdir + 3);
				*slash = '/';
			} else
				pathjoin(new, len, curr_dir, bdir);
			basis_dir[j] = bdir = new;
		}
		if (do_stat(bdir, &st) < 0)
			rprintf(FWARNING, "%s arg does not exist: %s\n", alt_dest_opt(0), bdir);
		else if (!S_ISDIR(st.st_mode))
			rprintf(FWARNING, "%s arg is not a dir: %s\n", alt_dest_opt(0), bdir);
	}
}

/* This is only called by the sender. */
static void read_final_goodbye(int f_in, int f_out)
{
	int i, iflags, xlen;
	uchar fnamecmp_type;
	char xname[MAXPATHLEN];

	shutting_down = True;

	if (protocol_version < 29)
		i = read_int(f_in);
	else {
		i = read_ndx_and_attrs(f_in, f_out, &iflags, &fnamecmp_type, xname, &xlen);
		if (protocol_version >= 31 && i == NDX_DONE) {
			if (am_sender)
				write_ndx(f_out, NDX_DONE);
			else {
				if (batch_gen_fd >= 0) {
					while (read_int(batch_gen_fd) != NDX_DEL_STATS) {}
					read_del_stats(batch_gen_fd);
				}
				write_int(f_out, NDX_DONE);
			}
			i = read_ndx_and_attrs(f_in, f_out, &iflags, &fnamecmp_type, xname, &xlen);
		}
	}

	if (i != NDX_DONE) {
		rprintf(FERROR, "Invalid packet at end of run (%d) [%s]\n",
			i, who_am_i());
		exit_cleanup(RERR_PROTOCOL);
	}
}

static void do_server_sender(int f_in, int f_out, int argc, char *argv[])
{
	struct file_list *flist;
	char *dir;

	if (DEBUG_GTE(SEND, 1))
		rprintf(FINFO, "server_sender starting pid=%d\n", (int)getpid());

	if (am_daemon && lp_write_only(module_id)) {
		rprintf(FERROR, "ERROR: module is write only\n");
		exit_cleanup(RERR_SYNTAX);
	}
	if (am_daemon && read_only && remove_source_files) {
		rprintf(FERROR,
			"ERROR: --remove-%s-files cannot be used with a read-only module\n",
			remove_source_files == 1 ? "source" : "sent");
		exit_cleanup(RERR_SYNTAX);
	}
	if (argc < 1) {
		rprintf(FERROR, "ERROR: do_server_sender called without args\n");
		exit_cleanup(RERR_SYNTAX);
	}

	become_copy_as_user();

	dir = argv[0];
	if (!relative_paths) {
		if (!change_dir(dir, CD_NORMAL)) {
			rsyserr(FERROR, errno, "change_dir#3 %s failed",
				full_fname(dir));
			exit_cleanup(RERR_FILESELECT);
		}
	}
	argc--;
	argv++;

	if (argc == 0 && (recurse || xfer_dirs || list_only)) {
		argc = 1;
		argv--;
		argv[0] = ".";
	}

	flist = send_file_list(f_out,argc,argv);
	if (!flist || flist->used == 0) {
		/* Make sure input buffering is off so we can't hang in noop_io_until_death(). */
		io_end_buffering_in(0);
		/* TODO:  we should really exit in a more controlled manner. */
		exit_cleanup(0);
	}

	io_start_buffering_in(f_in);

	send_files(f_in, f_out);
	io_flush(FULL_FLUSH);
	handle_stats(f_out);
	if (protocol_version >= 24)
		read_final_goodbye(f_in, f_out);
	io_flush(FULL_FLUSH);
	exit_cleanup(0);
}


static int do_recv(int f_in, int f_out, char *local_name)
{
	int pid;
	int exit_code = 0;
	int error_pipe[2];

	/* The receiving side mustn't obey this, or an existing symlink that
	 * points to an identical file won't be replaced by the referent. */
	copy_links = copy_dirlinks = copy_unsafe_links = 0;

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && !inc_recurse)
		match_hard_links(first_flist);
#endif

	if (fd_pair(error_pipe) < 0) {
		rsyserr(FERROR, errno, "pipe failed in do_recv");
		exit_cleanup(RERR_IPC);
	}

	if (backup_dir) {
		STRUCT_STAT st;
		int ret;
		if (backup_dir_len > 1)
			backup_dir_buf[backup_dir_len-1] = '\0';
		ret = do_stat(backup_dir_buf, &st);
		if (ret != 0 || !S_ISDIR(st.st_mode)) {
			if (ret == 0) {
				rprintf(FERROR, "The backup-dir is not a directory: %s\n", backup_dir_buf);
				exit_cleanup(RERR_SYNTAX);
			}
			if (errno != ENOENT) {
				rprintf(FERROR, "Failed to stat %s: %s\n", backup_dir_buf, strerror(errno));
				exit_cleanup(RERR_FILEIO);
			}
			if (INFO_GTE(BACKUP, 1))
				rprintf(FINFO, "(new) backup_dir is %s\n", backup_dir_buf);
		} else if (INFO_GTE(BACKUP, 1))
			rprintf(FINFO, "backup_dir is %s\n", backup_dir_buf);
		if (backup_dir_len > 1)
			backup_dir_buf[backup_dir_len-1] = '/';
	}

	if (tmpdir) {
		STRUCT_STAT st;
		int ret = do_stat(tmpdir, &st);
		if (ret < 0 || !S_ISDIR(st.st_mode)) {
			if (ret == 0) {
				rprintf(FERROR, "The temp-dir is not a directory: %s\n", tmpdir);
				exit_cleanup(RERR_SYNTAX);
			}
			if (errno == ENOENT) {
				rprintf(FERROR, "The temp-dir does not exist: %s\n", tmpdir);
				exit_cleanup(RERR_SYNTAX);
			}
			rprintf(FERROR, "Failed to stat temp-dir %s: %s\n", tmpdir, strerror(errno));
			exit_cleanup(RERR_FILEIO);
		}
	}

	io_flush(FULL_FLUSH);

	if ((pid = do_fork()) == -1) {
		rsyserr(FERROR, errno, "fork failed in do_recv");
		exit_cleanup(RERR_IPC);
	}

	if (pid == 0) {
		am_receiver = 1;
		send_msgs_to_gen = am_server;

		close(error_pipe[0]);

		/* We can't let two processes write to the socket at one time. */
		io_end_multiplex_out(MPLX_SWITCHING);
		if (f_in != f_out)
			close(f_out);
		sock_f_out = -1;
		f_out = error_pipe[1];

		bwlimit_writemax = 0; /* receiver doesn't need to do this */

		if (read_batch)
			io_start_buffering_in(f_in);
		io_start_multiplex_out(f_out);

		recv_files(f_in, f_out, local_name);
		io_flush(FULL_FLUSH);
		handle_stats(f_in);

		if (output_needs_newline) {
			fputc('\n', stdout);
			output_needs_newline = 0;
		}

		write_int(f_out, NDX_DONE);
		send_msg(MSG_STATS, (char*)&stats.total_read, sizeof stats.total_read, 0);
		io_flush(FULL_FLUSH);

		/* Handle any keep-alive packets from the post-processing work
		 * that the generator does. */
		if (protocol_version >= 29) {
			kluge_around_eof = -1;

			/* This should only get stopped via a USR2 signal. */
			read_final_goodbye(f_in, f_out);

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
	implied_filter_list.head = implied_filter_list.tail = NULL;
	flist_receiving_enabled = True;

	io_end_multiplex_in(MPLX_SWITCHING);
	if (write_batch && !am_server)
		stop_write_batch();

	close(error_pipe[1]);
	if (f_in != f_out)
		close(f_in);
	sock_f_in = -1;
	f_in = error_pipe[0];

	io_start_buffering_out(f_out);
	io_start_multiplex_in(f_in);

#ifdef SUPPORT_HARD_LINKS
	if (preserve_hard_links && inc_recurse) {
		struct file_list *flist;
		for (flist = first_flist; flist; flist = flist->next)
			match_hard_links(flist);
	}
#endif

	generate_files(f_out, local_name);

	handle_stats(-1);
	io_flush(FULL_FLUSH);
	shutting_down = True;
	if (protocol_version >= 24) {
		/* send a final goodbye message */
		write_ndx(f_out, NDX_DONE);
	}
	io_flush(FULL_FLUSH);

	kill(pid, SIGUSR2);
	wait_process_with_flush(pid, &exit_code);
	return exit_code;
}

static void do_server_recv(int f_in, int f_out, int argc, char *argv[])
{
	int exit_code;
	struct file_list *flist;
	char *local_name = NULL;
	int negated_levels;

	if (filesfrom_fd >= 0 && msgs2stderr != 1 && protocol_version < 31) {
		/* We can't mix messages with files-from data on the socket,
		 * so temporarily turn off info/debug messages. */
		negate_output_levels();
		negated_levels = 1;
	} else
		negated_levels = 0;

	if (DEBUG_GTE(RECV, 1))
		rprintf(FINFO, "server_recv(%d) starting pid=%d\n", argc, (int)getpid());

	if (am_daemon && read_only) {
		rprintf(FERROR,"ERROR: module is read only\n");
		exit_cleanup(RERR_SYNTAX);
		return;
	}

	become_copy_as_user();

	if (argc > 0) {
		char *dir = argv[0];
		argc--;
		argv++;
		if (!am_daemon && !change_dir(dir, CD_NORMAL)) {
			rsyserr(FERROR, errno, "change_dir#4 %s failed",
				full_fname(dir));
			exit_cleanup(RERR_FILESELECT);
		}
	}

	if (protocol_version >= 30)
		io_start_multiplex_in(f_in);
	else
		io_start_buffering_in(f_in);
	recv_filter_list(f_in);

	if (filesfrom_fd >= 0) {
		/* We need to send the files-from names to the sender at the
		 * same time that we receive the file-list from them, so we
		 * need the IO routines to automatically write out the names
		 * onto our f_out socket as we read the file-list.  This
		 * avoids both deadlock and extra delays/buffers. */
		start_filesfrom_forwarding(filesfrom_fd);
		filesfrom_fd = -1;
	}

	flist = recv_file_list(f_in, -1);
	if (!flist) {
		rprintf(FERROR,"server_recv: recv_file_list error\n");
		exit_cleanup(RERR_FILESELECT);
	}
	if (inc_recurse && file_total == 1)
		recv_additional_file_list(f_in);

	if (negated_levels)
		negate_output_levels();

	if (argc > 0)
		local_name = get_local_name(flist,argv[0]);

	/* Now that we know what our destination directory turned out to be,
	 * we can sanitize the --link-/copy-/compare-dest args correctly. */
	if (sanitize_paths) {
		char **dir_p;
		for (dir_p = basis_dir; *dir_p; dir_p++)
			*dir_p = sanitize_path(NULL, *dir_p, NULL, curr_dir_depth, SP_DEFAULT);
		if (partial_dir)
			partial_dir = sanitize_path(NULL, partial_dir, NULL, curr_dir_depth, SP_DEFAULT);
	}
	check_alt_basis_dirs();

	if (daemon_filter_list.head) {
		char **dir_p;
		filter_rule_list *elp = &daemon_filter_list;

		for (dir_p = basis_dir; *dir_p; dir_p++) {
			char *dir = *dir_p;
			if (*dir == '/')
				dir += module_dirlen;
			if (check_filter(elp, FLOG, dir, 1) < 0)
				goto options_rejected;
		}
		if (partial_dir && *partial_dir == '/'
		 && check_filter(elp, FLOG, partial_dir + module_dirlen, 1) < 0) {
		    options_rejected:
			rprintf(FERROR, "Your options have been rejected by the server.\n");
			exit_cleanup(RERR_SYNTAX);
		}
	}

	exit_code = do_recv(f_in, f_out, local_name);
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

	if (protocol_version >= 23)
		io_start_multiplex_out(f_out);
	if (am_daemon && io_timeout && protocol_version >= 31)
		send_msg_int(MSG_IO_TIMEOUT, io_timeout);

	if (am_sender) {
		keep_dirlinks = 0; /* Must be disabled on the sender. */
		if (need_messages_from_generator)
			io_start_multiplex_in(f_in);
		else
			io_start_buffering_in(f_in);
		recv_filter_list(f_in);
		do_server_sender(f_in, f_out, argc, argv);
	} else
		do_server_recv(f_in, f_out, argc, argv);
	exit_cleanup(0);
}

/* This is called once the connection has been negotiated.  It is used
 * for rsyncd, remote-shell, and local connections. */
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

		if (always_checksum
		 && (log_format_has(stdout_format, 'C')
		  || log_format_has(logfile_format, 'C')))
			sender_keeps_checksum = 1;

		if (protocol_version >= 30)
			io_start_multiplex_out(f_out);
		else
			io_start_buffering_out(f_out);
		if (protocol_version >= 31 || (!filesfrom_host && protocol_version >= 23))
			io_start_multiplex_in(f_in);
		else
			io_start_buffering_in(f_in);
		send_filter_list(f_out);
		if (filesfrom_host)
			filesfrom_fd = f_in;

		if (write_batch && !am_server)
			start_write_batch(f_out);

		become_copy_as_user();

		flist = send_file_list(f_out, argc, argv);
		if (DEBUG_GTE(FLIST, 3))
			rprintf(FINFO,"file list sent\n");

		if (protocol_version < 31 && filesfrom_host && protocol_version >= 23)
			io_start_multiplex_in(f_in);

		io_flush(NORMAL_FLUSH);
		send_files(f_in, f_out);
		io_flush(FULL_FLUSH);
		handle_stats(-1);
		if (protocol_version >= 24)
			read_final_goodbye(f_in, f_out);
		if (pid != -1) {
			if (DEBUG_GTE(EXIT, 2))
				rprintf(FINFO,"client_run waiting on %d\n", (int) pid);
			io_flush(FULL_FLUSH);
			wait_process_with_flush(pid, &exit_code);
		}
		output_summary();
		io_flush(FULL_FLUSH);
		exit_cleanup(exit_code);
	}

	if (!read_batch) {
		if (protocol_version >= 23)
			io_start_multiplex_in(f_in);
		if (need_messages_from_generator)
			io_start_multiplex_out(f_out);
		else
			io_start_buffering_out(f_out);
	}

	become_copy_as_user();

	send_filter_list(read_batch ? -1 : f_out);

	if (filesfrom_fd >= 0) {
		start_filesfrom_forwarding(filesfrom_fd);
		filesfrom_fd = -1;
	}

	if (write_batch && !am_server)
		start_write_batch(f_in);
	flist = recv_file_list(f_in, -1);
	if (inc_recurse && file_total == 1)
		recv_additional_file_list(f_in);

	if (flist && flist->used > 0) {
		local_name = get_local_name(flist, argv[0]);

		check_alt_basis_dirs();

		exit_code2 = do_recv(f_in, f_out, local_name);
	} else {
		handle_stats(-1);
		output_summary();
	}

	if (pid != -1) {
		if (DEBUG_GTE(RECV, 1))
			rprintf(FINFO,"client_run2 waiting on %d\n", (int) pid);
		io_flush(FULL_FLUSH);
		wait_process_with_flush(pid, &exit_code);
	}

	return MAX(exit_code, exit_code2);
}

/* Start a client for either type of remote connection.  Work out
 * whether the arguments request a remote shell or rsyncd connection,
 * and call the appropriate connection function, then run_client.
 *
 * Calls either start_socket_client (for sockets) or do_cmd and
 * client_run (for ssh). */
static int start_client(int argc, char *argv[])
{
	char *p, *shell_machine = NULL, *shell_user = NULL;
	char **remote_argv;
	int remote_argc, env_port = rsync_port;
	int f_in, f_out;
	int ret;
	pid_t pid;

	if (!read_batch) { /* for read_batch, NO source is specified */
		char *path = check_for_hostspec(argv[0], &shell_machine, &rsync_port);
		if (path) { /* source is remote */
			char *dummy_host;
			int dummy_port = 0;
			*argv = path;
			remote_argv = argv;
			remote_argc = argc;
			argv += argc - 1;
			if (argc == 1 || **argv == ':')
				argc = 0; /* no dest arg */
			else if (check_for_hostspec(*argv, &dummy_host, &dummy_port)) {
				rprintf(FERROR,
					"The source and destination cannot both be remote.\n");
				exit_cleanup(RERR_SYNTAX);
			} else {
				remote_argc--; /* don't count dest */
				argc = 1;
			}
			if (filesfrom_host && *filesfrom_host && strcmp(filesfrom_host, shell_machine) != 0) {
				rprintf(FERROR,
					"--files-from hostname is not the same as the transfer hostname\n");
				exit_cleanup(RERR_SYNTAX);
			}
			am_sender = 0;
			if (rsync_port)
				daemon_connection = shell_cmd ? 1 : -1;
		} else { /* source is local, check dest arg */
			am_sender = 1;

			if (argc > 1) {
				p = argv[--argc];
				if (!*p)
					p = dot_dir_or_error();
				remote_argv = argv + argc;
			} else {
				static char *dotarg[1] = { "." };
				p = dotarg[0];
				remote_argv = dotarg;
			}
			remote_argc = 1;

			path = check_for_hostspec(p, &shell_machine, &rsync_port);
			if (path && filesfrom_host && *filesfrom_host && strcmp(filesfrom_host, shell_machine) != 0) {
				rprintf(FERROR,
					"--files-from hostname is not the same as the transfer hostname\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (!path) { /* no hostspec found, so src & dest are local */
				local_server = 1;
				if (filesfrom_host) {
					rprintf(FERROR,
						"--files-from cannot be remote when the transfer is local\n");
					exit_cleanup(RERR_SYNTAX);
				}
				shell_machine = NULL;
				rsync_port = 0;
			} else { /* hostspec was found, so dest is remote */
				argv[argc] = path;
				if (rsync_port)
					daemon_connection = shell_cmd ? 1 : -1;
			}
		}
	} else {  /* read_batch */
		local_server = 1;
		if (check_for_hostspec(argv[argc-1], &shell_machine, &rsync_port)) {
			rprintf(FERROR, "remote destination is not allowed with --read-batch\n");
			exit_cleanup(RERR_SYNTAX);
		}
		remote_argv = argv += argc - 1;
		remote_argc = argc = 1;
		rsync_port = 0;
	}

	/* A local transfer doesn't unbackslash anything, so leave the args alone. */
	if (local_server) {
		old_style_args = 2;
		trust_sender_args = trust_sender_filter = 1;
	}

	if (!rsync_port && remote_argc && !**remote_argv) /* Turn an empty arg into a dot dir. */
		*remote_argv = ".";

	if (am_sender) {
		char *dummy_host;
		int dummy_port = rsync_port;
		int i;
		if (!argv[0][0])
			goto invalid_empty;
		/* For local source, extra source args must not have hostspec. */
		for (i = 1; i < argc; i++) {
			if (!argv[i][0]) {
			    invalid_empty:
				rprintf(FERROR, "Empty source arg specified.\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (check_for_hostspec(argv[i], &dummy_host, &dummy_port)) {
				rprintf(FERROR, "Unexpected remote arg: %s\n", argv[i]);
				exit_cleanup(RERR_SYNTAX);
			}
		}
	} else {
		char *dummy_host;
		int dummy_port = rsync_port;
		int i;
		if (filesfrom_fd < 0)
			add_implied_include(remote_argv[0], daemon_connection);
		/* For remote source, any extra source args must have either
		 * the same hostname or an empty hostname. */
		for (i = 1; i < remote_argc; i++) {
			char *arg = check_for_hostspec(remote_argv[i], &dummy_host, &dummy_port);
			if (!arg) {
				rprintf(FERROR, "Unexpected local arg: %s\n", remote_argv[i]);
				rprintf(FERROR, "If arg is a remote file/dir, prefix it with a colon (:).\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (*dummy_host && strcmp(dummy_host, shell_machine) != 0) {
				rprintf(FERROR, "All source args must come from the same machine.\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (rsync_port != dummy_port) {
				if (!rsync_port || !dummy_port)
					rprintf(FERROR, "All source args must use the same hostspec format.\n");
				else
					rprintf(FERROR, "All source args must use the same port number.\n");
				exit_cleanup(RERR_SYNTAX);
			}
			if (!rsync_port && !*arg) /* Turn an empty arg into a dot dir. */
				arg = ".";
			remote_argv[i] = arg;
			add_implied_include(arg, daemon_connection);
		}
	}

	if (rsync_port < 0)
		rsync_port = RSYNC_PORT;
	else
		env_port = rsync_port;

	if (daemon_connection < 0)
		return start_socket_client(shell_machine, remote_argc, remote_argv, argc, argv);

	if (password_file && !daemon_connection) {
		rprintf(FERROR, "The --password-file option may only be "
				"used when accessing an rsync daemon.\n");
		exit_cleanup(RERR_SYNTAX);
	}

	if (connect_timeout) {
		rprintf(FERROR, "The --contimeout option may only be "
				"used when connecting to an rsync daemon.\n");
		exit_cleanup(RERR_SYNTAX);
	}

	if (shell_machine) {
		p = strrchr(shell_machine,'@');
		if (p) {
			*p = 0;
			shell_user = shell_machine;
			shell_machine = p+1;
		}
	}

	if (DEBUG_GTE(CMD, 2)) {
		rprintf(FINFO,"cmd=%s machine=%s user=%s path=%s\n",
			NS(shell_cmd), NS(shell_machine), NS(shell_user),
			NS(remote_argv[0]));
	}

#ifdef HAVE_PUTENV
	if (daemon_connection)
		set_env_num("RSYNC_PORT", env_port);
#else
	(void)env_port;
#endif

	pid = do_cmd(shell_cmd, shell_machine, shell_user, remote_argv, remote_argc, &f_in, &f_out);

	/* if we're running an rsync server on the remote host over a
	 * remote shell command, we need to do the RSYNCD protocol first */
	if (daemon_connection) {
		int tmpret;
		tmpret = start_inband_exchange(f_in, f_out, shell_user, remote_argc, remote_argv);
		if (tmpret < 0)
			return tmpret;
	}

	ret = client_run(f_in, f_out, pid, argc, argv);

	fflush(stdout);
	fflush(stderr);

	return ret;
}


static void sigusr1_handler(UNUSED(int val))
{
	called_from_signal_handler = 1;
	exit_cleanup(RERR_SIGNAL1);
}

static void sigusr2_handler(UNUSED(int val))
{
	if (!am_server)
		output_summary();
	close_all();
	if (got_xfer_error)
		_exit(RERR_PARTIAL);
	_exit(0);
}

#if defined SIGINFO || defined SIGVTALRM
static void siginfo_handler(UNUSED(int val))
{
	if (!am_server && !INFO_GTE(PROGRESS, 1))
		want_progress_now = True;
}
#endif

void remember_children(UNUSED(int val))
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
	return "xterm -display :0 -T Panic -n Panic -e gdb /proc/%d/exe %d";
}

/**
 * Handle a fatal signal by launching a debugger, controlled by $RSYNC_PANIC_ACTION.
 *
 * This signal handler is only installed if we were configured with
 * --enable-maintainer-mode.  Perhaps it should always be on and we
 * should just look at the environment variable, but I'm a bit leery
 * of a signal sending us into a busy loop.
 **/
static void rsync_panic_handler(UNUSED(int whatsig))
{
	char cmd_buf[300];
	int ret, pid_int = getpid();

	snprintf(cmd_buf, sizeof cmd_buf, get_panic_action(), pid_int, pid_int);

	/* Unless we failed to execute gdb, we allow the process to
	 * continue.  I'm not sure if that's right. */
	ret = shell_exec(cmd_buf);
	if (ret)
		_exit(ret);
}
#endif

static void unset_env_var(const char *var)
{
#ifdef HAVE_UNSETENV
	unsetenv(var);
#else
#ifdef HAVE_PUTENV
	char *mem;
	if (asprintf(&mem, "%s=", var) < 0)
		out_of_memory("unset_env_var");
	putenv(mem);
#else
	(void)var;
#endif
#endif
}


int main(int argc,char *argv[])
{
	int ret;

	raw_argc = argc;
	raw_argv = argv;

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
#ifdef SIGINFO
	SIGACTMASK(SIGINFO, siginfo_handler);
#endif
#ifdef SIGVTALRM
	SIGACTMASK(SIGVTALRM, siginfo_handler);
#endif

	starttime = time(NULL);
	our_uid = MY_UID();
	our_gid = MY_GID();
	am_root = our_uid == ROOT_UID;

	unset_env_var("DISPLAY");

#if defined USE_OPENSSL && defined SET_OPENSSL_CONF
#define TO_STR2(x) #x
#define TO_STR(x) TO_STR2(x)
	/* ./configure --with-openssl-conf=/etc/ssl/openssl-rsync.cnf
	 * defines SET_OPENSSL_CONF as that unquoted pathname. */
	if (!getenv("OPENSSL_CONF")) /* Don't override it if it's already set. */
		set_env_str("OPENSSL_CONF", TO_STR(SET_OPENSSL_CONF));
#undef TO_STR
#undef TO_STR2
#endif

	memset(&stats, 0, sizeof(stats));

	/* Even a non-daemon runs needs the default config values to be set, e.g.
	 * lp_dont_compress() is queried when no --skip-compress option is set. */
	reset_daemon_vars();

	if (argc < 2) {
		usage(FERROR);
		exit_cleanup(RERR_SYNTAX);
	}

	/* Get the umask for use in permission calculations.  We no longer set
	 * it to zero; that is ugly and pointless now that all the callers that
	 * relied on it have been reeducated to work with default ACLs. */
	umask(orig_umask = umask(0));

#if defined CONFIG_LOCALE && defined HAVE_SETLOCALE
	setlocale(LC_CTYPE, "");
	setlocale(LC_NUMERIC, "");
#endif

	if (!parse_arguments(&argc, (const char ***) &argv)) {
		option_error();
		exit_cleanup(RERR_SYNTAX);
	}
	if (write_batch
	 && poptDupArgv(argc, (const char **)argv, &cooked_argc, (const char ***)&cooked_argv) != 0)
		out_of_memory("main");

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

	/* Initialize change_dir() here because on some old systems getcwd
	 * (implemented by forking "pwd" and reading its output) doesn't
	 * work when there are other child processes.  Also, on all systems
	 * that implement getcwd that way "pwd" can't be found after chroot. */
	change_dir(NULL, CD_NORMAL);

	if ((write_batch || read_batch) && !am_server) {
		open_batch_files(); /* sets batch_fd */
		if (read_batch)
			read_stream_flags(batch_fd);
		else
			write_stream_flags(batch_fd);
	}
	if (write_batch < 0)
		dry_run = 1;

	if (am_server) {
#ifdef ICONV_CONST
		setup_iconv();
#endif
	} else if (am_daemon)
		return daemon_main();

	if (am_server && protect_args) {
		char buf[MAXPATHLEN];
		protect_args = 2;
		read_args(STDIN_FILENO, NULL, buf, sizeof buf, 1, &argv, &argc, NULL);
		if (!parse_arguments(&argc, (const char ***) &argv)) {
			option_error();
			exit_cleanup(RERR_SYNTAX);
		}
	}

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
