/* -*- c-file-style: "linux" -*-

   Copyright (C) 1996-2000 by Andrew Tridgell
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2002 by Martin Pool

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

extern int io_error;
extern int keep_partial;
extern int log_got_error;

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
	struct stat st;

	max_fd = sysconf(_SC_OPEN_MAX) - 1;
	for (fd = max_fd; fd >= 0; fd--) {
		ret = fstat(fd,&st);
		if (fstat(fd,&st) == 0) {
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

static char *cleanup_fname;
static char *cleanup_new_fname;
static struct file_struct *cleanup_file;
static int cleanup_fd_r, cleanup_fd_w;
static pid_t cleanup_pid = 0;

pid_t cleanup_child_pid = -1;

/**
 * Eventually calls exit(), passing @p code, therefore does not return.
 *
 * @param code one of the RERR_* codes from errcode.h.
 **/
void _exit_cleanup(int code, const char *file, int line)
{
	int ocode = code;
	static int inside_cleanup = 0;

	if (inside_cleanup > 10) {
		/* prevent the occasional infinite recursion */
		return;
	}
	inside_cleanup++;

	signal(SIGUSR1, SIG_IGN);
	signal(SIGUSR2, SIG_IGN);

	if (verbose > 3) {
		rprintf(FINFO,"_exit_cleanup(code=%d, file=%s, line=%d): entered\n",
			code, file, line);
	}

	if (cleanup_child_pid != -1) {
		int status;
		if (waitpid(cleanup_child_pid, &status, WNOHANG) == cleanup_child_pid) {
			status = WEXITSTATUS(status);
			if (status > code)
				code = status;
		}
	}

	if (cleanup_got_literal && cleanup_fname && keep_partial
	    && handle_partial_dir(cleanup_new_fname, PDIR_CREATE)) {
		char *fname = cleanup_fname;
		cleanup_fname = NULL;
		if (cleanup_fd_r != -1)
			close(cleanup_fd_r);
		if (cleanup_fd_w != -1)
			close(cleanup_fd_w);
		finish_transfer(cleanup_new_fname, fname, cleanup_file, 0);
	}
	io_flush(FULL_FLUSH);
	if (cleanup_fname)
		do_unlink(cleanup_fname);
	if (code)
		kill_all(SIGUSR1);
	if (cleanup_pid && cleanup_pid == getpid()) {
		char *pidf = lp_pid_file();
		if (pidf && *pidf)
			unlink(lp_pid_file());
	}

	if (code == 0) {
		if ((io_error & ~IOERR_VANISHED) || log_got_error)
			code = RERR_PARTIAL;
		else if (io_error)
			code = RERR_VANISHED;
	}

	if (code)
		log_exit(code, file, line);

	if (verbose > 2) {
		rprintf(FINFO,"_exit_cleanup(code=%d, file=%s, line=%d): about to call exit(%d)\n",
			ocode, file, line, code);
	}

	close_all();
	exit(code);
}

void cleanup_disable(void)
{
	cleanup_fname = NULL;
	cleanup_got_literal = 0;
}


void cleanup_set(char *fnametmp, char *fname, struct file_struct *file,
		 int fd_r, int fd_w)
{
	cleanup_fname = fnametmp;
	cleanup_new_fname = fname;
	cleanup_file = file;
	cleanup_fd_r = fd_r;
	cleanup_fd_w = fd_w;
}

void cleanup_set_pid(pid_t pid)
{
	cleanup_pid = pid;
}
