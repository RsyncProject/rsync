/*
 * Functions for looking up the remote name or addr of a socket.
 *
 * Copyright (C) 1992-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2002-2009 Wayne Davison
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

/*
 * This file is now converted to use the new-style getaddrinfo()
 * interface, which supports IPv6 but is also supported on recent
 * IPv4-only machines.  On systems that don't have that interface, we
 * emulate it using the KAME implementation.
 */

#include "rsync.h"

static const char default_name[] = "UNKNOWN";
extern int am_server;


/**
 * Return the IP addr of the client as a string
 **/
char *client_addr(int fd)
{
	static char addr_buf[100];
	static int initialised;
	struct sockaddr_storage ss;
	socklen_t length = sizeof ss;
	char *ssh_info, *p;

	if (initialised)
		return addr_buf;

	initialised = 1;

	if (am_server) {	/* daemon over --rsh mode */
		strlcpy(addr_buf, "0.0.0.0", sizeof addr_buf);
		if ((ssh_info = getenv("SSH_CONNECTION")) != NULL
		    || (ssh_info = getenv("SSH_CLIENT")) != NULL
		    || (ssh_info = getenv("SSH2_CLIENT")) != NULL) {
			strlcpy(addr_buf, ssh_info, sizeof addr_buf);
			/* Truncate the value to just the IP address. */
			if ((p = strchr(addr_buf, ' ')) != NULL)
				*p = '\0';
		}
	} else {
		client_sockaddr(fd, &ss, &length);
		getnameinfo((struct sockaddr *)&ss, length,
			    addr_buf, sizeof addr_buf, NULL, 0, NI_NUMERICHOST);
	}

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
 *
 * After translation from sockaddr to name we do a forward lookup to
 * make sure nobody is spoofing PTR records.
 **/
char *client_name(int fd)
{
	static char name_buf[100];
	static char port_buf[100];
	static int initialised;
	struct sockaddr_storage ss;
	socklen_t ss_len;

	if (initialised)
		return name_buf;

	strlcpy(name_buf, default_name, sizeof name_buf);
	initialised = 1;

	memset(&ss, 0, sizeof ss);

	if (am_server) {	/* daemon over --rsh mode */
		char *addr = client_addr(fd);
		struct addrinfo hint, *answer;
		int err;

		if (strcmp(addr, "0.0.0.0") == 0)
			return name_buf;

		memset(&hint, 0, sizeof hint);

#ifdef AI_NUMERICHOST
		hint.ai_flags = AI_NUMERICHOST;
#endif
		hint.ai_socktype = SOCK_STREAM;

		if ((err = getaddrinfo(addr, NULL, &hint, &answer)) != 0) {
			rprintf(FLOG, "malformed address %s: %s\n",
			        addr, gai_strerror(err));
			return name_buf;
		}

		switch (answer->ai_family) {
		case AF_INET:
			ss_len = sizeof (struct sockaddr_in);
			memcpy(&ss, answer->ai_addr, ss_len);
			break;
#ifdef INET6
		case AF_INET6:
			ss_len = sizeof (struct sockaddr_in6);
			memcpy(&ss, answer->ai_addr, ss_len);
			break;
#endif
		default:
			exit_cleanup(RERR_SOCKETIO);
		}
		freeaddrinfo(answer);
	} else {
		ss_len = sizeof ss;
		client_sockaddr(fd, &ss, &ss_len);
	}

	if (lookup_name(fd, &ss, ss_len, name_buf, sizeof name_buf,
			port_buf, sizeof port_buf) == 0)
		check_name(fd, &ss, name_buf, sizeof name_buf);

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
	memset(ss, 0, sizeof *ss);

	if (getpeername(fd, (struct sockaddr *) ss, ss_len)) {
		/* FIXME: Can we really not continue? */
		rsyserr(FLOG, errno, "getpeername on fd%d failed", fd);
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

		memcpy(&sin6, ss, sizeof sin6);
		sin = (struct sockaddr_in *)ss;
		memset(sin, 0, sizeof *sin);
		sin->sin_family = AF_INET;
		*ss_len = sizeof (struct sockaddr_in);
#ifdef HAVE_SOCKADDR_IN_LEN
		sin->sin_len = *ss_len;
#endif
		sin->sin_port = sin6.sin6_port;

		/* There is a macro to extract the mapped part
		 * (IN6_V4MAPPED_TO_SINADDR ?), but it does not seem
		 * to be present in the Linux headers. */
		memcpy(&sin->sin_addr, &sin6.sin6_addr.s6_addr[12],
		    sizeof sin->sin_addr);
	}
#endif
}


/**
 * Look up a name from @p ss into @p name_buf.
 *
 * @param fd file descriptor for client socket.
 **/
int lookup_name(int fd, const struct sockaddr_storage *ss,
		socklen_t ss_len,
		char *name_buf, size_t name_buf_size,
		char *port_buf, size_t port_buf_size)
{
	int name_err;

	/* reverse lookup */
	name_err = getnameinfo((struct sockaddr *) ss, ss_len,
			       name_buf, name_buf_size,
			       port_buf, port_buf_size,
			       NI_NAMEREQD | NI_NUMERICSERV);
	if (name_err != 0) {
		strlcpy(name_buf, default_name, name_buf_size);
		rprintf(FLOG, "name lookup failed for %s: %s\n",
			client_addr(fd), gai_strerror(name_err));
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
		rprintf(FLOG, "%s: response family %d != %d\n",
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
	if (ss_family == AF_INET6) {
		const struct sockaddr_in6 *sin1, *sin2;

		sin1 = (const struct sockaddr_in6 *) ss;
		sin2 = (const struct sockaddr_in6 *) ai->ai_addr;

		if (ai->ai_addrlen < sizeof (struct sockaddr_in6)) {
			rprintf(FLOG, "%s: too short sockaddr_in6; length=%d\n",
				fn, (int)ai->ai_addrlen);
			return 1;
		}

		if (memcmp(&sin1->sin6_addr, &sin2->sin6_addr,
			   sizeof sin1->sin6_addr))
			return 1;

#ifdef HAVE_SOCKADDR_IN6_SCOPE_ID
		if (sin1->sin6_scope_id != sin2->sin6_scope_id)
			return 1;
#endif
		return 0;
	}
#endif /* INET6 */

	/* don't know */
	return 1;
}


/**
 * Do a forward lookup on @p name_buf and make sure it corresponds to
 * @p ss -- otherwise we may be being spoofed.  If we suspect we are,
 * then we don't abort the connection but just emit a warning, and
 * change @p name_buf to be "UNKNOWN".
 *
 * We don't do anything with the service when checking the name,
 * because it doesn't seem that it could be spoofed in any way, and
 * getaddrinfo on random service names seems to cause problems on AIX.
 **/
int check_name(int fd,
	       const struct sockaddr_storage *ss,
	       char *name_buf, size_t name_buf_size)
{
	struct addrinfo hints, *res, *res0;
	int error;
	int ss_family = get_sockaddr_family(ss);

	memset(&hints, 0, sizeof hints);
	hints.ai_family = ss_family;
	hints.ai_flags = AI_CANONNAME;
	hints.ai_socktype = SOCK_STREAM;
	error = getaddrinfo(name_buf, NULL, &hints, &res0);
	if (error) {
		rprintf(FLOG, "forward name lookup for %s failed: %s\n",
			name_buf, gai_strerror(error));
		strlcpy(name_buf, default_name, name_buf_size);
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
		rprintf(FLOG, "no known address for \"%s\": "
			"spoofed address?\n", name_buf);
		strlcpy(name_buf, default_name, name_buf_size);
	} else if (res == NULL) {
		/* We hit the end of the list without finding an
		 * address that was the same as ss. */
		rprintf(FLOG, "%s is not a known address for \"%s\": "
			"spoofed address?\n", client_addr(fd), name_buf);
		strlcpy(name_buf, default_name, name_buf_size);
	}

	freeaddrinfo(res0);
	return 0;
}
