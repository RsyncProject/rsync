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
  hosts allow/deny code for rsync

  */

#include "rsync.h"


static int match_hostname(char *host, char *tok)
{
	if (!host || !*host) return 0;
	return (fnmatch(tok, host, 0) == 0);
}


static int match_address(char *addr, char *tok)
{
	char *p;
	unsigned long a, t, mask = (unsigned long)~0;

	if (!addr || !*addr) return 0;

	if (!isdigit(tok[0])) return 0;

	p = strchr(tok,'/');
	if (p) *p = 0;

	a = inet_addr(addr);
	t = inet_addr(tok);

	if (p) {
		*p = '/';
	}

	if (t == INADDR_NONE) {
		rprintf(FERROR,"malformed address %s\n", tok);
		return 0;
	}

	a = ntohl(a);
	t = ntohl(t);

	if (p) {
		if (strchr(p+1,'.')) {
			mask = inet_addr(p+1);
			if (mask == INADDR_NONE) {
				rprintf(FERROR,"malformed mask in %s\n", tok);
				return 0;
			}
			mask = ntohl(mask);
		} else {
			int bits = atoi(p+1);
			if (bits <= 0 || bits > 32) {
				rprintf(FERROR,"malformed mask in %s\n", tok);
				return 0;
			}
			mask &= (mask << (32-bits));
		}
	}

	return ((a&mask) == (t&mask));
}

static int access_match(char *list, char *addr, char *host)
{
	char *tok;
	char *list2 = strdup(list);

	if (!list2) out_of_memory("access_match");

	strlower(list2);
	if (host) strlower(host);

	for (tok=strtok(list2," ,\t"); tok; tok=strtok(NULL," ,\t")) {
		if (match_hostname(host, tok) || match_address(addr, tok)) {
			free(list2);
			return 1;
		}
	}

	free(list2);
	return 0;
}

int allow_access(char *addr, char *host, char *allow_list, char *deny_list)
{
	/* if theres no deny list and no allow list then allow access */
	if ((!deny_list || !*deny_list) && (!allow_list || !*allow_list))
		return 1;

	/* if there is an allow list but no deny list then allow only hosts
	   on the allow list */
	if (!deny_list || !*deny_list)
		return(access_match(allow_list, addr, host));

	/* if theres a deny list but no allow list then allow
	   all hosts not on the deny list */
	if (!allow_list || !*allow_list)
		return(!access_match(deny_list,addr,host));

	/* if there are both type of list then allow all hosts on the
           allow list */
	if (access_match(allow_list,addr,host))
		return 1;

	/* if there are both type of list and it's not on the allow then
	   allow it if its not on the deny */
	if (access_match(deny_list,addr,host))
		return 0;

	return 1;
}
