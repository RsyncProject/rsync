/* 
   Copyright (C) Andrew Tridgell 1998
   
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

/* open a socket to a tcp remote host with the specified port 
   based on code from Warren */
int open_socket_out(char *host, int port)
{
	int type = SOCK_STREAM;
	struct sockaddr_in sock_out;
	int res;
	struct hostent *hp;
  

	res = socket(PF_INET, type, 0);
	if (res == -1) {
		return -1;
	}

	hp = gethostbyname(host);
	if (!hp) {
		rprintf(FERROR,"unknown host: %s\n", host);
		return -1;
	}

	memcpy(&sock_out.sin_addr, hp->h_addr, hp->h_length);
	sock_out.sin_port = htons(port);
	sock_out.sin_family = PF_INET;

	if (connect(res,(struct sockaddr *)&sock_out,sizeof(sock_out))) {
		close(res);
		rprintf(FERROR,"failed to connect to %s - %s\n", host, strerror(errno));
		return -1;
	}

	set_nonblocking(res);

	return res;
}


/****************************************************************************
open a socket of the specified type, port and address for incoming data
****************************************************************************/
static int open_socket_in(int type, int port)
{
	struct hostent *hp;
	struct sockaddr_in sock;
	char host_name[MAXHOSTNAMELEN];
	int res;
	int one=1;

	/* get my host name */
	if (gethostname(host_name, sizeof(host_name)) == -1) { 
		rprintf(FERROR,"gethostname failed\n"); 
		return -1; 
	} 

	/* get host info */
	if ((hp = gethostbyname(host_name)) == 0) {
		rprintf(FERROR,"gethostbyname: Unknown host %s\n",host_name);
		return -1;
	}
  
	memset((char *)&sock,0,sizeof(sock));
	memcpy((char *)&sock.sin_addr,(char *)hp->h_addr, hp->h_length);
	sock.sin_port = htons(port);
	sock.sin_family = hp->h_addrtype;
	sock.sin_addr.s_addr = INADDR_ANY;
	res = socket(hp->h_addrtype, type, 0);
	if (res == -1) { 
		rprintf(FERROR,"socket failed\n"); 
		return -1; 
	}

	setsockopt(res,SOL_SOCKET,SO_REUSEADDR,(char *)&one,sizeof(one));

	/* now we've got a socket - we need to bind it */
	if (bind(res, (struct sockaddr * ) &sock,sizeof(sock)) == -1) { 
		rprintf(FERROR,"bind failed on port %d\n", port);
		close(res); 
		return -1;
	}

	return res;
}


/****************************************************************************
determine if a file descriptor is in fact a socket
****************************************************************************/
int is_a_socket(int fd)
{
	int v,l;
	l = sizeof(int);
	return(getsockopt(fd, SOL_SOCKET, SO_TYPE, (char *)&v, &l) == 0);
}


void start_accept_loop(int port, int (*fn)(int ))
{
	int s;

	/* open an incoming socket */
	s = open_socket_in(SOCK_STREAM, port);
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
		int in_addrlen = sizeof(addr);

		FD_ZERO(&fds);
		FD_SET(s, &fds);

		if (select(s+1, &fds, NULL, NULL, NULL) != 1) {
			continue;
		}

		if(!FD_ISSET(s, &fds)) continue;

		fd = accept(s,&addr,&in_addrlen);

		if (fd == -1) continue;

		signal(SIGCHLD, SIG_IGN);

		/* we shouldn't have any children left hanging around
		   but I have had reports that on Digital Unix zombies
		   are produced, so this ensures that they are reaped */
#ifdef WNOHANG
		waitpid(-1, NULL, WNOHANG);
#endif

		if (fork()==0) {
			close(s);

			set_nonblocking(fd);

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
			rprintf(FERROR,"Failed to set socket option %s\n",tok);
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
	int     length = sizeof(sa);
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
		strcpy(name_buf,def);
		rprintf(FERROR,"reverse name lookup failed\n");
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
