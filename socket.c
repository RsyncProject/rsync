/* -*- c-file-style: "linux" -*-
   
   Copyright (C) 1992-2001 by Andrew Tridgell <tridge@samba.org>
   Copyright (C) 2001 by Martin Pool <mbp@samba.org>
   
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

/*
  socket functions used in rsync 

  */

#include "rsync.h"

#ifndef HAVE_GETADDRINFO
#include "lib/addrinfo.h"
#endif

extern int af;

/* Establish a proxy connection on an open socket to a web roxy by
 * using the CONNECT method. */
static int establish_proxy_connection(int fd, char *host, int port)
{
	char buffer[1024];
	char *cp;

	snprintf(buffer, sizeof(buffer), "CONNECT %s:%d HTTP/1.0\r\n\r\n", host, port);
	if (write(fd, buffer, strlen(buffer)) != strlen(buffer)) {
		rprintf(FERROR, "failed to write to proxy: %s\n",
			strerror(errno));
		return -1;
	}

	for (cp = buffer; cp < &buffer[sizeof(buffer) - 1]; cp++) {
		if (read(fd, cp, 1) != 1) {
			rprintf(FERROR, "failed to read from proxy: %s\n",
				strerror(errno));
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
		rprintf(FERROR, "bad response from proxy - %s\n",
			buffer);
		return -1;
	}
	for (cp = &buffer[5]; isdigit(*cp) || (*cp == '.'); cp++)
		;
	while (*cp == ' ')
		cp++;
	if (*cp != '2') {
		rprintf(FERROR, "bad response from proxy - %s\n",
			buffer);
		return -1;
	}
	/* throw away the rest of the HTTP header */
	while (1) {
		for (cp = buffer; cp < &buffer[sizeof(buffer) - 1];
		     cp++) {
			if (read(fd, cp, 1) != 1) {
				rprintf(FERROR, "failed to read from proxy: %s\n",
					strerror(errno));
				return -1;
			}
			if (*cp == '\n')
				break;
		}
		if ((cp > buffer) && (*cp == '\n'))
			cp--;
		if ((cp == buffer) && ((*cp == '\n') || (*cp == '\r')))
			break;
	}
	return 0;
}



/** Open a socket to a tcp remote host with the specified port .
 *
 * Based on code from Warren.   Proxy support by Stephen Rothwell
 *
 *
 * @param bind_address Local address to use.  Normally NULL to get the stack default.
 **/
int open_socket_out(char *host, int port, const char *bind_address)
{
	int type = SOCK_STREAM;
	int error;
	int s;
	int result;
	struct addrinfo hints, *res0, *res;
	char portbuf[10];
	char *h;
	int proxied = 0;
	char buffer[1024];
	char *cp;

	/* if we have a RSYNC_PROXY env variable then redirect our
	 * connetcion via a web proxy at the given address. The format
	 * is hostname:port */
	h = getenv("RSYNC_PROXY");
	proxied = (h != NULL) && (*h != '\0');

	if (proxied) {
		strlcpy(buffer, h, sizeof(buffer));
		cp = strchr(buffer, ':');
		if (cp == NULL) {
			rprintf(FERROR,
				"invalid proxy specification: should be HOST:PORT\n");
			return -1;
		}
		*cp++ = '\0';
		strcpy(portbuf, cp);
		h = buffer;
	} else {
		snprintf(portbuf, sizeof(portbuf), "%d", port);
		h = host;
	}

	memset(&hints, 0, sizeof(hints));
	hints.ai_family = af;
	hints.ai_socktype = type;
	error = getaddrinfo(h, portbuf, &hints, &res0);
	if (error) {
		rprintf(FERROR, RSYNC_NAME ": getaddrinfo: %s: %s\n", portbuf, gai_strerror(error));
		return -1;
	}

	s = -1;
	for (res = res0; res; res = res->ai_next) {
		s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
		if (s < 0)
			continue;

		if (bind_address) {
			struct addrinfo bhints, *bres;

			memset(&bhints, 0, sizeof(bhints));
			bhints.ai_family = res->ai_family;
			bhints.ai_socktype = type;
			bhints.ai_flags = AI_PASSIVE;
			error = getaddrinfo(bind_address, NULL, &bhints, &bres);
			if (error) {
				rprintf(FERROR, RSYNC_NAME ": getaddrinfo: bind address %s: %s\n",
					bind_address, gai_strerror(error));
				continue;
			}
			if (bres->ai_next) {
				rprintf(FERROR, RSYNC_NAME ": getaddrinfo: bind address %s resolved to multiple hosts\n",
					bind_address);
				freeaddrinfo(bres);
				continue;
			}
			bind(s, bres->ai_addr, bres->ai_addrlen);
		}

		if (connect(s, res->ai_addr, res->ai_addrlen) < 0) {
			close(s);
			s = -1;
			continue;
		}
		if (proxied &&
		    establish_proxy_connection(s, host, port) != 0) {
			close(s);
			s = -1;
			continue;
		} else
			break;
	}
	freeaddrinfo(res0);
	if (s < 0) {
		rprintf(FERROR, RSYNC_NAME ": failed to connect to %s: %s\n",
			h, strerror(errno));
		return -1;
	}
	return s;
}


/**
 * Open an outgoing socket, but allow for it to be intercepted by
 * $RSYNC_CONNECT_PROG, which will execute a program across a TCP
 * socketpair rather than really opening a socket.
 *
 * We use this primarily in testing to detect TCP flow bugs, but not
 * cause security problems by really opening remote connections.
 *
 * This is based on the Samba LIBSMB_PROG feature.
 *
 * @param bind_address Local address to use.  Normally NULL to get the stack default.
 **/
int open_socket_out_wrapped (char *host,
			     int port,
			     const char *bind_address)
{
	char *prog;

	if ((prog = getenv ("RSYNC_CONNECT_PROG")) != NULL) 
		return sock_exec (prog);
	else 
		return open_socket_out (host, port, bind_address);
}



/**
 * Open a socket of the specified type, port and address for incoming data
 *
 * @param bind_address Local address to bind, or NULL to allow it to
 * default.
 **/
static int open_socket_in(int type, int port, const char *bind_address)
{
	int one=1;
	int s;
	struct addrinfo hints, *res;
	char portbuf[10];
	int error;

	memset(&hints, 0, sizeof(hints));
	hints.ai_family = af;
	hints.ai_socktype = type;
	hints.ai_flags = AI_PASSIVE;
	snprintf(portbuf, sizeof(portbuf), "%d", port);
	error = getaddrinfo(bind_address, portbuf, &hints, &res);
	if (error) {
		rprintf(FERROR, RSYNC_NAME ": getaddrinfo: bind address %s: %s\n",
			bind_address, gai_strerror(error));
		return -1;
	}
	if (res->ai_next) {
		rprintf(FERROR, RSYNC_NAME ": getaddrinfo: bind address %s: "
			"resolved to multiple hosts\n",
			bind_address);
		freeaddrinfo(res);
		return -1;
	}

	s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
	if (s < 0) {
		rprintf(FERROR, RSYNC_NAME ": open socket in failed: %s\n",
			strerror(errno)); 
		freeaddrinfo(res);
		return -1; 
	}

	setsockopt(s,SOL_SOCKET,SO_REUSEADDR,(char *)&one,sizeof(one));

	/* now we've got a socket - we need to bind it */
	if (bind(s, res->ai_addr, res->ai_addrlen) < 0) { 
		rprintf(FERROR, RSYNC_NAME ": bind failed on port %d\n", port);
		freeaddrinfo(res);
		close(s); 
		return -1;
	}

	return s;
}


/*
 * Determine if a file descriptor is in fact a socket
 */
int is_a_socket(int fd)
{
	int v;
	socklen_t l;
	l = sizeof(int);

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
	return(getsockopt(fd, SOL_SOCKET, SO_TYPE, (char *)&v, &l) == 0);
}


void start_accept_loop(int port, int (*fn)(int ))
{
	int s;
	extern char *bind_address;

	/* open an incoming socket */
	s = open_socket_in(SOCK_STREAM, port, bind_address);
	if (s == -1)
		exit_cleanup(RERR_SOCKETIO);

	/* ready to listen */
	if (listen(s, 5) == -1) {
		close(s);
		exit_cleanup(RERR_SOCKETIO);
	}


	/* now accept incoming connections - forking a new process
	   for each incoming connection */
	while (1) {
		fd_set fds;
		int fd;
		struct sockaddr_storage addr;
		int in_addrlen = sizeof(addr);

		/* close log file before the potentially very long select so
		   file can be trimmed by another process instead of growing
		   forever */
		log_close();

		FD_ZERO(&fds);
		FD_SET(s, &fds);

		if (select(s+1, &fds, NULL, NULL, NULL) != 1) {
			continue;
		}

		if(!FD_ISSET(s, &fds)) continue;

		fd = accept(s,(struct sockaddr *)&addr,&in_addrlen);

		if (fd == -1) continue;

		signal(SIGCHLD, SIG_IGN);

		/* we shouldn't have any children left hanging around
		   but I have had reports that on Digital Unix zombies
		   are produced, so this ensures that they are reaped */
#ifdef WNOHANG
                while (waitpid(-1, NULL, WNOHANG) > 0);
#endif

		if (fork()==0) {
			close(s);
			/* open log file in child before possibly giving
			   up privileges  */
			log_open();
			_exit(fn(fd));
		}

		close(fd);
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
  {NULL,0,0,0,0}};

	

/****************************************************************************
set user socket options
****************************************************************************/
void set_socket_options(int fd, char *options)
{
	char *tok;
	if (!options || !*options) return;

	options = strdup(options);
	
	if (!options) out_of_memory("set_socket_options");

	for (tok=strtok(options, " \t,"); tok; tok=strtok(NULL," \t,")) {
		int ret=0,i;
		int value = 1;
		char *p;
		int got_value = 0;

		if ((p = strchr(tok,'='))) {
			*p = 0;
			value = atoi(p+1);
			got_value = 1;
		}

		for (i=0;socket_options[i].name;i++)
			if (strcmp(socket_options[i].name,tok)==0)
				break;

		if (!socket_options[i].name) {
			rprintf(FERROR,"Unknown socket option %s\n",tok);
			continue;
		}

		switch (socket_options[i].opttype) {
		case OPT_BOOL:
		case OPT_INT:
			ret = setsockopt(fd,socket_options[i].level,
					 socket_options[i].option,(char *)&value,sizeof(int));
			break;
			
		case OPT_ON:
			if (got_value)
				rprintf(FERROR,"syntax error - %s does not take a value\n",tok);

			{
				int on = socket_options[i].value;
				ret = setsockopt(fd,socket_options[i].level,
						 socket_options[i].option,(char *)&on,sizeof(int));
			}
			break;	  
		}
		
		if (ret != 0)
			rprintf(FERROR, "failed to set socket option %s: %s\n", tok,
				strerror(errno));
	}

	free(options);
}

/****************************************************************************
become a daemon, discarding the controlling terminal
****************************************************************************/
void become_daemon(void)
{
	int i;

	if (fork()) {
		_exit(0);
	}

	/* detach from the terminal */
#ifdef HAVE_SETSID
	setsid();
#else
#ifdef TIOCNOTTY
	i = open("/dev/tty", O_RDWR);
	if (i >= 0) {
		ioctl(i, (int) TIOCNOTTY, (char *)0);      
		close(i);
	}
#endif /* TIOCNOTTY */
#endif
	/* make sure that stdin, stdout an stderr don't stuff things
           up (library functions, for example) */
	for (i=0;i<3;i++) {
		close(i); 
		open("/dev/null", O_RDWR);
	}
}

/*******************************************************************
 return the IP addr of the client as a string 
 ******************************************************************/
char *client_addr(int fd)
{
	struct sockaddr_storage ss;
	int     length = sizeof(ss);
	static char addr_buf[100];
	static int initialised;

	if (initialised) return addr_buf;

	initialised = 1;

	if (getpeername(fd, (struct sockaddr *)&ss, &length)) {
		exit_cleanup(RERR_SOCKETIO);
	}

	getnameinfo((struct sockaddr *)&ss, length,
		addr_buf, sizeof(addr_buf), NULL, 0, NI_NUMERICHOST);
	return addr_buf;
}


/*******************************************************************
 return the DNS name of the client 
 ******************************************************************/
char *client_name(int fd)
{
	struct sockaddr_storage ss;
	int     length = sizeof(ss);
	static char name_buf[100];
	static char port_buf[100];
	char *def = "UNKNOWN";
	static int initialised;
	struct addrinfo hints, *res, *res0;
	int error;

	if (initialised) return name_buf;

	initialised = 1;

	strcpy(name_buf,def);

	if (getpeername(fd, (struct sockaddr *)&ss, &length)) {
		exit_cleanup(RERR_SOCKETIO);
	}

#ifdef INET6
        if (ss.ss_family == AF_INET6 && 
	    IN6_IS_ADDR_V4MAPPED(&((struct sockaddr_in6 *)&ss)->sin6_addr)) {
		struct sockaddr_in6 sin6;
		struct sockaddr_in *sin;

		memcpy(&sin6, &ss, sizeof(sin6));
		sin = (struct sockaddr_in *)&ss;
		memset(sin, 0, sizeof(*sin));
		sin->sin_family = AF_INET;
		length = sizeof(struct sockaddr_in);
#ifdef HAVE_SOCKADDR_LEN
		sin->sin_len = length;
#endif
		sin->sin_port = sin6.sin6_port;
		memcpy(&sin->sin_addr, &sin6.sin6_addr.s6_addr[12],
			sizeof(sin->sin_addr));
        }
#endif

	/* reverse lookup */
	if (getnameinfo((struct sockaddr *)&ss, length,
			name_buf, sizeof(name_buf), port_buf, sizeof(port_buf),
			NI_NAMEREQD | NI_NUMERICSERV) != 0) {
		strcpy(name_buf, def);
		rprintf(FERROR, "reverse name lookup failed\n");
	}

	/* forward lookup */
	memset(&hints, 0, sizeof(hints));
	hints.ai_family = PF_UNSPEC;
	hints.ai_flags = AI_CANONNAME;
	hints.ai_socktype = SOCK_STREAM;
	error = getaddrinfo(name_buf, port_buf, &hints, &res0);
	if (error) {
		strcpy(name_buf, def);
		rprintf(FERROR,
			RSYNC_NAME ": forward name lookup for %s failed: %s\n",
			port_buf,
			gai_strerror(error));
		return name_buf;
	}

	/* XXX sin6_flowinfo and other fields */
	for (res = res0; res; res = res->ai_next) {
		if (res->ai_family != ss.ss_family)
			continue;
		if (res->ai_addrlen != length)
			continue;
		if (memcmp(res->ai_addr, &ss, res->ai_addrlen) == 0)
			break;
	}

	/* TODO: Do a forward lookup as well to prevent spoofing */

	if (res == NULL) {
		strcpy(name_buf, def);
		rprintf(FERROR,
			"reverse name lookup mismatch - spoofed address?\n");
	}

	freeaddrinfo(res0);
	return name_buf;
}

/**
   Convert a string to an IP address. The string can be a name or
   dotted decimal number.

   Returns a pointer to a static in_addr struct -- if you call this
   more than once then you should copy it.
*/
struct in_addr *ip_address(const char *str)
{
	static struct in_addr ret;
	struct hostent *hp;

	if (!str) {
		rprintf (FERROR, "ip_address received NULL name\n");
		return NULL;
	}

	/* try as an IP address */
	if (inet_aton(str, &ret) != 0) {
		return &ret;
	}

	/* otherwise assume it's a network name of some sort and use 
	   gethostbyname */
	if ((hp = gethostbyname (str)) == 0) {
		rprintf(FERROR, "gethostbyname failed for \"%s\": unknown host?\n",str);
		return NULL;
	}

	if (hp->h_addr == NULL) {
		rprintf(FERROR, "gethostbyname: host address is invalid for host \"%s\"\n",str);
		return NULL;
	}

	if (hp->h_length > sizeof ret) {
		rprintf(FERROR, "gethostbyname: host address for \"%s\" is too large\n",
			str);
		return NULL;
	}

	if (hp->h_addrtype != AF_INET) {
		rprintf (FERROR, "gethostname: host address for \"%s\" is not IPv4\n",
			 str);
		return NULL;
	}

	/* This is kind of difficult.  The only field in ret is
	   s_addr, which is the IP address as a 32-bit int.  On
	   UNICOS, s_addr is in fact a *bitfield* for reasons best
	   know to Cray.  This means we can't memcpy in to it.  On the
	   other hand, h_addr is a char*, so we can't just assign.

	   Since there's meant to be only one field inside the in_addr
	   structure we will try just copying over the top and see how
	   that goes. */
	memcpy (&ret, hp->h_addr, hp->h_length);

	return &ret;
}



/*******************************************************************
this is like socketpair but uses tcp. It is used by the Samba
regression test code
The function guarantees that nobody else can attach to the socket,
or if they do that this function fails and the socket gets closed
returns 0 on success, -1 on failure
the resulting file descriptors are symmetrical
 ******************************************************************/
static int socketpair_tcp(int fd[2])
{
	int listener;
	struct sockaddr_in sock;
	struct sockaddr_in sock2;
	socklen_t socklen = sizeof(sock);
	int connect_done = 0;
	
	fd[0] = fd[1] = listener = -1;

	memset(&sock, 0, sizeof(sock));
	
	if ((listener = socket(PF_INET, SOCK_STREAM, 0)) == -1) goto failed;

        memset(&sock2, 0, sizeof(sock2));
#ifdef HAVE_SOCK_SIN_LEN
        sock2.sin_len = sizeof(sock2);
#endif
        sock2.sin_family = PF_INET;

        bind(listener, (struct sockaddr *)&sock2, sizeof(sock2));

	if (listen(listener, 1) != 0) goto failed;

	if (getsockname(listener, (struct sockaddr *)&sock, &socklen) != 0) goto failed;

	if ((fd[1] = socket(PF_INET, SOCK_STREAM, 0)) == -1) goto failed;

	set_nonblocking(fd[1]);

	sock.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

	if (connect(fd[1],(struct sockaddr *)&sock,sizeof(sock)) == -1) {
		if (errno != EINPROGRESS) goto failed;
	} else {
		connect_done = 1;
	}

	if ((fd[0] = accept(listener, (struct sockaddr *)&sock, &socklen)) == -1) goto failed;

	close(listener);
	if (connect_done == 0) {
		if (connect(fd[1],(struct sockaddr *)&sock,sizeof(sock)) != 0
		    && errno != EISCONN) goto failed;
	}

	set_blocking (fd[1]);

	/* all OK! */
	return 0;

 failed:
	if (fd[0] != -1) close(fd[0]);
	if (fd[1] != -1) close(fd[1]);
	if (listener != -1) close(listener);
	return -1;
}


/*******************************************************************
run a program on a local tcp socket, this is used to launch smbd
when regression testing
the return value is a socket which is attached to a subprocess
running "prog". stdin and stdout are attached. stderr is left
attached to the original stderr
 ******************************************************************/
int sock_exec(const char *prog)
{
	int fd[2];
	if (socketpair_tcp(fd) != 0) {
		rprintf (FERROR, RSYNC_NAME
			 ": socketpair_tcp failed (%s)\n",
			 strerror(errno));
		return -1;
	}
	if (fork() == 0) {
		close(fd[0]);
		close(0);
		close(1);
		dup(fd[1]);
		dup(fd[1]);
		if (verbose > 3)
			fprintf (stderr,
				 RSYNC_NAME ": execute socket program \"%s\"\n",
				 prog);
		exit (system (prog));
	}
	close (fd[1]);
	return fd[0];
}



