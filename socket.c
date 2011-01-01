/*
 * Socket functions used in rsync.
 *
 * Copyright (C) 1992-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2009 Wayne Davison
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

/* This file is now converted to use the new-style getaddrinfo()
 * interface, which supports IPv6 but is also supported on recent
 * IPv4-only machines.  On systems that don't have that interface, we
 * emulate it using the KAME implementation. */

#include "rsync.h"
#include "ifuncs.h"
#include <netinet/in_systm.h>
#include <netinet/ip.h>
#include <netinet/tcp.h>

extern char *bind_address;
extern char *sockopts;
extern int default_af_hint;
extern int connect_timeout;

#ifdef HAVE_SIGACTION
static struct sigaction sigact;
#endif

static int sock_exec(const char *prog);

/* Establish a proxy connection on an open socket to a web proxy by using the
 * CONNECT method.  If proxy_user and proxy_pass are not NULL, they are used to
 * authenticate to the proxy using the "Basic" proxy-authorization protocol. */
static int establish_proxy_connection(int fd, char *host, int port,
				      char *proxy_user, char *proxy_pass)
{
	char *cp, buffer[1024];
	char *authhdr, authbuf[1024];
	int len;

	if (proxy_user && proxy_pass) {
		stringjoin(buffer, sizeof buffer,
			 proxy_user, ":", proxy_pass, NULL);
		len = strlen(buffer);

		if ((len*8 + 5) / 6 >= (int)sizeof authbuf - 3) {
			rprintf(FERROR,
				"authentication information is too long\n");
			return -1;
		}

		base64_encode(buffer, len, authbuf, 1);
		authhdr = "\r\nProxy-Authorization: Basic ";
	} else {
		*authbuf = '\0';
		authhdr = "";
	}

	snprintf(buffer, sizeof buffer, "CONNECT %s:%d HTTP/1.0%s%s\r\n\r\n",
		 host, port, authhdr, authbuf);
	len = strlen(buffer);
	if (write(fd, buffer, len) != len) {
		rsyserr(FERROR, errno, "failed to write to proxy");
		return -1;
	}

	for (cp = buffer; cp < &buffer[sizeof buffer - 1]; cp++) {
		if (read(fd, cp, 1) != 1) {
			rsyserr(FERROR, errno, "failed to read from proxy");
			return -1;
		}
		if (*cp == '\n')
			break;
	}

	if (*cp != '\n')
		cp++;
	*cp-- = '\0';
	if (*cp == '\r')
		*cp = '\0';
	if (strncmp(buffer, "HTTP/", 5) != 0) {
		rprintf(FERROR, "bad response from proxy -- %s\n",
			buffer);
		return -1;
	}
	for (cp = &buffer[5]; isDigit(cp) || *cp == '.'; cp++) {}
	while (*cp == ' ')
		cp++;
	if (*cp != '2') {
		rprintf(FERROR, "bad response from proxy -- %s\n",
			buffer);
		return -1;
	}
	/* throw away the rest of the HTTP header */
	while (1) {
		for (cp = buffer; cp < &buffer[sizeof buffer - 1]; cp++) {
			if (read(fd, cp, 1) != 1) {
				rsyserr(FERROR, errno,
					"failed to read from proxy");
				return -1;
			}
			if (*cp == '\n')
				break;
		}
		if (cp > buffer && *cp == '\n')
			cp--;
		if (cp == buffer && (*cp == '\n' || *cp == '\r'))
			break;
	}
	return 0;
}


/* Try to set the local address for a newly-created socket.
 * Return -1 if this fails. */
int try_bind_local(int s, int ai_family, int ai_socktype,
		   const char *bind_addr)
{
	int error;
	struct addrinfo bhints, *bres_all, *r;

	memset(&bhints, 0, sizeof bhints);
	bhints.ai_family = ai_family;
	bhints.ai_socktype = ai_socktype;
	bhints.ai_flags = AI_PASSIVE;
	if ((error = getaddrinfo(bind_addr, NULL, &bhints, &bres_all))) {
		rprintf(FERROR, RSYNC_NAME ": getaddrinfo %s: %s\n",
			bind_addr, gai_strerror(error));
		return -1;
	}

	for (r = bres_all; r; r = r->ai_next) {
		if (bind(s, r->ai_addr, r->ai_addrlen) == -1)
			continue;
		freeaddrinfo(bres_all);
		return s;
	}

	/* no error message; there might be some problem that allows
	 * creation of the socket but not binding, perhaps if the
	 * machine has no ipv6 address of this name. */
	freeaddrinfo(bres_all);
	return -1;
}

/* connect() timeout handler based on alarm() */
static RETSIGTYPE contimeout_handler(UNUSED(int val))
{
	connect_timeout = -1;
}

/* Open a socket to a tcp remote host with the specified port.
 *
 * Based on code from Warren.  Proxy support by Stephen Rothwell.
 * getaddrinfo() rewrite contributed by KAME.net.
 *
 * Now that we support IPv6 we need to look up the remote machine's address
 * first, using af_hint to set a preference for the type of address.  Then
 * depending on whether it has v4 or v6 addresses we try to open a connection.
 *
 * The loop allows for machines with some addresses which may not be reachable,
 * perhaps because we can't e.g. route ipv6 to that network but we can get ip4
 * packets through.
 *
 * bind_addr: local address to use.  Normally NULL to bind the wildcard address.
 *
 * af_hint: address family, e.g. AF_INET or AF_INET6. */
int open_socket_out(char *host, int port, const char *bind_addr,
		    int af_hint)
{
	int type = SOCK_STREAM;
	int error, s, j, addr_cnt, *errnos;
	struct addrinfo hints, *res0, *res;
	char portbuf[10];
	char *h, *cp;
	int proxied = 0;
	char buffer[1024];
	char *proxy_user = NULL, *proxy_pass = NULL;

	/* if we have a RSYNC_PROXY env variable then redirect our
	 * connetcion via a web proxy at the given address. */
	h = getenv("RSYNC_PROXY");
	proxied = h != NULL && *h != '\0';

	if (proxied) {
		strlcpy(buffer, h, sizeof buffer);

		/* Is the USER:PASS@ prefix present? */
		if ((cp = strrchr(buffer, '@')) != NULL) {
			*cp++ = '\0';
			/* The remainder is the HOST:PORT part. */
			h = cp;

			if ((cp = strchr(buffer, ':')) == NULL) {
				rprintf(FERROR,
					"invalid proxy specification: should be USER:PASS@HOST:PORT\n");
				return -1;
			}
			*cp++ = '\0';

			proxy_user = buffer;
			proxy_pass = cp;
		} else {
			/* The whole buffer is the HOST:PORT part. */
			h = buffer;
		}

		if ((cp = strchr(h, ':')) == NULL) {
			rprintf(FERROR,
				"invalid proxy specification: should be HOST:PORT\n");
			return -1;
		}
		*cp++ = '\0';
		strlcpy(portbuf, cp, sizeof portbuf);
		if (verbose >= 2) {
			rprintf(FINFO, "connection via http proxy %s port %s\n",
				h, portbuf);
		}
	} else {
		snprintf(portbuf, sizeof portbuf, "%d", port);
		h = host;
	}

	memset(&hints, 0, sizeof hints);
	hints.ai_family = af_hint;
	hints.ai_socktype = type;
	error = getaddrinfo(h, portbuf, &hints, &res0);
	if (error) {
		rprintf(FERROR, RSYNC_NAME ": getaddrinfo: %s %s: %s\n",
			h, portbuf, gai_strerror(error));
		return -1;
	}

	for (res = res0, addr_cnt = 0; res; res = res->ai_next, addr_cnt++) {}
	errnos = new_array0(int, addr_cnt);
	if (!errnos)
		out_of_memory("open_socket_out");

	s = -1;
	/* Try to connect to all addresses for this machine until we get
	 * through.  It might e.g. be multi-homed, or have both IPv4 and IPv6
	 * addresses.  We need to create a socket for each record, since the
	 * address record tells us what protocol to use to try to connect. */
	for (res = res0, j = 0; res; res = res->ai_next, j++) {
		s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
		if (s < 0)
			continue;

		if (bind_addr
		 && try_bind_local(s, res->ai_family, type,
				   bind_addr) == -1) {
			close(s);
			s = -1;
			continue;
		}
		if (connect_timeout > 0) {
			SIGACTION(SIGALRM, contimeout_handler);
			alarm(connect_timeout);
		}

		set_socket_options(s, sockopts);
		while (connect(s, res->ai_addr, res->ai_addrlen) < 0) {
			if (connect_timeout < 0)
				exit_cleanup(RERR_CONTIMEOUT);
			if (errno == EINTR)
				continue;
			close(s);
			s = -1;
			break;
		}

		if (connect_timeout > 0)
			alarm(0);

		if (s < 0) {
			errnos[j] = errno;
			continue;
		}

		if (proxied
		 && establish_proxy_connection(s, host, port,
					       proxy_user, proxy_pass) != 0) {
			close(s);
			s = -1;
			continue;
		}
		break;
	}
	freeaddrinfo(res0);

	if (s < 0) {
		char buf[2048];
		for (res = res0, j = 0; res; res = res->ai_next, j++) {
			if (errnos[j] == 0)
				continue;
			if (inet_ntop(res->ai_family, res->ai_addr->sa_data + 2, buf, sizeof buf) == NULL)
				strlcpy(buf, "*inet_ntop failed*", sizeof buf);
			rsyserr(FERROR, errnos[j], "failed to connect to %s (%s)", h, buf);
		}
		s = -1;
	}

	free(errnos);

	return s;
}


/* Open an outgoing socket, but allow for it to be intercepted by
 * $RSYNC_CONNECT_PROG, which will execute a program across a TCP
 * socketpair rather than really opening a socket.
 *
 * We use this primarily in testing to detect TCP flow bugs, but not
 * cause security problems by really opening remote connections.
 *
 * This is based on the Samba LIBSMB_PROG feature.
 *
 * bind_addr: local address to use.  Normally NULL to get the stack default. */
int open_socket_out_wrapped(char *host, int port, const char *bind_addr,
			    int af_hint)
{
	char *prog = getenv("RSYNC_CONNECT_PROG");

	if (prog && strchr(prog, '%')) {
		int hlen = strlen(host);
		int len = strlen(prog) + 1;
		char *f, *t;
		for (f = prog; *f; f++) {
			if (*f != '%')
				continue;
			/* Compute more than enough room. */
			if (f[1] == '%')
				f++;
			else
				len += hlen;
		}
		f = prog;
		if (!(prog = new_array(char, len)))
			out_of_memory("open_socket_out_wrapped");
		for (t = prog; *f; f++) {
			if (*f == '%') {
				switch (*++f) {
				case '%':
					/* Just skips the extra '%'. */
					break;
				case 'H':
					memcpy(t, host, hlen);
					t += hlen;
					continue;
				default:
					f--; /* pass % through */
					break;
				}
			}
			*t++ = *f;
		}
		*t = '\0';
	}

	if (verbose >= 2) {
		rprintf(FINFO, "%sopening tcp connection to %s port %d\n",
			prog ? "Using RSYNC_CONNECT_PROG instead of " : "",
			host, port);
	}
	if (prog)
		return sock_exec(prog);
	return open_socket_out(host, port, bind_addr, af_hint);
}


/* Open one or more sockets for incoming data using the specified type,
 * port, and address.
 *
 * The getaddrinfo() call may return several address results, e.g. for
 * the machine's IPv4 and IPv6 name.
 *
 * We return an array of file-descriptors to the sockets, with a trailing
 * -1 value to indicate the end of the list.
 *
 * bind_addr: local address to bind, or NULL to allow it to default. */
static int *open_socket_in(int type, int port, const char *bind_addr,
			   int af_hint)
{
	int one = 1;
	int s, *socks, maxs, i, ecnt;
	struct addrinfo hints, *all_ai, *resp;
	char portbuf[10], **errmsgs;
	int error;

	memset(&hints, 0, sizeof hints);
	hints.ai_family = af_hint;
	hints.ai_socktype = type;
	hints.ai_flags = AI_PASSIVE;
	snprintf(portbuf, sizeof portbuf, "%d", port);
	error = getaddrinfo(bind_addr, portbuf, &hints, &all_ai);
	if (error) {
		rprintf(FERROR, RSYNC_NAME ": getaddrinfo: bind address %s: %s\n",
			bind_addr, gai_strerror(error));
		return NULL;
	}

	/* Count max number of sockets we might open. */
	for (maxs = 0, resp = all_ai; resp; resp = resp->ai_next, maxs++) {}

	socks = new_array(int, maxs + 1);
	errmsgs = new_array(char *, maxs);
	if (!socks || !errmsgs)
		out_of_memory("open_socket_in");

	/* We may not be able to create the socket, if for example the
	 * machine knows about IPv6 in the C library, but not in the
	 * kernel. */
	for (resp = all_ai, i = ecnt = 0; resp; resp = resp->ai_next) {
		s = socket(resp->ai_family, resp->ai_socktype,
			   resp->ai_protocol);

		if (s == -1) {
			int r = asprintf(&errmsgs[ecnt++],
				"socket(%d,%d,%d) failed: %s\n",
				(int)resp->ai_family, (int)resp->ai_socktype,
				(int)resp->ai_protocol, strerror(errno));
			if (r < 0)
				out_of_memory("open_socket_in");
			/* See if there's another address that will work... */
			continue;
		}

		setsockopt(s, SOL_SOCKET, SO_REUSEADDR,
			   (char *)&one, sizeof one);
		if (sockopts)
			set_socket_options(s, sockopts);
		else
			set_socket_options(s, lp_socket_options());

#ifdef IPV6_V6ONLY
		if (resp->ai_family == AF_INET6) {
			if (setsockopt(s, IPPROTO_IPV6, IPV6_V6ONLY,
				       (char *)&one, sizeof one) < 0
			    && default_af_hint != AF_INET6) {
				close(s);
				continue;
			}
		}
#endif

		/* Now we've got a socket - we need to bind it. */
		if (bind(s, resp->ai_addr, resp->ai_addrlen) < 0) {
			/* Nope, try another */
			int r = asprintf(&errmsgs[ecnt++],
				"bind() failed: %s (address-family %d)\n",
				strerror(errno), (int)resp->ai_family);
			if (r < 0)
				out_of_memory("open_socket_in");
			close(s);
			continue;
		}

		socks[i++] = s;
	}
	socks[i] = -1;

	if (all_ai)
		freeaddrinfo(all_ai);

	/* Only output the socket()/bind() messages if we were totally
	 * unsuccessful, or if the daemon is being run with -vv. */
	for (s = 0; s < ecnt; s++) {
		if (!i || verbose > 1)
			rwrite(FLOG, errmsgs[s], strlen(errmsgs[s]), 0);
		free(errmsgs[s]);
	}
	free(errmsgs);

	if (!i) {
		rprintf(FERROR,
			"unable to bind any inbound sockets on port %d\n",
			port);
		free(socks);
		return NULL;
	}
	return socks;
}


/* Determine if a file descriptor is in fact a socket. */
int is_a_socket(int fd)
{
	int v;
	socklen_t l = sizeof (int);

	/* Parameters to getsockopt, setsockopt etc are very
	 * unstandardized across platforms, so don't be surprised if
	 * there are compiler warnings on e.g. SCO OpenSwerver or AIX.
	 * It seems they all eventually get the right idea.
	 *
	 * Debian says: ``The fifth argument of getsockopt and
	 * setsockopt is in reality an int [*] (and this is what BSD
	 * 4.* and libc4 and libc5 have).  Some POSIX confusion
	 * resulted in the present socklen_t.  The draft standard has
	 * not been adopted yet, but glibc2 already follows it and
	 * also has socklen_t [*]. See also accept(2).''
	 *
	 * We now return to your regularly scheduled programming.  */
	return getsockopt(fd, SOL_SOCKET, SO_TYPE, (char *)&v, &l) == 0;
}


static RETSIGTYPE sigchld_handler(UNUSED(int val))
{
#ifdef WNOHANG
	while (waitpid(-1, NULL, WNOHANG) > 0) {}
#endif
#ifndef HAVE_SIGACTION
	signal(SIGCHLD, sigchld_handler);
#endif
}


void start_accept_loop(int port, int (*fn)(int, int))
{
	fd_set deffds;
	int *sp, maxfd, i;

#ifdef HAVE_SIGACTION
	sigact.sa_flags = SA_NOCLDSTOP;
#endif

	/* open an incoming socket */
	sp = open_socket_in(SOCK_STREAM, port, bind_address, default_af_hint);
	if (sp == NULL)
		exit_cleanup(RERR_SOCKETIO);

	/* ready to listen */
	FD_ZERO(&deffds);
	for (i = 0, maxfd = -1; sp[i] >= 0; i++) {
		if (listen(sp[i], 5) < 0) {
			rsyserr(FERROR, errno, "listen() on socket failed");
#ifdef INET6
			if (errno == EADDRINUSE && i > 0) {
				rprintf(FINFO,
				    "Try using --ipv4 or --ipv6 to avoid this listen() error.\n");
			}
#endif
			exit_cleanup(RERR_SOCKETIO);
		}
		FD_SET(sp[i], &deffds);
		if (maxfd < sp[i])
			maxfd = sp[i];
	}

	/* now accept incoming connections - forking a new process
	 * for each incoming connection */
	while (1) {
		fd_set fds;
		pid_t pid;
		int fd;
		struct sockaddr_storage addr;
		socklen_t addrlen = sizeof addr;

		/* close log file before the potentially very long select so
		 * file can be trimmed by another process instead of growing
		 * forever */
		logfile_close();

#ifdef FD_COPY
		FD_COPY(&deffds, &fds);
#else
		fds = deffds;
#endif

		if (select(maxfd + 1, &fds, NULL, NULL, NULL) < 1)
			continue;

		for (i = 0, fd = -1; sp[i] >= 0; i++) {
			if (FD_ISSET(sp[i], &fds)) {
				fd = accept(sp[i], (struct sockaddr *)&addr,
					    &addrlen);
				break;
			}
		}

		if (fd < 0)
			continue;

		SIGACTION(SIGCHLD, sigchld_handler);

		if ((pid = fork()) == 0) {
			int ret;
			for (i = 0; sp[i] >= 0; i++)
				close(sp[i]);
			/* Re-open log file in child before possibly giving
			 * up privileges (see logfile_close() above). */
			logfile_reopen();
			ret = fn(fd, fd);
			close_all();
			_exit(ret);
		} else if (pid < 0) {
			rsyserr(FERROR, errno,
				"could not create child server process");
			close(fd);
			/* This might have happened because we're
			 * overloaded.  Sleep briefly before trying to
			 * accept again. */
			sleep(2);
		} else {
			/* Parent doesn't need this fd anymore. */
			close(fd);
		}
	}
}


enum SOCK_OPT_TYPES {OPT_BOOL,OPT_INT,OPT_ON};

struct
{
  char *name;
  int level;
  int option;
  int value;
  int opttype;
} socket_options[] = {
  {"SO_KEEPALIVE",      SOL_SOCKET,    SO_KEEPALIVE,    0,                 OPT_BOOL},
  {"SO_REUSEADDR",      SOL_SOCKET,    SO_REUSEADDR,    0,                 OPT_BOOL},
  {"SO_BROADCAST",      SOL_SOCKET,    SO_BROADCAST,    0,                 OPT_BOOL},
#ifdef TCP_NODELAY
  {"TCP_NODELAY",       IPPROTO_TCP,   TCP_NODELAY,     0,                 OPT_BOOL},
#endif
#ifdef IPTOS_LOWDELAY
  {"IPTOS_LOWDELAY",    IPPROTO_IP,    IP_TOS,          IPTOS_LOWDELAY,    OPT_ON},
#endif
#ifdef IPTOS_THROUGHPUT
  {"IPTOS_THROUGHPUT",  IPPROTO_IP,    IP_TOS,          IPTOS_THROUGHPUT,  OPT_ON},
#endif
#ifdef SO_SNDBUF
  {"SO_SNDBUF",         SOL_SOCKET,    SO_SNDBUF,       0,                 OPT_INT},
#endif
#ifdef SO_RCVBUF
  {"SO_RCVBUF",         SOL_SOCKET,    SO_RCVBUF,       0,                 OPT_INT},
#endif
#ifdef SO_SNDLOWAT
  {"SO_SNDLOWAT",       SOL_SOCKET,    SO_SNDLOWAT,     0,                 OPT_INT},
#endif
#ifdef SO_RCVLOWAT
  {"SO_RCVLOWAT",       SOL_SOCKET,    SO_RCVLOWAT,     0,                 OPT_INT},
#endif
#ifdef SO_SNDTIMEO
  {"SO_SNDTIMEO",       SOL_SOCKET,    SO_SNDTIMEO,     0,                 OPT_INT},
#endif
#ifdef SO_RCVTIMEO
  {"SO_RCVTIMEO",       SOL_SOCKET,    SO_RCVTIMEO,     0,                 OPT_INT},
#endif
  {NULL,0,0,0,0}
};


/* Set user socket options. */
void set_socket_options(int fd, char *options)
{
	char *tok;

	if (!options || !*options)
		return;

	options = strdup(options);

	if (!options)
		out_of_memory("set_socket_options");

	for (tok = strtok(options, " \t,"); tok; tok = strtok(NULL," \t,")) {
		int ret=0,i;
		int value = 1;
		char *p;
		int got_value = 0;

		if ((p = strchr(tok,'='))) {
			*p = 0;
			value = atoi(p+1);
			got_value = 1;
		}

		for (i = 0; socket_options[i].name; i++) {
			if (strcmp(socket_options[i].name,tok)==0)
				break;
		}

		if (!socket_options[i].name) {
			rprintf(FERROR,"Unknown socket option %s\n",tok);
			continue;
		}

		switch (socket_options[i].opttype) {
		case OPT_BOOL:
		case OPT_INT:
			ret = setsockopt(fd,socket_options[i].level,
					 socket_options[i].option,
					 (char *)&value, sizeof (int));
			break;

		case OPT_ON:
			if (got_value)
				rprintf(FERROR,"syntax error -- %s does not take a value\n",tok);

			{
				int on = socket_options[i].value;
				ret = setsockopt(fd,socket_options[i].level,
						 socket_options[i].option,
						 (char *)&on, sizeof (int));
			}
			break;
		}

		if (ret != 0) {
			rsyserr(FERROR, errno,
				"failed to set socket option %s", tok);
		}
	}

	free(options);
}


/* This is like socketpair but uses tcp.  The function guarantees that nobody
 * else can attach to the socket, or if they do that this function fails and
 * the socket gets closed.  Returns 0 on success, -1 on failure.  The resulting
 * file descriptors are symmetrical.  Currently only for RSYNC_CONNECT_PROG. */
static int socketpair_tcp(int fd[2])
{
	int listener;
	struct sockaddr_in sock;
	struct sockaddr_in sock2;
	socklen_t socklen = sizeof sock;
	int connect_done = 0;

	fd[0] = fd[1] = listener = -1;

	memset(&sock, 0, sizeof sock);

	if ((listener = socket(PF_INET, SOCK_STREAM, 0)) == -1)
		goto failed;

	memset(&sock2, 0, sizeof sock2);
#ifdef HAVE_SOCKADDR_IN_LEN
	sock2.sin_len = sizeof sock2;
#endif
	sock2.sin_family = PF_INET;
	sock2.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

	if (bind(listener, (struct sockaddr *)&sock2, sizeof sock2) != 0
	 || listen(listener, 1) != 0
	 || getsockname(listener, (struct sockaddr *)&sock, &socklen) != 0
	 || (fd[1] = socket(PF_INET, SOCK_STREAM, 0)) == -1)
		goto failed;

	set_nonblocking(fd[1]);

	sock.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

	if (connect(fd[1], (struct sockaddr *)&sock, sizeof sock) == -1) {
		if (errno != EINPROGRESS)
			goto failed;
	} else
		connect_done = 1;

	if ((fd[0] = accept(listener, (struct sockaddr *)&sock2, &socklen)) == -1)
		goto failed;

	close(listener);
	listener = -1;

	set_blocking(fd[1]);

	if (connect_done == 0) {
		if (connect(fd[1], (struct sockaddr *)&sock, sizeof sock) != 0
		    && errno != EISCONN)
			goto failed;
	}

	/* all OK! */
	return 0;

 failed:
	if (fd[0] != -1)
		close(fd[0]);
	if (fd[1] != -1)
		close(fd[1]);
	if (listener != -1)
		close(listener);
	return -1;
}


/* Run a program on a local tcp socket, so that we can talk to it's stdin and
 * stdout.  This is used to fake a connection to a daemon for testing -- not
 * for the normal case of running SSH.
 *
 * Retruns a socket which is attached to a subprocess running "prog". stdin and
 * stdout are attached. stderr is left attached to the original stderr. */
static int sock_exec(const char *prog)
{
	pid_t pid;
	int fd[2];

	if (socketpair_tcp(fd) != 0) {
		rsyserr(FERROR, errno, "socketpair_tcp failed");
		return -1;
	}
	if (verbose >= 2)
		rprintf(FINFO, "Running socket program: \"%s\"\n", prog);

	pid = fork();
	if (pid < 0) {
		rsyserr(FERROR, errno, "fork");
		exit_cleanup(RERR_IPC);
	}

	if (pid == 0) {
		close(fd[0]);
		if (dup2(fd[1], STDIN_FILENO) < 0
		 || dup2(fd[1], STDOUT_FILENO) < 0) {
			fprintf(stderr, "Failed to run \"%s\"\n", prog);
			exit(1);
		}
		exit(system(prog));
	}

	close(fd[1]);
	return fd[0];
}
