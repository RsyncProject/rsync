/*
 * Functions for looking up the remote name or addr of a socket.
 *
 * Copyright (C) 1992-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2002-2020 Wayne Davison
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
#include "itypes.h"

extern int am_daemon;

static const char default_name[] = "UNKNOWN";
static const char proxyv2sig[] = "\r\n\r\n\0\r\nQUIT\n";

static char ipaddr_buf[100];

#define PROXY_V2_SIG_SIZE ((int)sizeof proxyv2sig - 1)
#define PROXY_V2_HEADER_SIZE (PROXY_V2_SIG_SIZE + 1 + 1 + 2)

#define CMD_LOCAL 0
#define CMD_PROXY 1

#define PROXY_FAM_TCPv4 0x11
#define PROXY_FAM_TCPv6 0x21

#define GET_SOCKADDR_FAMILY(ss) ((struct sockaddr*)ss)->sa_family

static void client_sockaddr(int fd, struct sockaddr_storage *ss, socklen_t *ss_len);
static int check_name(const char *ipaddr, const struct sockaddr_storage *ss, char *name_buf, size_t name_buf_size);
static int valid_ipaddr(const char *s);

/* Return the IP addr of the client as a string. */
char *client_addr(int fd)
{
	struct sockaddr_storage ss;
	socklen_t length = sizeof ss;

	if (*ipaddr_buf)
		return ipaddr_buf;

	if (am_daemon < 0) {	/* daemon over --rsh mode */
		char *env_str;
		strlcpy(ipaddr_buf, "0.0.0.0", sizeof ipaddr_buf);
		if ((env_str = getenv("REMOTE_HOST")) != NULL
		 || (env_str = getenv("SSH_CONNECTION")) != NULL
		 || (env_str = getenv("SSH_CLIENT")) != NULL
		 || (env_str = getenv("SSH2_CLIENT")) != NULL) {
			char *p;
			strlcpy(ipaddr_buf, env_str, sizeof ipaddr_buf);
			/* Truncate the value to just the IP address. */
			if ((p = strchr(ipaddr_buf, ' ')) != NULL)
				*p = '\0';
		}
		if (valid_ipaddr(ipaddr_buf))
			return ipaddr_buf;
	}

	client_sockaddr(fd, &ss, &length);
	getnameinfo((struct sockaddr *)&ss, length, ipaddr_buf, sizeof ipaddr_buf, NULL, 0, NI_NUMERICHOST);

	return ipaddr_buf;
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
char *client_name(const char *ipaddr)
{
	static char name_buf[100];
	char port_buf[100];
	struct sockaddr_storage ss;
	socklen_t ss_len;
	struct addrinfo hint, *answer;
	int err;

	if (*name_buf)
		return name_buf;

	strlcpy(name_buf, default_name, sizeof name_buf);

	if (strcmp(ipaddr, "0.0.0.0") == 0)
		return name_buf;

	memset(&ss, 0, sizeof ss);
	memset(&hint, 0, sizeof hint);

#ifdef AI_NUMERICHOST
	hint.ai_flags = AI_NUMERICHOST;
#endif
	hint.ai_socktype = SOCK_STREAM;

	if ((err = getaddrinfo(ipaddr, NULL, &hint, &answer)) != 0) {
		rprintf(FLOG, "malformed address %s: %s\n", ipaddr, gai_strerror(err));
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
		NOISY_DEATH("Unknown ai_family value");
	}
	freeaddrinfo(answer);

	/* reverse lookup */
	err = getnameinfo((struct sockaddr*)&ss, ss_len, name_buf, sizeof name_buf,
			  port_buf, sizeof port_buf, NI_NAMEREQD | NI_NUMERICSERV);
	if (err) {
		strlcpy(name_buf, default_name, sizeof name_buf);
		rprintf(FLOG, "name lookup failed for %s: %s\n", ipaddr, gai_strerror(err));
	} else
		check_name(ipaddr, &ss, name_buf, sizeof name_buf);

	return name_buf;
}


/* Try to read a proxy protocol header (V1 or V2). Returns 1 on success or 0 on failure. */
int read_proxy_protocol_header(int fd)
{
	union {
		struct {
			char line[108];
		} v1;
		struct {
			char sig[PROXY_V2_SIG_SIZE];
			char ver_cmd;
			char fam;
			char len[2];
			union {
				struct {
					char src_addr[4];
					char dst_addr[4];
					char src_port[2];
					char dst_port[2];
				} ip4;
				struct {
					char src_addr[16];
					char dst_addr[16];
					char src_port[2];
					char dst_port[2];
				} ip6;
				struct {
					char src_addr[108];
					char dst_addr[108];
				} unx;
			} addr;
		} v2;
	} hdr;

	read_buf(fd, (char*)&hdr, PROXY_V2_SIG_SIZE);

	if (memcmp(hdr.v2.sig, proxyv2sig, PROXY_V2_SIG_SIZE) == 0) { /* Proxy V2 */
		int ver, cmd, size;

		read_buf(fd, (char*)&hdr + PROXY_V2_SIG_SIZE, PROXY_V2_HEADER_SIZE - PROXY_V2_SIG_SIZE);

		ver = (hdr.v2.ver_cmd & 0xf0) >> 4;
		cmd = (hdr.v2.ver_cmd & 0x0f);
		size = (hdr.v2.len[0] << 8) + hdr.v2.len[1];

		if (ver != 2 || size + PROXY_V2_HEADER_SIZE > (int)sizeof hdr)
			return 0;

		/* Grab all the remaining data in the binary request. */
		read_buf(fd, (char*)&hdr + PROXY_V2_HEADER_SIZE, size);

		switch (cmd) {
		case CMD_PROXY:
			switch (hdr.v2.fam) {
			case PROXY_FAM_TCPv4:
				if (size != sizeof hdr.v2.addr.ip4)
					return 0;
				inet_ntop(AF_INET, hdr.v2.addr.ip4.src_addr, ipaddr_buf, sizeof ipaddr_buf);
				return valid_ipaddr(ipaddr_buf);
			case PROXY_FAM_TCPv6:
				if (size != sizeof hdr.v2.addr.ip6)
					return 0;
				inet_ntop(AF_INET6, hdr.v2.addr.ip6.src_addr, ipaddr_buf, sizeof ipaddr_buf);
				return valid_ipaddr(ipaddr_buf);
			default:
				break;
			}
			/* For an unsupported protocol we'll ignore the proxy data (leaving ipaddr_buf unset)
			 * and accept the connection, which will get handled as a normal socket addr. */
			return 1;
		case CMD_LOCAL:
			return 1;
		default:
			break;
		}

		return 0;
	}

	if (memcmp(hdr.v1.line, "PROXY", 5) == 0) { /* Proxy V1 */
		char *endc, *sp, *p = hdr.v1.line + PROXY_V2_SIG_SIZE;
		int port_chk;

		*p = '\0';
		if (!strchr(hdr.v1.line, '\n')) {
			while (1) {
				read_buf(fd, p, 1);
				if (*p++ == '\n')
					break;
				if (p - hdr.v1.line >= (int)sizeof hdr.v1.line - 1)
					return 0;
			}
			*p = '\0';
		}

		endc = strchr(hdr.v1.line, '\r');
		if (!endc || endc[1] != '\n' || endc[2])
			return 0;
		*endc = '\0';

		p = hdr.v1.line + 5;

		if (!isSpace(p++))
			return 0;
		if (strncmp(p, "TCP4", 4) == 0)
			p += 4;
		else if (strncmp(p, "TCP6", 4) == 0)
			p += 4;
		else if (strncmp(p, "UNKNOWN", 7) == 0)
			return 1;
		else
			return 0;

		if (!isSpace(p++))
			return 0;

		if ((sp = strchr(p, ' ')) == NULL)
			return 0;
		*sp = '\0';
		if (!valid_ipaddr(p))
			return 0;
		strlcpy(ipaddr_buf, p, sizeof ipaddr_buf); /* It will always fit when valid. */

		p = sp + 1;
		if ((sp = strchr(p, ' ')) == NULL)
			return 0;
		*sp = '\0';
		if (!valid_ipaddr(p))
			return 0;
		/* Ignore destination address. */

		p = sp + 1;
		if ((sp = strchr(p, ' ')) == NULL)
			return 0;
		*sp = '\0';
		port_chk = strtol(p, &endc, 10);
		if (*endc || port_chk == 0)
			return 0;
		/* Ignore source port. */

		p = sp + 1;
		port_chk = strtol(p, &endc, 10);
		if (*endc || port_chk == 0)
			return 0;
		/* Ignore destination port. */

		return 1;
	}

	return 0;
}


/**
 * Get the sockaddr for the client.
 *
 * If it comes in as an ipv4 address mapped into IPv6 format then we
 * convert it back to a regular IPv4.
 **/
static void client_sockaddr(int fd, struct sockaddr_storage *ss, socklen_t *ss_len)
{
	memset(ss, 0, sizeof *ss);

	if (getpeername(fd, (struct sockaddr *) ss, ss_len)) {
		/* FIXME: Can we really not continue? */
		rsyserr(FLOG, errno, "getpeername on fd%d failed", fd);
		exit_cleanup(RERR_SOCKETIO);
	}

#ifdef INET6
	if (GET_SOCKADDR_FAMILY(ss) == AF_INET6
	 && IN6_IS_ADDR_V4MAPPED(&((struct sockaddr_in6 *)ss)->sin6_addr)) {
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
		memcpy(&sin->sin_addr, &sin6.sin6_addr.s6_addr[12], sizeof sin->sin_addr);
	}
#endif
}


/**
 * Compare an addrinfo from the resolver to a sockinfo.
 *
 * Like strcmp, returns 0 for identical.
 **/
static int compare_addrinfo_sockaddr(const struct addrinfo *ai, const struct sockaddr_storage *ss)
{
	int ss_family = GET_SOCKADDR_FAMILY(ss);
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

		return memcmp(&sin1->sin_addr, &sin2->sin_addr, sizeof sin1->sin_addr);
	}

#ifdef INET6
	if (ss_family == AF_INET6) {
		const struct sockaddr_in6 *sin1, *sin2;

		sin1 = (const struct sockaddr_in6 *) ss;
		sin2 = (const struct sockaddr_in6 *) ai->ai_addr;

		if (ai->ai_addrlen < (int)sizeof (struct sockaddr_in6)) {
			rprintf(FLOG, "%s: too short sockaddr_in6; length=%d\n",
				fn, (int)ai->ai_addrlen);
			return 1;
		}

		if (memcmp(&sin1->sin6_addr, &sin2->sin6_addr, sizeof sin1->sin6_addr))
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
static int check_name(const char *ipaddr, const struct sockaddr_storage *ss, char *name_buf, size_t name_buf_size)
{
	struct addrinfo hints, *res, *res0;
	int error;
	int ss_family = GET_SOCKADDR_FAMILY(ss);

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
			"spoofed address?\n", ipaddr, name_buf);
		strlcpy(name_buf, default_name, name_buf_size);
	}

	freeaddrinfo(res0);
	return 0;
}

/* Returns 1 for a valid IPv4 or IPv6 addr, or 0 for a bad one. */
static int valid_ipaddr(const char *s)
{
	int i;

	if (strchr(s, ':') != NULL) { /* Only IPv6 has a colon. */
		int count, saw_double_colon = 0;
		int ipv4_at_end = 0;

		if (*s == ':') { /* A colon at the start must be a :: */
			if (*++s != ':')
				return 0;
			saw_double_colon = 1;
			s++;
		}

		for (count = 0; count < 8; count++) {
			if (!*s)
				return saw_double_colon;

			if (strchr(s, ':') == NULL && strchr(s, '.') != NULL) {
				if ((!saw_double_colon && count != 6) || (saw_double_colon && count > 6))
					return 0;
				ipv4_at_end = 1;
				break;
			}

			if (!isHexDigit(s++)) /* Need 1-4 hex digits */
				return 0;
			if (isHexDigit(s) && isHexDigit(++s) && isHexDigit(++s) && isHexDigit(++s))
				return 0;

			if (*s == ':') {
				if (!*++s)
					return 0;
				if (*s == ':') {
					if (saw_double_colon)
						return 0;
					saw_double_colon = 1;
					s++;
				}
			}
		}

		if (!ipv4_at_end)
			return !*s;
	}

	/* IPv4 */
	for (i = 0; i < 4; i++) {
		long n;
		char *end;

		if (i && *s++ != '.')
			return 0;
		n = strtol(s, &end, 10);
		if (n > 255 || n < 0 || end <= s || end > s+3)
			return 0;
		s = end;
	}

	return !*s;
}
