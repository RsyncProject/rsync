/*
 * End-of-run cleanup routines.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003-2015 Wayne Davison
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

extern int dry_run;
extern int am_server;
extern int am_daemon;
extern int am_receiver;
extern int io_error;
extern int keep_partial;
extern int got_xfer_error;
extern int protocol_version;
extern int output_needs_newline;
extern char *partial_dir;
extern char *logfile_name;

BOOL shutting_down = False;
BOOL flush_ok_after_signal = False;

#ifdef HAVE_SIGACTION
static struct sigaction sigact;
#endif

/**
 * Close all open sockets and files, allowing a (somewhat) graceful
 * shutdown() of socket connections.  This eliminates the abortive
 * TCP RST sent by a Winsock-based system when the close() occurs.
 **/
void close_all(void)
{
#ifdef SHUTDOWN_ALL_SOCKETS
	int max_fd;
	int fd;
	int ret;
	STRUCT_STAT st;

	max_fd = sysconf(_SC_OPEN_MAX) - 1;
	for (fd = max_fd; fd >= 0; fd--) {
		if ((ret = do_fstat(fd, &st)) == 0) {
			if (is_a_socket(fd))
				ret = shutdown(fd, 2);
			ret = close(fd);
		}
	}
#endif
}

/**
 * @file cleanup.c
 *
 * Code for handling interrupted transfers.  Depending on the @c
 * --partial option, we may either delete the temporary file, or go
 * ahead and overwrite the destination.  This second behaviour only
 * occurs if we've sent literal data and therefore hopefully made
 * progress on the transfer.
 **/

/**
 * Set to True once literal data has been sent across the link for the
 * current file. (????)
 *
 * Handling the cleanup when a transfer is interrupted is tricky when
 * --partial is selected.  We need to ensure that the partial file is
 * kept if any real data has been transferred.
 **/
int cleanup_got_literal = 0;

static const char *cleanup_fname;
static const char *cleanup_new_fname;
static struct file_struct *cleanup_file;
static int cleanup_fd_r = -1, cleanup_fd_w = -1;
static pid_t cleanup_pid = 0;

pid_t cleanup_child_pid = -1;

/**
 * Eventually calls exit(), passing @p code, therefore does not return.
 *
 * @param code one of the RERR_* codes from errcode.h.
 **/
NORETURN void _exit_cleanup(int code, const char *file, int line)
{
	static int switch_step = 0;
	static int exit_code = 0, exit_line = 0;
	static const char *exit_file = NULL;
	static int first_code = 0;

	SIGACTION(SIGUSR1, SIG_IGN);
	SIGACTION(SIGUSR2, SIG_IGN);

	if (!exit_code) { /* Preserve first error exit info when recursing. */
		exit_code = code;
		exit_file = file;
		exit_line = line < 0 ? -line : line;
	}

	/* If this is the exit at the end of the run, the server side
	 * should not attempt to output a message (see log_exit()). */
	if (am_server && code == 0)
		am_server = 2;

	/* Some of our actions might cause a recursive call back here, so we
	 * keep track of where we are in the cleanup and never repeat a step. */
	switch (switch_step) {
#include "case_N.h" /* case 0: */
		switch_step++;

		first_code = code;

		if (output_needs_newline) {
			fputc('\n', stdout);
			output_needs_newline = 0;
		}

		if (DEBUG_GTE(EXIT, 2)) {
			rprintf(FINFO,
				"[%s] _exit_cleanup(code=%d, file=%s, line=%d): entered\n",
				who_am_i(), code, file, line);
		}

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (cleanup_child_pid != -1) {
			int status;
			int pid = wait_process(cleanup_child_pid, &status, WNOHANG);
			if (pid == cleanup_child_pid) {
				status = WEXITSTATUS(status);
				if (status > exit_code)
					exit_code = status;
			}
		}

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (cleanup_got_literal && (cleanup_fname || cleanup_fd_w != -1)) {
			if (cleanup_fd_r != -1) {
				close(cleanup_fd_r);
				cleanup_fd_r = -1;
			}
			if (cleanup_fd_w != -1) {
				flush_write_file(cleanup_fd_w);
				close(cleanup_fd_w);
				cleanup_fd_w = -1;
			}
			if (cleanup_fname && cleanup_new_fname && keep_partial
			 && handle_partial_dir(cleanup_new_fname, PDIR_CREATE)) {
				int tweak_modtime = 0;
				const char *fname = cleanup_fname;
				cleanup_fname = NULL;
				if (!partial_dir) {
				    /* We don't want to leave a partial file with a modern time or it
				     * could be skipped via --update.  Setting the time to something
				     * really old also helps it to stand out as unfinished in an ls. */
				    tweak_modtime = 1;
				    cleanup_file->modtime = 0;
				}
				finish_transfer(cleanup_new_fname, fname, NULL, NULL,
						cleanup_file, tweak_modtime, !partial_dir);
			}
		}

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (flush_ok_after_signal) {
			flush_ok_after_signal = False;
			if (code == RERR_SIGNAL)
				io_flush(FULL_FLUSH);
		}
		if (!exit_code && !code)
			io_flush(FULL_FLUSH);

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (cleanup_fname)
			do_unlink(cleanup_fname);
		if (exit_code)
			kill_all(SIGUSR1);
		if (cleanup_pid && cleanup_pid == getpid()) {
			char *pidf = lp_pid_file();
			if (pidf && *pidf)
				unlink(lp_pid_file());
		}

		if (exit_code == 0) {
			if (code)
				exit_code = code;
			if (io_error & IOERR_DEL_LIMIT)
				exit_code = RERR_DEL_LIMIT;
			if (io_error & IOERR_VANISHED)
				exit_code = RERR_VANISHED;
			if (io_error & IOERR_GENERAL || got_xfer_error)
				exit_code = RERR_PARTIAL;
		}

		/* If line < 0, this exit is after a MSG_ERROR_EXIT event, so
		 * we don't want to output a duplicate error. */
		if ((exit_code && line > 0)
		 || am_daemon || (logfile_name && (am_server || !INFO_GTE(STATS, 1))))
			log_exit(exit_code, exit_file, exit_line);

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (DEBUG_GTE(EXIT, 1)) {
			rprintf(FINFO,
				"[%s] _exit_cleanup(code=%d, file=%s, line=%d): "
				"about to call exit(%d)%s\n",
				who_am_i(), first_code, exit_file, exit_line, exit_code,
				dry_run ? " (DRY RUN)" : "");
		}

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (exit_code && exit_code != RERR_SOCKETIO && exit_code != RERR_STREAMIO && exit_code != RERR_SIGNAL1
		 && exit_code != RERR_TIMEOUT && !shutting_down && (protocol_version >= 31 || am_receiver)) {
			if (line > 0) {
				if (DEBUG_GTE(EXIT, 3)) {
					rprintf(FINFO, "[%s] sending MSG_ERROR_EXIT with exit_code %d\n",
						who_am_i(), exit_code);
				}
				send_msg_int(MSG_ERROR_EXIT, exit_code);
			}
			noop_io_until_death();
		}

		/* FALLTHROUGH */
#include "case_N.h"
		switch_step++;

		if (am_server && exit_code)
			msleep(100);
		close_all();

		/* FALLTHROUGH */
	default:
		break;
	}

	exit(exit_code);
}

void cleanup_disable(void)
{
	cleanup_fname = cleanup_new_fname = NULL;
	cleanup_fd_r = cleanup_fd_w = -1;
	cleanup_got_literal = 0;
}


void cleanup_set(const char *fnametmp, const char *fname, struct file_struct *file,
		 int fd_r, int fd_w)
{
	cleanup_fname = fnametmp;
	cleanup_new_fname = fname; /* can be NULL on a partial-dir failure */
	cleanup_file = file;
	cleanup_fd_r = fd_r;
	cleanup_fd_w = fd_w;
}

void cleanup_set_pid(pid_t pid)
{
	cleanup_pid = pid;
}
