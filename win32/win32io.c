/*
 * Windows fd routing: pipes, sockets, and select()/poll() over both.
 *
 * rsync assumes a Unix fd space where read/write/select work uniformly over
 * files, pipes and sockets.  Windows splits those into CRT fds and Winsock
 * SOCKETs, and select() only accepts the latter.  This file bridges the gap.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"
#include <poll.h>

static int win32_trace(void)
{
	static int on = -1;

	if (on < 0) {
		const char *e = getenv("RSYNC_WIN32_DEBUG");
		on = e && *e && *e != '0';
	}
	return on;
}

#define TRACE(...) \
	do { if (win32_trace()) { \
		fprintf(stderr, "[w32] " __VA_ARGS__); \
		fflush(stderr); \
	} } while (0)

#define MAX_SOCKFDS 256

static SOCKET sock_table[MAX_SOCKFDS];
static int    sock_used[MAX_SOCKFDS];

int win32_is_sockfd(int fd)
{
	return fd >= WIN32_SOCK_BASE && fd < WIN32_SOCK_BASE + MAX_SOCKFDS;
}

SOCKET win32_sockfd_handle(int fd)
{
	int idx = fd - WIN32_SOCK_BASE;

	if (!win32_is_sockfd(fd) || !sock_used[idx])
		return INVALID_SOCKET;
	return sock_table[idx];
}

int win32_sockfd_alloc(SOCKET s)
{
	int i;

	if (s == INVALID_SOCKET)
		return -1;

	for (i = 0; i < MAX_SOCKFDS; i++) {
		if (!sock_used[i]) {
			sock_used[i] = 1;
			sock_table[i] = s;
			return WIN32_SOCK_BASE + i;
		}
	}
	closesocket(s);
	errno = EMFILE;
	return -1;
}

static void sockfd_free(int fd)
{
	int idx = fd - WIN32_SOCK_BASE;

	if (win32_is_sockfd(fd))
		sock_used[idx] = 0;
}

static int wsa_to_errno(int err)
{
	switch (err) {
	case WSAEWOULDBLOCK:   return EAGAIN;
	case WSAEINTR:         return EINTR;
	case WSAECONNRESET:    return ECONNRESET;
	case WSAECONNREFUSED:  return ECONNREFUSED;
	case WSAECONNABORTED:  return ECONNABORTED;
	case WSAENOTCONN:      return ENOTCONN;
	case WSAEADDRINUSE:    return EADDRINUSE;
	case WSAETIMEDOUT:     return ETIMEDOUT;
	case WSAEHOSTUNREACH:  return EHOSTUNREACH;
	case WSAENETUNREACH:   return ENETUNREACH;
	case WSAEAFNOSUPPORT:  return EAFNOSUPPORT;
	case WSAEINVAL:        return EINVAL;
	case WSAEACCES:        return EACCES;
	case WSAEMFILE:        return EMFILE;
	default:               return EIO;
	}
}

static int sock_fail(void)
{
	errno = wsa_to_errno(WSAGetLastError());
	return -1;
}

#define MAX_CRTFDS 2048
static unsigned char fd_nonblock[MAX_CRTFDS];

static HANDLE fd_handle(int fd)
{
	intptr_t h;

	if (fd < 0 || fd >= MAX_CRTFDS)
		return INVALID_HANDLE_VALUE;
	h = _get_osfhandle(fd);
	return h == -1 ? INVALID_HANDLE_VALUE : (HANDLE)h;
}

static int handle_is_pipe(HANDLE h)
{
	return h != INVALID_HANDLE_VALUE && GetFileType(h) == FILE_TYPE_PIPE;
}

int win32_read(int fd, void *buf, unsigned int count)
{
	HANDLE h;
	DWORD got = 0;

	if (win32_is_sockfd(fd)) {
		int n = recv(win32_sockfd_handle(fd), (char *)buf, (int)count, 0);
		if (n == SOCKET_ERROR)
			return sock_fail();
		return n;
	}

	h = fd_handle(fd);
	if (h == INVALID_HANDLE_VALUE) {
		errno = EBADF;
		return -1;
	}

	if (!handle_is_pipe(h))
		return _read(fd, buf, count);

	if (!ReadFile(h, buf, count, &got, NULL)) {
		TRACE("read(%d,%u) ReadFile err=%lu\n", fd, count, GetLastError());
		DWORD err = GetLastError();
		if (err == ERROR_BROKEN_PIPE || err == ERROR_PIPE_NOT_CONNECTED)
			return 0;
		if (err == ERROR_NO_DATA) {
			errno = EAGAIN;
			return -1;
		}
		errno = EIO;
		return -1;
	}
	TRACE("read(%d,%u) = %lu\n", fd, count, got);

	if (got == 0 && fd < MAX_CRTFDS && fd_nonblock[fd]) {
		errno = EAGAIN;
		return -1;
	}
	return (int)got;
}

int win32_write(int fd, const void *buf, unsigned int count)
{
	HANDLE h;
	DWORD put = 0;

	if (win32_is_sockfd(fd)) {
		int n = send(win32_sockfd_handle(fd), (const char *)buf, (int)count, 0);
		if (n == SOCKET_ERROR)
			return sock_fail();
		return n;
	}

	h = fd_handle(fd);
	if (h == INVALID_HANDLE_VALUE) {
		errno = EBADF;
		return -1;
	}

	if (!handle_is_pipe(h))
		return _write(fd, buf, count);

	if (!WriteFile(h, buf, count, &put, NULL)) {
		DWORD err = GetLastError();
		if (err == ERROR_BROKEN_PIPE || err == ERROR_NO_DATA) {
			errno = EPIPE;
			return -1;
		}
		errno = EIO;
		return -1;
	}
	TRACE("write(%d,%u) = %lu\n", fd, count, put);
	if (put == 0 && count > 0 && fd < MAX_CRTFDS && fd_nonblock[fd]) {
		errno = EAGAIN;
		return -1;
	}
	return (int)put;
}

int win32_close(int fd)
{
	if (win32_is_sockfd(fd)) {
		SOCKET s = win32_sockfd_handle(fd);
		sockfd_free(fd);
		if (s != INVALID_SOCKET && closesocket(s) == SOCKET_ERROR)
			return sock_fail();
		return 0;
	}
	if (fd >= 0 && fd < MAX_CRTFDS)
		fd_nonblock[fd] = 0;
	return _close(fd);
}

int win32_dup(int fd)
{
	if (win32_is_sockfd(fd)) {
		errno = ENOSYS;
		return -1;
	}
	return _dup(fd);
}

int win32_dup2(int oldfd, int newfd)
{
	if (win32_is_sockfd(oldfd) || win32_is_sockfd(newfd)) {
		errno = ENOSYS;
		return -1;
	}
	return _dup2(oldfd, newfd);
}

int win32_pipe(int fd[2])
{
	static LONG counter;
	char name[128];
	HANDLE rd, wr;
	SECURITY_ATTRIBUTES sa;

	sa.nLength = sizeof sa;
	sa.lpSecurityDescriptor = NULL;
	sa.bInheritHandle = TRUE;

	snprintf(name, sizeof name, "\\\\.\\pipe\\rsync-%lu-%ld",
		 (unsigned long)GetCurrentProcessId(),
		 (long)InterlockedIncrement(&counter));

	rd = CreateNamedPipeA(name, PIPE_ACCESS_INBOUND,
			      PIPE_TYPE_BYTE | PIPE_WAIT, 1,
			      65536, 65536, 0, &sa);
	if (rd == INVALID_HANDLE_VALUE) {
		errno = EMFILE;
		return -1;
	}

	wr = CreateFileA(name, GENERIC_WRITE, 0, &sa, OPEN_EXISTING,
			 FILE_ATTRIBUTE_NORMAL, NULL);
	if (wr == INVALID_HANDLE_VALUE) {
		CloseHandle(rd);
		errno = EMFILE;
		return -1;
	}

	fd[0] = _open_osfhandle((intptr_t)rd, _O_RDONLY | _O_BINARY);
	fd[1] = _open_osfhandle((intptr_t)wr, _O_BINARY);
	if (fd[0] < 0 || fd[1] < 0) {
		if (fd[0] >= 0) _close(fd[0]); else CloseHandle(rd);
		if (fd[1] >= 0) _close(fd[1]); else CloseHandle(wr);
		errno = EMFILE;
		return -1;
	}
	return 0;
}

int win32_socketpair(int domain, int type, int protocol, int sv[2])
{
	SOCKET listener = INVALID_SOCKET, a = INVALID_SOCKET, b = INVALID_SOCKET;
	struct sockaddr_in addr;
	int len = sizeof addr;

	(void)domain; (void)protocol;

	listener = socket(AF_INET, type, 0);
	if (listener == INVALID_SOCKET)
		return sock_fail();

	memset(&addr, 0, sizeof addr);
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	addr.sin_port = 0;

	if (bind(listener, (struct sockaddr *)&addr, sizeof addr) == SOCKET_ERROR
	 || listen(listener, 1) == SOCKET_ERROR
	 || getsockname(listener, (struct sockaddr *)&addr, &len) == SOCKET_ERROR)
		goto fail;

	a = socket(AF_INET, type, 0);
	if (a == INVALID_SOCKET)
		goto fail;
	if (connect(a, (struct sockaddr *)&addr, sizeof addr) == SOCKET_ERROR)
		goto fail;

	b = accept(listener, NULL, NULL);
	if (b == INVALID_SOCKET)
		goto fail;

	closesocket(listener);

	sv[0] = win32_sockfd_alloc(a);
	sv[1] = win32_sockfd_alloc(b);
	if (sv[0] < 0 || sv[1] < 0) {
		if (sv[0] >= 0)
			win32_close(sv[0]);
		if (sv[1] >= 0)
			win32_close(sv[1]);
		sv[0] = sv[1] = -1;
		errno = EMFILE;
		return -1;
	}
	return 0;

fail:
	{
		int saved = WSAGetLastError();
		if (listener != INVALID_SOCKET) closesocket(listener);
		if (a != INVALID_SOCKET) closesocket(a);
		if (b != INVALID_SOCKET) closesocket(b);
		WSASetLastError(saved);
	}
	return sock_fail();
}

int win32_fcntl(int fd, int cmd, ...)
{
	long arg = 0;

	if (cmd == F_SETFL || cmd == F_SETFD) {
		va_list ap;

		va_start(ap, cmd);
		arg = va_arg(ap, long);
		va_end(ap);
	}

	switch (cmd) {
	case F_GETFL:
		if (win32_is_sockfd(fd))
			return 0;
		return (fd >= 0 && fd < MAX_CRTFDS && fd_nonblock[fd]) ? O_NONBLOCK : 0;

	case F_SETFL: {
		int want = (arg & O_NONBLOCK) ? 1 : 0;

		if (win32_is_sockfd(fd)) {
			u_long mode = want;
			if (ioctlsocket(win32_sockfd_handle(fd), FIONBIO, &mode) == SOCKET_ERROR)
				return sock_fail();
			return 0;
		}
		{
			HANDLE h = fd_handle(fd);
			if (handle_is_pipe(h)) {
				DWORD mode = PIPE_READMODE_BYTE
					   | (want ? PIPE_NOWAIT : PIPE_WAIT);
				if (!SetNamedPipeHandleState(h, &mode, NULL, NULL)) {

				}
			}
			if (fd >= 0 && fd < MAX_CRTFDS)
				fd_nonblock[fd] = (unsigned char)want;
		}
		return 0;
	}

	case F_GETFD:
		return 0;
	case F_SETFD:
		return 0;
	case F_SETLK:
		return 0;
	default:
		errno = EINVAL;
		return -1;
	}
}

static int crtfd_readable(int fd)
{
	HANDLE h = fd_handle(fd);
	DWORD avail = 0;

	if (h == INVALID_HANDLE_VALUE)
		return 1;

	switch (GetFileType(h)) {
	case FILE_TYPE_PIPE:
		if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL))
			return 1;
		return avail > 0;
	case FILE_TYPE_CHAR:
		return WaitForSingleObject(h, 0) == WAIT_OBJECT_0;
	default:
		return 1;
	}
}

static int crtfd_writable(int fd)
{
	HANDLE h = fd_handle(fd);

	return h != INVALID_HANDLE_VALUE;
}

int win32_poll(struct pollfd *fds, nfds_t nfds, int timeout)
{
	fd_set rf, wf, ef;
	struct timeval tv, *tvp;
	nfds_t i;
	int rc, count = 0;

	FD_ZERO(&rf);
	FD_ZERO(&wf);
	FD_ZERO(&ef);

	for (i = 0; i < nfds; i++) {
		fds[i].revents = 0;
		if (fds[i].fd < 0)
			continue;
		if (fds[i].events & (POLLIN | POLLPRI))
			FD_SET((SOCKET)fds[i].fd, &rf);
		if (fds[i].events & POLLOUT)
			FD_SET((SOCKET)fds[i].fd, &wf);

		if (win32_is_sockfd(fds[i].fd))
			FD_SET((SOCKET)fds[i].fd, &ef);
	}

	if (timeout < 0)
		tvp = NULL;
	else {
		tv.tv_sec  = timeout / 1000;
		tv.tv_usec = (timeout % 1000) * 1000;
		tvp = &tv;
	}

	rc = win32_select(0, &rf, &wf, &ef, tvp);
	if (rc <= 0)
		return rc;

	for (i = 0; i < nfds; i++) {
		if (fds[i].fd < 0)
			continue;
		if (FD_ISSET((SOCKET)fds[i].fd, &rf))
			fds[i].revents |= POLLIN;
		if (FD_ISSET((SOCKET)fds[i].fd, &wf))
			fds[i].revents |= POLLOUT;
		if (FD_ISSET((SOCKET)fds[i].fd, &ef))
			fds[i].revents |= POLLERR;
		if (fds[i].revents)
			count++;
	}

	TRACE("poll nfds=%lu timeout=%d -> %d\n", (unsigned long)nfds, timeout, count);
	return count;
}

int win32_select(int nfds, fd_set *rfds, fd_set *wfds, fd_set *efds,
		 struct timeval *tv)
{
	fd_set sock_r, sock_w, sock_e;
	int    crt_r[FD_SETSIZE], crt_w[FD_SETSIZE];
	int    n_crt_r = 0, n_crt_w = 0;
	int    have_socks = 0;
	unsigned int i;
	DWORD  deadline = 0;
	int    infinite = (tv == NULL);
	DWORD  waited = 0;

	(void)nfds;

	TRACE("select nfds=%d r=%u w=%u e=%u tv=%ld.%06ld\n", nfds,
	      rfds ? rfds->fd_count : 0, wfds ? wfds->fd_count : 0,
	      efds ? efds->fd_count : 0,
	      tv ? (long)tv->tv_sec : -1L, tv ? (long)tv->tv_usec : 0L);

	FD_ZERO(&sock_r);
	FD_ZERO(&sock_w);
	FD_ZERO(&sock_e);

	if (rfds) {
		for (i = 0; i < rfds->fd_count; i++) {
			int fd = (int)rfds->fd_array[i];
			if (win32_is_sockfd(fd)) {
				FD_SET(win32_sockfd_handle(fd), &sock_r);
				have_socks = 1;
			} else if (n_crt_r < FD_SETSIZE)
				crt_r[n_crt_r++] = fd;
		}
	}
	if (wfds) {
		for (i = 0; i < wfds->fd_count; i++) {
			int fd = (int)wfds->fd_array[i];
			if (win32_is_sockfd(fd)) {
				FD_SET(win32_sockfd_handle(fd), &sock_w);
				have_socks = 1;
			} else if (n_crt_w < FD_SETSIZE)
				crt_w[n_crt_w++] = fd;
		}
	}
	if (efds) {
		for (i = 0; i < efds->fd_count; i++) {
			int fd = (int)efds->fd_array[i];
			if (win32_is_sockfd(fd)) {
				FD_SET(win32_sockfd_handle(fd), &sock_e);
				have_socks = 1;
			}
		}
	}

	if (!have_socks && n_crt_r == 0 && n_crt_w == 0) {
		if (rfds) FD_ZERO(rfds);
		if (wfds) FD_ZERO(wfds);
		if (efds) FD_ZERO(efds);
		if (tv)
			Sleep((DWORD)(tv->tv_sec * 1000 + tv->tv_usec / 1000));
		else
			Sleep(INFINITE);
		return 0;
	}

	if (!infinite)
		deadline = GetTickCount() + (DWORD)(tv->tv_sec * 1000 + tv->tv_usec / 1000);

	if (have_socks && n_crt_r == 0 && n_crt_w == 0) {
		int rc = select(0, rfds ? &sock_r : NULL, wfds ? &sock_w : NULL,
				efds ? &sock_e : NULL, tv);
		if (rc == SOCKET_ERROR)
			return sock_fail();

		{
			fd_set out_r, out_w, out_e;
			FD_ZERO(&out_r); FD_ZERO(&out_w); FD_ZERO(&out_e);
			if (rfds) {
				for (i = 0; i < rfds->fd_count; i++) {
					int fd = (int)rfds->fd_array[i];
					if (FD_ISSET(win32_sockfd_handle(fd), &sock_r))
						FD_SET((SOCKET)fd, &out_r);
				}
				*rfds = out_r;
			}
			if (wfds) {
				for (i = 0; i < wfds->fd_count; i++) {
					int fd = (int)wfds->fd_array[i];
					if (FD_ISSET(win32_sockfd_handle(fd), &sock_w))
						FD_SET((SOCKET)fd, &out_w);
				}
				*wfds = out_w;
			}
			if (efds) {
				for (i = 0; i < efds->fd_count; i++) {
					int fd = (int)efds->fd_array[i];
					if (FD_ISSET(win32_sockfd_handle(fd), &sock_e))
						FD_SET((SOCKET)fd, &out_e);
				}
				*efds = out_e;
			}
		}
		return rc;
	}

	for (;;) {
		fd_set out_r, out_w, out_e;
		int count = 0;
		int j;

		FD_ZERO(&out_r);
		FD_ZERO(&out_w);
		FD_ZERO(&out_e);

		for (j = 0; j < n_crt_r; j++) {
			if (crtfd_readable(crt_r[j])) {
				FD_SET((SOCKET)crt_r[j], &out_r);
				count++;
			}
		}
		for (j = 0; j < n_crt_w; j++) {
			if (crtfd_writable(crt_w[j])) {
				FD_SET((SOCKET)crt_w[j], &out_w);
				count++;
			}
		}

		if (have_socks) {
			fd_set try_r = sock_r, try_w = sock_w, try_e = sock_e;
			struct timeval zero;
			int rc;

			zero.tv_sec = 0;
			zero.tv_usec = 0;
			rc = select(0, rfds ? &try_r : NULL, wfds ? &try_w : NULL,
				    efds ? &try_e : NULL, &zero);
			if (rc == SOCKET_ERROR)
				return sock_fail();
			if (rc > 0) {
				if (rfds) {
					for (i = 0; i < rfds->fd_count; i++) {
						int fd = (int)rfds->fd_array[i];
						if (win32_is_sockfd(fd)
						 && FD_ISSET(win32_sockfd_handle(fd), &try_r)) {
							FD_SET((SOCKET)fd, &out_r);
							count++;
						}
					}
				}
				if (wfds) {
					for (i = 0; i < wfds->fd_count; i++) {
						int fd = (int)wfds->fd_array[i];
						if (win32_is_sockfd(fd)
						 && FD_ISSET(win32_sockfd_handle(fd), &try_w)) {
							FD_SET((SOCKET)fd, &out_w);
							count++;
						}
					}
				}
				if (efds) {
					for (i = 0; i < efds->fd_count; i++) {
						int fd = (int)efds->fd_array[i];
						if (win32_is_sockfd(fd)
						 && FD_ISSET(win32_sockfd_handle(fd), &try_e)) {
							FD_SET((SOCKET)fd, &out_e);
							count++;
						}
					}
				}
			}
		}

		if (count > 0) {
			TRACE("select -> %d (poll)\n", count);
			if (rfds) *rfds = out_r;
			if (wfds) *wfds = out_w;
			if (efds) *efds = out_e;
			return count;
		}

		if (!infinite) {
			DWORD now = GetTickCount();
			if ((long)(now - deadline) >= 0) {
				if (rfds) FD_ZERO(rfds);
				if (wfds) FD_ZERO(wfds);
				if (efds) FD_ZERO(efds);
				return 0;
			}
		}

		Sleep(waited < 20 ? 1 : 5);
		if (waited < 1000)
			waited++;
	}
}

static int sock_result(int rc)
{
	return rc == SOCKET_ERROR ? sock_fail() : 0;
}

int win32_socket(int af, int type, int protocol)
{
	SOCKET s = socket(af, type, protocol);

	if (s == INVALID_SOCKET)
		return sock_fail();
	return win32_sockfd_alloc(s);
}

int win32_accept(int fd, struct sockaddr *addr, socklen_t *addrlen)
{
	SOCKET s = accept(win32_sockfd_handle(fd), addr, addrlen);

	if (s == INVALID_SOCKET)
		return sock_fail();
	return win32_sockfd_alloc(s);
}

int win32_bind(int fd, const struct sockaddr *addr, socklen_t addrlen)
{
	return sock_result(bind(win32_sockfd_handle(fd), addr, addrlen));
}

int win32_connect(int fd, const struct sockaddr *addr, socklen_t addrlen)
{
	int rc = connect(win32_sockfd_handle(fd), addr, addrlen);

	if (rc == SOCKET_ERROR) {
		int err = WSAGetLastError();

		if (err == WSAEWOULDBLOCK || err == WSAEINPROGRESS || err == WSAEALREADY) {
			errno = EINPROGRESS;
			return -1;
		}
		return sock_fail();
	}
	return 0;
}

int win32_listen(int fd, int backlog)
{
	return sock_result(listen(win32_sockfd_handle(fd), backlog));
}

int win32_setsockopt(int fd, int level, int opt, const void *val, socklen_t len)
{
	return sock_result(setsockopt(win32_sockfd_handle(fd), level, opt, (const char *)val, len));
}

int win32_getsockopt(int fd, int level, int opt, void *val, socklen_t *len)
{
	int rc = getsockopt(win32_sockfd_handle(fd), level, opt, (char *)val, len);

	if (rc == SOCKET_ERROR)
		return sock_fail();

	if (level == SOL_SOCKET && opt == SO_ERROR && val && len
	 && *len >= (socklen_t)sizeof(int) && *(int *)val != 0)
		*(int *)val = wsa_to_errno(*(int *)val);
	return 0;
}

int win32_getpeername(int fd, struct sockaddr *addr, socklen_t *len)
{
	return sock_result(getpeername(win32_sockfd_handle(fd), addr, len));
}

int win32_getsockname(int fd, struct sockaddr *addr, socklen_t *len)
{
	return sock_result(getsockname(win32_sockfd_handle(fd), addr, len));
}

int win32_shutdown(int fd, int how)
{
	return sock_result(shutdown(win32_sockfd_handle(fd), how));
}
