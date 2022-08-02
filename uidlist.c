/*
 * Handle the mapping of uid/gid and user/group names between systems.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2022 Wayne Davison
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

/* If the source username/group does not exist on the target then use
 * the numeric IDs.  Never do any mapping for uid=0 or gid=0 as these
 * are special. */

#include "rsync.h"
#include "ifuncs.h"
#include "itypes.h"
#include "io.h"

extern int am_root;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_acls;
extern int numeric_ids;
extern int xmit_id0_names;
extern pid_t namecvt_pid;
extern gid_t our_gid;
extern char *usermap;
extern char *groupmap;

#ifdef HAVE_GETGROUPS
# ifndef GETGROUPS_T
#  define GETGROUPS_T gid_t
# endif
#endif

#define NFLAGS_WILD_NAME_MATCH (1<<0)
#define NFLAGS_NAME_MATCH (1<<1)

union name_or_id {
	const char *name;
	id_t max_id;
};

struct idlist {
	struct idlist *next;
	union name_or_id u;
	id_t id, id2;
	uint16 flags;
};

static struct idlist *uidlist, *uidmap;
static struct idlist *gidlist, *gidmap;

static inline int id_eq_uid(id_t id, uid_t uid)
{
	return id == (id_t)uid;
}

static inline int id_eq_gid(id_t id, gid_t gid)
{
	return id == (id_t)gid;
}

static id_t id_parse(const char *num_str)
{
	id_t tmp, num = 0;
	const char *cp = num_str;

	while (*cp) {
		if (!isDigit(cp)) {
		  invalid_num:
			rprintf(FERROR, "Invalid ID number: %s\n", num_str);
			exit_cleanup(RERR_SYNTAX);
		}
		tmp = num * 10 + *cp++ - '0';
		if (tmp < num)
			goto invalid_num;
		num = tmp;
	}

	return num;
}

static struct idlist *add_to_list(struct idlist **root, id_t id, union name_or_id noiu,
				  id_t id2, uint16 flags)
{
	struct idlist *node = new(struct idlist);
	node->next = *root;
	node->u = noiu;
	node->id = id;
	node->id2 = id2;
	node->flags = flags;
	*root = node;
	return node;
}

/* turn a uid into a user name */
const char *uid_to_user(uid_t uid)
{
	const char *name = NULL;

	if (namecvt_pid) {
		id_t id = uid;
		namecvt_call("uid", &name, &id);
	} else {
		struct passwd *pass = getpwuid(uid);
		if (pass)
			name = strdup(pass->pw_name);
	}

	return name;
}

/* turn a gid into a group name */
const char *gid_to_group(gid_t gid)
{
	const char *name = NULL;

	if (namecvt_pid) {
		id_t id = gid;
		namecvt_call("gid", &name, &id);
	} else {
		struct group *grp = getgrgid(gid);
		if (grp)
			name = strdup(grp->gr_name);
	}

	return name;
}

/* Parse a user name or (optionally) a number into a uid */
int user_to_uid(const char *name, uid_t *uid_p, BOOL num_ok)
{
	if (!name || !*name)
		return 0;

	if (num_ok && name[strspn(name, "0123456789")] == '\0') {
		*uid_p = id_parse(name);
		return 1;
	}

	if (namecvt_pid) {
		id_t id;
		if (!namecvt_call("usr", &name, &id))
			return 0;
		*uid_p = id;
	} else {
		struct passwd *pass = getpwnam(name);
		if (!pass)
			return 0;
		*uid_p = pass->pw_uid;
	}

	return 1;
}

/* Parse a group name or (optionally) a number into a gid */
int group_to_gid(const char *name, gid_t *gid_p, BOOL num_ok)
{
	if (!name || !*name)
		return 0;

	if (num_ok && name[strspn(name, "0123456789")] == '\0') {
		*gid_p = id_parse(name);
		return 1;
	}

	if (namecvt_pid) {
		id_t id;
		if (!namecvt_call("grp", &name, &id))
			return 0;
		*gid_p = id;
	} else {
		struct group *grp = getgrnam(name);
		if (!grp)
			return 0;
		*gid_p = grp->gr_gid;
	}

	return 1;
}

static int is_in_group(gid_t gid)
{
#ifdef HAVE_GETGROUPS
	static gid_t last_in;
	static int ngroups = -2, last_out = -1;
	static GETGROUPS_T *gidset;
	int n;

	if (gid == last_in && last_out >= 0)
		return last_out;
	if (ngroups < -1) {
		if ((ngroups = getgroups(0, NULL)) < 0)
			ngroups = 0;
		gidset = new_array(GETGROUPS_T, ngroups+1);
		if (ngroups > 0)
			ngroups = getgroups(ngroups, gidset);
		/* The default gid might not be in the list on some systems. */
		for (n = 0; n < ngroups; n++) {
			if ((gid_t)gidset[n] == our_gid)
				break;
		}
		if (n == ngroups)
			gidset[ngroups++] = our_gid;
		if (DEBUG_GTE(OWN, 2)) {
			int pos;
			char *gidbuf = new_array(char, ngroups*21+32);
			pos = snprintf(gidbuf, 32, "process has %d gid%s: ", ngroups, ngroups == 1? "" : "s");
			for (n = 0; n < ngroups; n++) {
				pos += snprintf(gidbuf+pos, 21, " %d", (int)gidset[n]);
			}
			rprintf(FINFO, "%s\n", gidbuf);
			free(gidbuf);
		}
	}

	last_in = gid;
	for (n = 0; n < ngroups; n++) {
		if ((gid_t)gidset[n] == gid)
			return last_out = 1;
	}
	return last_out = 0;

#else
	return gid == our_gid;
#endif
}

/* Add a uid/gid to its list of ids.  Only called on receiving side. */
static struct idlist *recv_add_id(struct idlist **idlist_ptr, struct idlist *idmap,
				  id_t id, const char *name)
{
	struct idlist *node;
	union name_or_id noiu;
	int flag;
	id_t id2;

	noiu.name = name; /* ensure that add_to_list() gets the raw value. */
	if (!name)
		name = "";

	for (node = idmap; node; node = node->next) {
		if (node->flags & NFLAGS_WILD_NAME_MATCH) {
			if (!wildmatch(node->u.name, name))
				continue;
		} else if (node->flags & NFLAGS_NAME_MATCH) {
			if (strcmp(node->u.name, name) != 0)
				continue;
		} else if (node->u.max_id) {
			if (id < node->id || id > node->u.max_id)
				continue;
		} else {
			if (node->id != id)
				continue;
		}
		break;
	}
	if (node)
		id2 = node->id2;
	else if (*name && id) {
		if (idlist_ptr == &uidlist) {
			uid_t uid;
			id2 = user_to_uid(name, &uid, False) ? (id_t)uid : id;
		} else {
			gid_t gid;
			id2 = group_to_gid(name, &gid, False) ? (id_t)gid : id;
		}
	} else
		id2 = id;

	flag = idlist_ptr == &gidlist && !am_root && !is_in_group(id2) ? FLAG_SKIP_GROUP : 0;
	node = add_to_list(idlist_ptr, id, noiu, id2, flag);

	if (DEBUG_GTE(OWN, 2)) {
		rprintf(FINFO, "%sid %u(%s) maps to %u\n",
			idlist_ptr == &uidlist ? "u" : "g",
			(unsigned)id, name, (unsigned)id2);
	}

	return node;
}

/* this function is a definite candidate for a faster algorithm */
uid_t match_uid(uid_t uid)
{
	static struct idlist *last = NULL;
	struct idlist *list;

	if (last && id_eq_uid(last->id, uid))
		return last->id2;

	for (list = uidlist; list; list = list->next) {
		if (id_eq_uid(list->id, uid))
			break;
	}

	if (!list)
		list = recv_add_id(&uidlist, uidmap, uid, NULL);
	last = list;

	return list->id2;
}

gid_t match_gid(gid_t gid, uint16 *flags_ptr)
{
	static struct idlist *last = NULL;
	struct idlist *list;

	if (last && id_eq_gid(last->id, gid))
		list = last;
	else {
		for (list = gidlist; list; list = list->next) {
			if (id_eq_gid(list->id, gid))
				break;
		}
		if (!list)
			list = recv_add_id(&gidlist, gidmap, gid, NULL);
		last = list;
	}

	if (flags_ptr && list->flags & FLAG_SKIP_GROUP)
		*flags_ptr |= FLAG_SKIP_GROUP;
	return list->id2;
}

/* Add a uid to the list of uids.  Only called on sending side. */
const char *add_uid(uid_t uid)
{
	struct idlist *list;
	struct idlist *node;
	union name_or_id noiu;

	for (list = uidlist; list; list = list->next) {
		if (id_eq_uid(list->id, uid))
			return NULL;
	}

	noiu.name = uid_to_user(uid);
	node = add_to_list(&uidlist, uid, noiu, 0, 0);
	return node->u.name;
}

/* Add a gid to the list of gids.  Only called on sending side. */
const char *add_gid(gid_t gid)
{
	struct idlist *list;
	struct idlist *node;
	union name_or_id noiu;

	for (list = gidlist; list; list = list->next) {
		if (id_eq_gid(list->id, gid))
			return NULL;
	}

	noiu.name = gid_to_group(gid);
	node = add_to_list(&gidlist, gid, noiu, 0, 0);
	return node->u.name;
}

static void send_one_name(int f, id_t id, const char *name)
{
	int len;

	if (!name)
		name = "";
	if ((len = strlen(name)) > 255) /* Impossible? */
		len = 255;

	write_varint30(f, id);
	write_byte(f, len);
	if (len)
		write_buf(f, name, len);
}

static void send_one_list(int f, struct idlist *idlist, int usernames)
{
	struct idlist *list;

	/* we send sequences of id/byte-len/name */
	for (list = idlist; list; list = list->next) {
		if (list->id && list->u.name)
			send_one_name(f, list->id, list->u.name);
	}

	/* Terminate the uid list with 0 (which was excluded above).
	 * A modern rsync also sends the name of id 0. */
	if (xmit_id0_names)
		send_one_name(f, 0, usernames ? uid_to_user(0) : gid_to_group(0));
	else
		write_varint30(f, 0);
}

/* send a complete uid/gid mapping to the peer */
void send_id_lists(int f)
{
	if (preserve_uid || preserve_acls)
		send_one_list(f, uidlist, 1);

	if (preserve_gid || preserve_acls)
		send_one_list(f, gidlist, 0);
}

uid_t recv_user_name(int f, uid_t uid)
{
	struct idlist *node;
	int len = read_byte(f);
	char *name;

	if (len) {
		name = new_array(char, len+1);
		read_sbuf(f, name, len);
		if (numeric_ids < 0) {
			free(name);
			name = NULL;
		}
	} else
		name = NULL;

	node = recv_add_id(&uidlist, uidmap, uid, name); /* node keeps name's memory */
	return node->id2;
}

gid_t recv_group_name(int f, gid_t gid, uint16 *flags_ptr)
{
	struct idlist *node;
	int len = read_byte(f);
	char *name;

	if (len) {
		name = new_array(char, len+1);
		read_sbuf(f, name, len);
		if (numeric_ids < 0) {
			free(name);
			name = NULL;
		}
	} else
		name = NULL;

	node = recv_add_id(&gidlist, gidmap, gid, name); /* node keeps name's memory */
	if (flags_ptr && node->flags & FLAG_SKIP_GROUP)
		*flags_ptr |= FLAG_SKIP_GROUP;
	return node->id2;
}

/* recv a complete uid/gid mapping from the peer and map the uid/gid
 * in the file list to local names */
void recv_id_list(int f, struct file_list *flist)
{
	id_t id;
	int i;

	if ((preserve_uid || preserve_acls) && numeric_ids <= 0) {
		/* read the uid list */
		while ((id = read_varint30(f)) != 0)
			recv_user_name(f, id);
		if (xmit_id0_names)
			recv_user_name(f, 0);
	}

	if ((preserve_gid || preserve_acls) && numeric_ids <= 0) {
		/* read the gid list */
		while ((id = read_varint30(f)) != 0)
			recv_group_name(f, id, NULL);
		if (xmit_id0_names)
			recv_group_name(f, 0, NULL);
	}

	/* Now convert all the uids/gids from sender values to our values. */
#ifdef SUPPORT_ACLS
	if (preserve_acls && (!numeric_ids || usermap || groupmap))
		match_acl_ids();
#endif
	if (am_root && preserve_uid && (!numeric_ids || usermap)) {
		for (i = 0; i < flist->used; i++)
			F_OWNER(flist->files[i]) = match_uid(F_OWNER(flist->files[i]));
	}
	if (preserve_gid && (!am_root || !numeric_ids || groupmap)) {
		for (i = 0; i < flist->used; i++) {
			F_GROUP(flist->files[i]) = match_gid(F_GROUP(flist->files[i]), &flist->files[i]->flags);
		}
	}
}

void parse_name_map(char *map, BOOL usernames)
{
	struct idlist **idmap_ptr = usernames ? &uidmap : &gidmap;
	struct idlist **idlist_ptr = usernames ? &uidlist : &gidlist;
	char *colon, *cp = map + strlen(map);
	union name_or_id noiu;
	id_t id1;
	uint16 flags;

	/* Parse the list in reverse, so the order in the struct is right. */
	while (1) {
		while (cp > map && cp[-1] != ',') cp--;
		if (!(colon = strchr(cp, ':'))) {
			rprintf(FERROR, "No colon found in --%smap: %s\n",
				usernames ? "user" : "group", cp);
			exit_cleanup(RERR_SYNTAX);
		}
		if (!colon[1]) {
			rprintf(FERROR, "No name found after colon --%smap: %s\n",
				usernames ? "user" : "group", cp);
			exit_cleanup(RERR_SYNTAX);
		}
		*colon = '\0';

		if (isDigit(cp)) {
			char *dash = strchr(cp, '-');
			if (strspn(cp, "0123456789-") != (size_t)(colon - cp)
			 || (dash && (!dash[1] || strchr(dash+1, '-')))) {
				rprintf(FERROR, "Invalid number in --%smap: %s\n",
					usernames ? "user" : "group", cp);
				exit_cleanup(RERR_SYNTAX);
			}
			if (dash) {
				*dash = '\0';
				noiu.max_id = id_parse(dash+1);
			} else
				noiu.max_id = 0;
			flags = 0;
			id1 = id_parse(cp);
			if (dash)
				*dash = '-';
		} else if (strpbrk(cp, "*[?")) {
			flags = NFLAGS_WILD_NAME_MATCH;
			noiu.name = cp;
			id1 = 0;
		} else {
			flags = NFLAGS_NAME_MATCH;
			noiu.name = cp;
			id1 = 0;
		}

		if (usernames) {
			uid_t uid;
			if (user_to_uid(colon+1, &uid, True))
				add_to_list(idmap_ptr, id1, noiu, uid, flags);
			else {
				rprintf(FERROR, "Unknown --usermap name on receiver: %s\n", colon+1);
			}
		} else {
			gid_t gid;
			if (group_to_gid(colon+1, &gid, True))
				add_to_list(idmap_ptr, id1, noiu, gid, flags);
			else {
				rprintf(FERROR, "Unknown --groupmap name on receiver: %s\n", colon+1);
			}
		}

		if (cp == map)
			break;

		*--cp = '\0'; /* replace comma */
	}

	/* If the sender isn't going to xmit the id0 name, we assume it's "root". */
	if (!xmit_id0_names)
		recv_add_id(idlist_ptr, *idmap_ptr, 0, numeric_ids ? NULL : "root");
}

#ifdef HAVE_GETGROUPLIST
const char *getallgroups(uid_t uid, item_list *gid_list)
{
	struct passwd *pw;
	gid_t *gid_array;
	int size;

	if ((pw = getpwuid(uid)) == NULL)
		return "getpwuid failed";

	gid_list->count = 0; /* We're overwriting any items in the list */
	(void)EXPAND_ITEM_LIST(gid_list, gid_t, 32);
	size = gid_list->malloced;

	/* Get all the process's groups, with the pw_gid group first. */
	if (getgrouplist(pw->pw_name, pw->pw_gid, gid_list->items, &size) < 0) {
		if (size > (int)gid_list->malloced) {
			gid_list->count = gid_list->malloced;
			(void)EXPAND_ITEM_LIST(gid_list, gid_t, size);
			if (getgrouplist(pw->pw_name, pw->pw_gid, gid_list->items, &size) < 0)
				size = -1;
		} else
			size = -1;
		if (size < 0)
			return "getgrouplist failed";
	}
	gid_list->count = size;
	gid_array = gid_list->items;

	/* Paranoia: is the default group not first in the list? */
	if (gid_array[0] != pw->pw_gid) {
		int j;
		for (j = 1; j < size; j++) {
			if (gid_array[j] == pw->pw_gid)
				break;
		}
		if (j == size) { /* The default group wasn't found! */
			(void)EXPAND_ITEM_LIST(gid_list, gid_t, size+1);
			gid_array = gid_list->items;
		}
		gid_array[j] = gid_array[0];
		gid_array[0] = pw->pw_gid;
	}

	return NULL;
}
#endif
