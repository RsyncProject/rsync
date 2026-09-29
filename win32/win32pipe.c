/*
 * Windows replacement for pipe.c.
 *
 * This file is linked instead of pipe.c, not alongside it, so the two are
 * alternative implementations of the same small interface:
 *
 *   piped_child()          spawn the remote shell on a pipe pair
 *   local_child()          spawn the in-process server for a local copy
 *   spawn_receiver_half()  split do_recv() into generator and receiver
 *   receiver_half_finish() end the receiving half
 *   inc_recurse_when_receiving
 *
 * pipe.c does all of that with fork(); none of it exists on Windows, so the
 * remote shell goes through CreateProcess and the generator/receiver split
 * goes through a thread (win32/win32fork.c).  Keeping both versions whole,
 * rather than interleaving them with #ifdef, is what lets the shared sources
 * stay platform-agnostic.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

extern int protocol_version;
extern int am_server;
extern RSYNC_TLS int kluge_around_eof;
extern RSYNC_TLS BOOL shutting_down;

int inc_recurse_when_receiving = 0;

int local_server_shares_memory = 0;

pid_t piped_child(char **command, int *f_in, int *f_out)
{
	pid_t pid;

	if (DEBUG_GTE(CMD, 1))
		print_child_argv("opening connection using:", command);

	pid = win32_piped_child(command, f_in, f_out);
	if (pid == -1) {
		rsyserr(FERROR, errno, "Failed to exec %s", command[0]);
		exit_cleanup(RERR_IPC);
	}

	set_blocking(*f_out);
	return pid;
}

pid_t local_child(int argc, char **argv, int *f_in, int *f_out,
		  int (*child_main)(int, char*[]))
{
	char *args[MAX_ARGS];
	char self[MAXPATHLEN];
	int i, ac = 0;
	pid_t pid;

	(void)child_main;

	if (!GetModuleFileNameA(NULL, self, sizeof self)) {
		rprintf(FERROR, "failed to determine our own executable path\n");
		exit_cleanup(RERR_IPC);
	}

	args[ac++] = self;
	server_options(args, &ac);

	if (ac + argc >= MAX_ARGS) {
		rprintf(FERROR, "argc overflow in local_child().\n");
		exit_cleanup(RERR_MALLOC);
	}
	for (i = 0; i < argc; i++)
		args[ac++] = argv[i];
	args[ac] = NULL;

	if (DEBUG_GTE(CMD, 1))
		print_child_argv("starting local server:", args);

	pid = win32_piped_child(args, f_in, f_out);
	if (pid == -1) {
		rsyserr(FERROR, errno, "failed to start the local server");
		exit_cleanup(RERR_IPC);
	}

	set_blocking(*f_out);
	return pid;
}

struct recv_half_args {
	int f_in, f_out;
	char *local_name;
	int error_pipe_r, error_pipe_w;
};

static void receiver_half_entry(void *arg)
{
	struct recv_half_args *a = (struct recv_half_args *)arg;

	receiver_half(a->f_in, a->f_out, a->local_name,
		      a->error_pipe_r, a->error_pipe_w);
}

pid_t spawn_receiver_half(int f_in, int f_out, char *local_name,
			  int error_pipe_r, int error_pipe_w)
{

	static struct recv_half_args args;

	args.f_in = f_in;
	args.f_out = f_out;
	args.local_name = local_name;
	args.error_pipe_r = error_pipe_r;
	args.error_pipe_w = error_pipe_w;

	return win32_fork_thread(receiver_half_entry, &args);
}

void receiver_half_finish(int f_in, int f_out)
{
	if (protocol_version >= 29) {
		uchar fnamecmp_type;
		char xname[MAXPATHLEN];
		int iflags, xlen, i;

		kluge_around_eof = -1;
		shutting_down = True;

		i = read_ndx_and_attrs(f_in, f_out, &iflags, &fnamecmp_type,
				       xname, &xlen);
		if (protocol_version >= 31 && i == NDX_DONE)
			write_int(f_out, NDX_DONE);
		io_flush(FULL_FLUSH);
	}

	if (!am_server)
		output_summary();
}
