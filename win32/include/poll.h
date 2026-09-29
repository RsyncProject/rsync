/*
 * <poll.h> for the Windows build.
 *
 * rsync 3.5.0 moved io.c off select() and onto poll(), to escape select()'s
 * FD_SETSIZE ceiling on a high-numbered fd.  Windows has WSAPoll(), but it
 * waits on SOCKETs only, and rsync polls pipes and CRT fds alongside its
 * socket -- which is the gap win32_select() already bridges.  So poll() is
 * expressed in terms of that rather than the other way round.
 *
 * struct pollfd and the POLL* constants normally come from winsock2.h, which
 * win32compat.h includes; they appear there only when _WIN32_WINNT is at
 * least 0x0600, so this supplies them if the SDK did not.  See
 * win32/include/unistd.h for why these shims exist.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_POLL_H
#define RSYNC_WIN32_SHIM_POLL_H

#include "win32/win32compat.h"

#ifndef POLLRDNORM
#define POLLRDNORM  0x0100
#define POLLRDBAND  0x0200
#define POLLIN      (POLLRDNORM | POLLRDBAND)
#define POLLPRI     0x0400
#define POLLWRNORM  0x0010
#define POLLOUT     (POLLWRNORM)
#define POLLWRBAND  0x0020
#define POLLERR     0x0001
#define POLLHUP     0x0002
#define POLLNVAL    0x0004
struct pollfd {
	int   fd;
	short events;
	short revents;
};
#endif

typedef unsigned long nfds_t;

int win32_poll(struct pollfd *fds, nfds_t nfds, int timeout);

#define poll(f, n, t) win32_poll((f), (n), (t))

#endif
