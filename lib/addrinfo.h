/*
PostgreSQL Database Management System
(formerly known as Postgres, then as Postgres95)

Portions Copyright (c) 1996-2005, The PostgreSQL Global Development Group

Portions Copyright (c) 1994, The Regents of the University of California

Permission to use, copy, modify, and distribute this software and its
documentation for any purpose, without fee, and without a written agreement
is hereby granted, provided that the above copyright notice and this paragraph
and the following two paragraphs appear in all copies.

IN NO EVENT SHALL THE UNIVERSITY OF CALIFORNIA BE LIABLE TO ANY PARTY FOR
DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING
LOST PROFITS, ARISING OUT OF THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION,
EVEN IF THE UNIVERSITY OF CALIFORNIA HAS BEEN ADVISED OF THE POSSIBILITY OF
SUCH DAMAGE.

THE UNIVERSITY OF CALIFORNIA SPECIFICALLY DISCLAIMS ANY WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE. THE SOFTWARE PROVIDED HEREUNDER IS
ON AN "AS IS" BASIS, AND THE UNIVERSITY OF CALIFORNIA HAS NO OBLIGATIONS
TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.

*/

/*-------------------------------------------------------------------------
 *
 * getaddrinfo.h
 *	  Support getaddrinfo() on platforms that don't have it.
 *
 * Note: we use our own routines on platforms that don't HAVE_STRUCT_ADDRINFO,
 * whether or not the library routine getaddrinfo() can be found.  This
 * policy is needed because on some platforms a manually installed libbind.a
 * may provide getaddrinfo(), yet the system headers may not provide the
 * struct definitions needed to call it.  To avoid conflict with the libbind
 * definition in such cases, we rename our routines to pg_xxx() via macros.
 *
 * This code will also work on platforms where struct addrinfo is defined
 * in the system headers but no getaddrinfo() can be located.
 *
 * Copyright (c) 2003-2007, PostgreSQL Global Development Group
 *
 *-------------------------------------------------------------------------
 */
#ifndef ADDRINFO_H
#define ADDRINFO_H


/* Various macros that ought to be in <netdb.h>, but might not be */

#ifndef EAI_FAIL
#define EAI_BADFLAGS	(-1)
#define EAI_NONAME		(-2)
#define EAI_AGAIN		(-3)
#define EAI_FAIL		(-4)
#define EAI_FAMILY		(-6)
#define EAI_SOCKTYPE	(-7)
#define EAI_SERVICE		(-8)
#define EAI_MEMORY		(-10)
#define EAI_SYSTEM		(-11)
#endif   /* !EAI_FAIL */

#ifndef AI_PASSIVE
#define AI_PASSIVE		0x0001
#endif

#ifndef AI_NUMERICHOST
/*
 * some platforms don't support AI_NUMERICHOST; define as zero if using
 * the system version of getaddrinfo...
 */
#if defined(HAVE_STRUCT_ADDRINFO) && defined(HAVE_GETADDRINFO)
#define AI_NUMERICHOST	0
#else
#define AI_NUMERICHOST	0x0004
#endif
#endif

#ifndef AI_CANONNAME
#if defined(HAVE_STRUCT_ADDRINFO) && defined(HAVE_GETADDRINFO)
#define AI_CANONNAME 0
#else
#define AI_CANONNAME 0x0008
#endif
#endif

#ifndef AI_NUMERICSERV
#if defined(HAVE_STRUCT_ADDRINFO) && defined(HAVE_GETADDRINFO)
#define AI_NUMERICSERV 0
#else
#define AI_NUMERICSERV 0x0010
#endif
#endif

#ifndef NI_NUMERICHOST
#define NI_NUMERICHOST	1
#endif

#ifndef NI_NUMERICSERV
#define NI_NUMERICSERV	2
#endif

#ifndef NI_NOFQDN
#define NI_NOFQDN	4
#endif

#ifndef NI_NAMEREQD
#define NI_NAMEREQD 	8
#endif

#ifndef NI_DGRAM
#define NI_DGRAM	16
#endif


#ifndef NI_MAXHOST
#define NI_MAXHOST	1025
#endif

#ifndef NI_MAXSERV
#define NI_MAXSERV	32
#endif

#ifndef HAVE_STRUCT_ADDRINFO
struct addrinfo
{
	int			ai_flags;
	int			ai_family;
	int			ai_socktype;
	int			ai_protocol;
	size_t		ai_addrlen;
	struct sockaddr *ai_addr;
	char	   *ai_canonname;
	struct addrinfo *ai_next;
};
#endif   /* !HAVE_STRUCT_ADDRINFO */

#ifndef HAVE_STRUCT_SOCKADDR_STORAGE
struct sockaddr_storage {
	unsigned short ss_family;
	unsigned long ss_align;
	char ss_padding[128 - sizeof (unsigned long)];
};
#endif	/* !HAVE_STRUCT_SOCKADDR_STORAGE */

#ifndef HAVE_GETADDRINFO

/* Rename private copies per comments above */
#ifdef getaddrinfo
#undef getaddrinfo
#endif
#define getaddrinfo pg_getaddrinfo

#ifdef freeaddrinfo
#undef freeaddrinfo
#endif
#define freeaddrinfo pg_freeaddrinfo

#ifdef gai_strerror
#undef gai_strerror
#endif
#define gai_strerror pg_gai_strerror

#ifdef getnameinfo
#undef getnameinfo
#endif
#define getnameinfo pg_getnameinfo

extern int getaddrinfo(const char *node, const char *service,
			const struct addrinfo * hints, struct addrinfo ** res);
extern void freeaddrinfo(struct addrinfo * res);
extern const char *gai_strerror(int errcode);
extern int getnameinfo(const struct sockaddr * sa, socklen_t salen,
			char *node, size_t nodelen,
			char *service, size_t servicelen, int flags);
#endif   /* !HAVE_GETADDRINFO */

#endif   /* ADDRINFO_H */
