/* -*- c-file-style: "linux" -*-
   
   rsync -- fast file replication program
   
   Copyright (C) 1992-2001 by Andrew Tridgell <tridge@samba.org>
   Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
   
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

/**
 * @file clientname.c
 * 
 * Functions for looking up the remote name or addr of a socket.
 *
 * This file is now converted to use the new-style getaddrinfo()
 * interface, which supports IPv6 but is also supported on recent
 * IPv4-only machines.  On systems that don't have that interface, we
 * emulate it using the KAME implementation.
 **/

#include "rsync.h"

static const char default_name[] = "UNKNOWN";


/**
 * Return the IP addr of the client as a string 
 **/
char *client_addr(int fd)
{
	struct sockaddr_storage ss;
	socklen_t length = sizeof ss;
	static char addr_buf[100];
	static int initialised;

	if (initialised) return addr_buf;

	initialised = 1;

	client_sockaddr(fd, &ss, &length);

	getnameinfo((struct sockaddr *)&ss, length,
		    addr_buf, sizeof(addr_buf), NULL, 0, NI_NUMERICHOST);
	
	return addr_buf;
}


static int get_sockaddr_family(const struct sockaddr_storage *ss)
{
	return ((struct sockaddr *) ss)->sa_family;
}


/**
 * Return the DNS name of the client.
 *
 * The name is statically cached so that repeated lookups are quick,
 * so there is a limit of one lookup per customer.
 *
 * If anything goes wrong, including the name->addr->name check, then
 * we just use "UNKNOWN", so you can use that value in hosts allow
 * lines.
 **/
char *client_name(int fd)
{
	struct sockaddr_storage ss;
	socklen_t ss_len = sizeof ss;
	static char name_buf[100];
	static char port_buf[100];
	static int initialised;

	if (initialised) return name_buf;

	strcpy(name_buf, default_name);
	initialised = 1;

	client_sockaddr(fd, &ss, &ss_len);

	if (!lookup_name(fd, &ss, ss_len, name_buf, sizeof name_buf, port_buf, sizeof port_buf))
		check_name(fd, &ss, name_buf, port_buf);

	return name_buf;
}



/**
 * Get the sockaddr for the client.
 *
 * If it comes in as an ipv4 address mapped into IPv6 format then we
 * convert it back to a regular IPv4.
 **/
void client_sockaddr(int fd,
		     struct sockaddr_storage *ss,
		     socklen_t *ss_len)
{
	if (getpeername(fd, (struct sockaddr *) ss, ss_len)) {
		/* FIXME: Can we really not continue? */
		rprintf(FERROR, RSYNC_NAME ": getpeername on fd%d failed: %s\n",
			fd, strerror(errno));
		exit_cleanup(RERR_SOCKETIO);
	}

#ifdef INET6
        if (get_sockaddr_family(ss) == AF_INET6 && 
	    IN6_IS_ADDR_V4MAPPED(&((struct sockaddr_in6 *)ss)->sin6_addr)) {
		/* OK, so ss is in the IPv6 family, but it is really
		 * an IPv4 address: something like
		 * "::ffff:10.130.1.2".  If we use it as-is, then the
		 * reverse lookup might fail or perhaps something else
		 * bad might happen.  So instead we convert it to an
		 * equivalent address in the IPv4 address family.  */
		struct sockaddr_in6 sin6;
		struct sockaddr_in *sin;

		memcpy(&sin6, ss, sizeof(sin6));
		sin = (struct sockaddr_in *)ss;
		memset(sin, 0, sizeof(*sin));
		sin->sin_family = AF_INET;
		*ss_len = sizeof(struct sockaddr_in);
#ifdef HAVE_SOCKADDR_LEN
		sin->sin_len = *ss_len;
#endif
		sin->sin_port = sin6.sin6_port;

		/* There is a macro to extract the mapped part
		 * (IN6_V4MAPPED_TO_SINADDR ?), but it does not seem
		 * to be present in the Linux headers. */
		memcpy(&sin->sin_addr, &sin6.sin6_addr.s6_addr[12],
			sizeof(sin->sin_addr));
        }
#endif
}


/**
 * Look up a name from @p ss into @p name_buf.
 **/
int lookup_name(int fd, const struct sockaddr_storage *ss,
		socklen_t ss_len,
		char *name_buf, size_t name_buf_len,
		char *port_buf, size_t port_buf_len)
{
	int name_err;
	
	/* reverse lookup */
	name_err = getnameinfo((struct sockaddr *) ss, ss_len,
			       name_buf, name_buf_len,
			       port_buf, port_buf_len,
			       NI_NAMEREQD | NI_NUMERICSERV);
	if (name_err != 0) {
		strcpy(name_buf, default_name);
		rprintf(FERROR, RSYNC_NAME ": name lookup failed for %s: %s\n",
			client_addr(fd),
			gai_strerror(name_err));
		return name_err;
	}

	return 0;
}



/**
 * Compare an addrinfo from the resolver to a sockinfo.
 *
 * Like strcmp, returns 0 for identical.
 **/
int compare_addrinfo_sockaddr(const struct addrinfo *ai,
			      const struct sockaddr_storage *ss)
{
	int ss_family = get_sockaddr_family(ss);
	const char fn[] = "compare_addrinfo_sockaddr";
		      
	if (ai->ai_family != ss_family) {
		rprintf(FERROR,
			"%s: response family %d != %d\n",
			fn, ai->ai_family, ss_family);
		return 1;
	}

	/* The comparison method depends on the particular AF. */
	if (ss_family == AF_INET) {
		const struct sockaddr_in *sin1, *sin2;

		sin1 = (const struct sockaddr_in *) ss;
		sin2 = (const struct sockaddr_in *) ai->ai_addr;
		
		return memcmp(&sin1->sin_addr, &sin2->sin_addr,
			      sizeof sin1->sin_addr);
	}
#ifdef INET6
	else if (ss_family == AF_INET6) {
		const struct sockaddr_in6 *sin1, *sin2;

		sin1 = (const struct sockaddr_in6 *) ss;
		sin2 = (const struct sockaddr_in6 *) ai->ai_addr;
		
		return memcmp(&sin1->sin6_addr, &sin2->sin6_addr,
			      sizeof sin1->sin6_addr);
	}
#endif /* INET6 */
	else {
		/* don't know */
		return 1;
	}
}


/**
 * Do a forward lookup on @p name_buf and make sure it corresponds to
 * @p ss -- otherwise we may be being spoofed.  If we suspect we are,
 * then we don't abort the connection but just emit a warning, and
 * change @p name_buf to be "UNKNOWN".
 **/
int check_name(int fd,
	       const struct sockaddr_storage *ss,
	       char *name_buf,
	       const char *port_buf)
{
	struct addrinfo hints, *res, *res0;
	int error;
	int ss_family = get_sockaddr_family(ss);

	memset(&hints, 0, sizeof(hints));
	hints.ai_family = ss_family;
	hints.ai_flags = AI_CANONNAME;
	hints.ai_socktype = SOCK_STREAM;
	error = getaddrinfo(name_buf, port_buf, &hints, &res0);
	if (error) {
		rprintf(FERROR,
			RSYNC_NAME ": forward name lookup for %s failed: %s\n",
			name_buf, gai_strerror(error));
		strcpy(name_buf, default_name);
		return error;
	}


	/* Given all these results, we expect that one of them will be
	 * the same as ss.  The comparison is a bit complicated. */
	for (res = res0; res; res = res->ai_next) {
		if (!compare_addrinfo_sockaddr(res, ss))
			break;	/* OK, identical */
	}

	if (!res0) {
		/* We hit the end of the list without finding an
		 * address that was the same as ss. */
		rprintf(FERROR, RSYNC_NAME
			": no known address for \"%s\": "
			"spoofed address?\n",
			name_buf);
		strcpy(name_buf, default_name);
	} else if (res == NULL) {
		/* We hit the end of the list without finding an
		 * address that was the same as ss. */
		rprintf(FERROR, RSYNC_NAME
			": %s is not a known address for \"%s\": "
			"spoofed address?\n",
			client_addr(fd),
			name_buf);
		strcpy(name_buf, default_name);
	}

	freeaddrinfo(res0);
	return 0;
}

