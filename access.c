/*
 * Routines to authenticate access to a daemon (hosts allow/deny).
 *
 * Copyright (C) 1998 Andrew Tridgell
 * Copyright (C) 2004-2020 Wayne Davison
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

#include "rsync.h"
#include "ifuncs.h"
#ifdef HAVE_NETGROUP_H
#include <netgroup.h>
#endif

static int allow_forward_dns;

extern const char undetermined_hostname[];

static int match_hostname(const char **host_ptr, const char *addr, const char *tok)
{
	struct hostent *hp;
	unsigned int i;
	const char *host = *host_ptr;

	if (!host || !*host)
		return 0;

#ifdef HAVE_INNETGR
	if (*tok == '@' && tok[1])
		return innetgr(tok + 1, host, NULL, NULL);
#endif

	/* First check if the reverse-DNS-determined hostname matches. */
	if (iwildmatch(tok, host))
		return 1;

	if (!allow_forward_dns)
		return 0;

	/* Fail quietly if tok is an address or wildcarded entry, not a simple hostname. */
	if (!tok[strspn(tok, ".0123456789")] || tok[strcspn(tok, ":/*?[")])
		return 0;

	/* Now try forward-DNS on the token (config-specified hostname) and see if the IP matches. */
	if (!(hp = gethostbyname(tok)))
		return 0;

	for (i = 0; hp->h_addr_list[i] != NULL; i++) {
		if (strcmp(addr, inet_ntoa(*(struct in_addr*)(hp->h_addr_list[i]))) == 0) {
			/* If reverse lookups are off, we'll use the conf-specified
			 * hostname in preference to UNDETERMINED. */
			if (host == undetermined_hostname)
				*host_ptr = strdup(tok);
			return 1;
		}
	}

	return 0;
}

static int match_binary(const char *b1, const char *b2, const char *mask, int addrlen)
{
	int i;

	for (i = 0; i < addrlen; i++) {
		if ((b1[i] ^ b2[i]) & mask[i])
			return 0;
	}

	return 1;
}

static void make_mask(char *mask, int plen, int addrlen)
{
	int w, b;

	w = plen >> 3;
	b = plen & 0x7;

	if (w)
		memset(mask, 0xff, w);
	if (w < addrlen)
		mask[w] = 0xff & (0xff<<(8-b));
	if (w+1 < addrlen)
		memset(mask+w+1, 0, addrlen-w-1);

	return;
}

static int match_address(const char *addr, const char *tok)
{
	char *p;
	struct addrinfo hints, *resa, *rest;
	int gai;
	int ret = 0;
	int addrlen = 0;
#ifdef HAVE_STRTOL
	long int bits;
#else
	int bits;
#endif
	char mask[16];
	char *a = NULL, *t = NULL;

	if (!addr || !*addr)
		return 0;

	p = strchr(tok,'/');
	if (p)
		*p = '\0';

	/* Fail quietly if tok is a hostname, not an address. */
	if (tok[strspn(tok, ".0123456789")] && strchr(tok, ':') == NULL) {
		if (p)
			*p = '/';
		return 0;
	}

	memset(&hints, 0, sizeof(hints));
	hints.ai_family = PF_UNSPEC;
	hints.ai_socktype = SOCK_STREAM;
#ifdef AI_NUMERICHOST
	hints.ai_flags = AI_NUMERICHOST;
#endif

	if (getaddrinfo(addr, NULL, &hints, &resa) != 0) {
		if (p)
			*p = '/';
		return 0;
	}

	gai = getaddrinfo(tok, NULL, &hints, &rest);
	if (p)
		*p++ = '/';
	if (gai != 0) {
		rprintf(FLOG, "error matching address %s: %s\n",
			tok, gai_strerror(gai));
		freeaddrinfo(resa);
		return 0;
	}

	if (rest->ai_family != resa->ai_family) {
		ret = 0;
		goto out;
	}

	switch(resa->ai_family) {
	case PF_INET:
		a = (char *)&((struct sockaddr_in *)resa->ai_addr)->sin_addr;
		t = (char *)&((struct sockaddr_in *)rest->ai_addr)->sin_addr;
		addrlen = 4;

		break;

#ifdef INET6
	case PF_INET6: {
		struct sockaddr_in6 *sin6a, *sin6t;

		sin6a = (struct sockaddr_in6 *)resa->ai_addr;
		sin6t = (struct sockaddr_in6 *)rest->ai_addr;

		a = (char *)&sin6a->sin6_addr;
		t = (char *)&sin6t->sin6_addr;

		addrlen = 16;

#ifdef HAVE_SOCKADDR_IN6_SCOPE_ID
		if (sin6t->sin6_scope_id && sin6a->sin6_scope_id != sin6t->sin6_scope_id) {
			ret = 0;
			goto out;
		}
#endif

		break;
	}
#endif
	default:
		rprintf(FLOG, "unknown family %u\n", rest->ai_family);
		ret = 0;
		goto out;
	}

	bits = -1;
	if (p) {
		if (inet_pton(resa->ai_addr->sa_family, p, mask) <= 0) {
#ifdef HAVE_STRTOL
			char *ep = NULL;
#else
			unsigned char *pp;
#endif

#ifdef HAVE_STRTOL
			bits = strtol(p, &ep, 10);
			if (!*p || *ep) {
				rprintf(FLOG, "malformed mask in %s\n", tok);
				ret = 0;
				goto out;
			}
#else
			for (pp = (unsigned char *)p; *pp; pp++) {
				if (!isascii(*pp) || !isdigit(*pp)) {
					rprintf(FLOG, "malformed mask in %s\n", tok);
					ret = 0;
					goto out;
				}
			}
			bits = atoi(p);
#endif
			if (bits == 0) {
				ret = 1;
				goto out;
			}
			if (bits < 0 || bits > (addrlen << 3)) {
				rprintf(FLOG, "malformed mask in %s\n", tok);
				ret = 0;
				goto out;
			}
		}
	} else {
		bits = 128;
	}

	if (bits >= 0)
		make_mask(mask, bits, addrlen);

	ret = match_binary(a, t, mask, addrlen);

  out:
	freeaddrinfo(resa);
	freeaddrinfo(rest);
	return ret;
}

static int access_match(const char *list, const char *addr, const char **host_ptr)
{
	char *tok;
	char *list2 = strdup(list);

	strlower(list2);

	for (tok = strtok(list2, " ,\t"); tok; tok = strtok(NULL, " ,\t")) {
		if (match_hostname(host_ptr, addr, tok) || match_address(addr, tok)) {
			free(list2);
			return 1;
		}
	}

	free(list2);
	return 0;
}

int allow_access(const char *addr, const char **host_ptr, int i)
{
	const char *allow_list = lp_hosts_allow(i);
	const char *deny_list = lp_hosts_deny(i);

	if (allow_list && !*allow_list)
		allow_list = NULL;
	if (deny_list && !*deny_list)
		deny_list = NULL;

	allow_forward_dns = lp_forward_lookup(i);

	/* If we match an allow-list item, we always allow access. */
	if (allow_list) {
		if (access_match(allow_list, addr, host_ptr))
			return 1;
		/* For an allow-list w/o a deny-list, disallow non-matches. */
		if (!deny_list)
			return 0;
	}

	/* If we match a deny-list item (and got past any allow-list
	 * items), we always disallow access. */
	if (deny_list && access_match(deny_list, addr, host_ptr))
		return 0;

	/* Allow all other access. */
	return 1;
}
