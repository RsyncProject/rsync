/* -*- c-file-style: "linux" -*-
   
   Copyright (C) 1998-2001 by Andrew Tridgell <tridge@samba.org>
   
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


/* open a socket to a tcp remote host with the specified port 
   based on code from Warren
   proxy support by Stephen Rothwell */
int open_socket_out(char *host, int port, struct in_addr *address)
{
	int type = SOCK_STREAM;
	struct sockaddr_in sock_out;
	struct sockaddr_in sock;
	int res;
	struct hostent *hp;
	char *h;
	unsigned p;
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
		p = atoi(cp);
		h = buffer;
	} else {
		h = host;
		p = port;
	}

	res = socket(PF_INET, type, 0);
	if (res == -1) {
		return -1;
	}

	hp = gethostbyname(h);
	if (!hp) {
		rprintf(FERROR,"unknown host: %s\n", h);
		close(res);
		return -1;
	}

	memcpy(&sock_out.sin_addr, hp->h_addr, hp->h_length);
	sock_out.sin_port = htons(p);
	sock_out.sin_family = PF_INET;

	if (address) {
		sock.sin_addr = *address;
		sock.sin_port = 0;
		sock.sin_family = hp->h_addrtype;
		bind(res, (struct sockaddr * ) &sock,sizeof(sock));
	}

	if (connect(res,(struct sockaddr *)&sock_out,sizeof(sock_out))) {
		rprintf(FERROR,"failed to connect to %s - %s\n", h, strerror(errno));
		close(res);
		return -1;
	}

	if (proxied && establish_proxy_connection(res, host, port) != 0) {
		close(res);
		return -1;
	}

	return res;
}


/****************************************************************************
open a socket of the specified type, port and address for incoming data
****************************************************************************/
static int open_socket_in(int type, int port, struct in_addr *address)
{
	struct sockaddr_in sock;
	int res;
	int one=1;

	memset((char *)&sock,0,sizeof(sock));
	sock.sin_port = htons(port);
	sock.sin_family = AF_INET;
	if (address) {
		sock.sin_addr = *address;
	} else {
		sock.sin_addr.s_addr = INADDR_ANY;
	}
	res = socket(AF_INET, type, 0);
	if (res == -1) { 
		rprintf(FERROR,"socket failed: %s\n",
			strerror(errno)); 
		return -1; 
	}

	setsockopt(res,SOL_SOCKET,SO_REUSEADDR,(char *)&one,sizeof(one));

	/* now we've got a socket - we need to bind it */
	if (bind(res, (struct sockaddr * ) &sock,sizeof(sock)) == -1) { 
		rprintf(FERROR,"bind failed on port %d: %s\n", port,
			strerror(errno));
		if (errno == EACCES && port < 1024) {
			rprintf(FERROR, "Note: you must be root to bind "
				"to low-numbered ports");
		}
		close(res); 
		return -1;
	}

	return res;
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
	extern struct in_addr socket_address;

	/* open an incoming socket */
	s = open_socket_in(SOCK_STREAM, port, &socket_address);
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
		struct sockaddr addr;
		socklen_t in_addrlen = sizeof(addr);

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

                /* See note above prototypes. */
		fd = accept(s,&addr, &in_addrlen);

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
	struct sockaddr sa;
	struct sockaddr_in *sockin = (struct sockaddr_in *) (&sa);
	int     length = sizeof(sa);
	static char addr_buf[100];
	static int initialised;

	if (initialised) return addr_buf;

	initialised = 1;

	if (getpeername(fd, &sa, &length)) {
		exit_cleanup(RERR_SOCKETIO);
	}
	
	strlcpy(addr_buf,(char *)inet_ntoa(sockin->sin_addr), sizeof(addr_buf));
	return addr_buf;
}


/*******************************************************************
 return the DNS name of the client 
 ******************************************************************/
char *client_name(int fd)
{
	struct sockaddr sa;
	struct sockaddr_in *sockin = (struct sockaddr_in *) (&sa);
	socklen_t length = sizeof(sa);
	static char name_buf[100];
	struct hostent *hp;
	char **p;
	char *def = "UNKNOWN";
	static int initialised;

	if (initialised) return name_buf;

	initialised = 1;

	strcpy(name_buf,def);

	if (getpeername(fd, &sa, &length)) {
		exit_cleanup(RERR_SOCKETIO);
	}

	/* Look up the remote host name. */
	if ((hp = gethostbyaddr((char *) &sockin->sin_addr,
				sizeof(sockin->sin_addr),
				AF_INET))) {
		strlcpy(name_buf,(char *)hp->h_name,sizeof(name_buf));
	}


	/* do a forward lookup as well to prevent spoofing */
	hp = gethostbyname(name_buf);
	if (!hp) {
		strcpy (name_buf,def);
		rprint (FERROR, "reverse name lookup for \"%s\" failed\n",
			name_buf);
	} else {
		for (p=hp->h_addr_list;*p;p++) {
			if (memcmp(*p, &sockin->sin_addr, hp->h_length) == 0) {
				break;
			}
		}
		if (!*p) {
			strcpy(name_buf,def);
			rprintf(FERROR,"reverse name lookup mismatch - spoofed address?\n");
		} 
	}

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

	assert (str);

	/* try as an IP address */
	if (inet_aton(str, &ret) != 0) {
		return &ret;
	}

	/* otherwise assume it's a network name of some sort and use 
	   gethostbyname */
	if ((hp = gethostbyname(str)) == 0) {
		rprintf(FERROR, "gethostbyname failed for \"%s\": unknown host?\n",str);
		return NULL;
	}

	if (hp->h_addr == NULL) {
		rprintf(FERROR, "gethostbyname: host address is invalid for host \"%s\"\n",str);
		return NULL;
	}

	if (hp->h_length > sizeof(ret)) {
		rprintf(FERROR, "gethostbyname: host address for \"%s\" is too large\n",
			str);
		return NULL;
	}

	memcpy(&ret.s_addr, hp->h_addr, hp->h_length);

	return(&ret);
}
