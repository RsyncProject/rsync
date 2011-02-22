/*
 * Handle the mapping of uid/gid and user/group names between systems.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004-2009 Wayne Davison
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
#include "io.h"

extern int verbose;
extern int am_root;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_acls;
extern int numeric_ids;

#ifdef HAVE_GETGROUPS
# ifndef GETGROUPS_T
#  define GETGROUPS_T gid_t
# endif
#endif

struct idlist {
	struct idlist *next;
	const char *name;
	id_t id, id2;
	uint16 flags;
};

static struct idlist *uidlist;
static struct idlist *gidlist;

static struct idlist *add_to_list(struct idlist **root, id_t id, const char *name,
				  id_t id2, uint16 flags)
{
	struct idlist *node = new(struct idlist);
	if (!node)
		out_of_memory("add_to_list");
	node->next = *root;
	node->name = name;
	node->id = id;
	node->id2 = id2;
	node->flags = flags;
	*root = node;
	return node;
}

/* turn a uid into a user name */
static const char *uid_to_name(uid_t uid)
{
	struct passwd *pass = getpwuid(uid);
	if (pass)
		return strdup(pass->pw_name);
	return NULL;
}

/* turn a gid into a group name */
static const char *gid_to_name(gid_t gid)
{
	struct group *grp = getgrgid(gid);
	if (grp)
		return strdup(grp->gr_name);
	return NULL;
}

static uid_t map_uid(uid_t id, const char *name)
{
	uid_t uid;
	if (id != 0 && name_to_uid(name, &uid))
		return uid;
	return id;
}

static gid_t map_gid(gid_t id, const char *name)
{
	gid_t gid;
	if (id != 0 && name_to_gid(name, &gid))
		return gid;
	return id;
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
		gid_t mygid = MY_GID();
		if ((ngroups = getgroups(0, NULL)) < 0)
			ngroups = 0;
		gidset = new_array(GETGROUPS_T, ngroups+1);
		if (!gidset)
			out_of_memory("is_in_group");
		if (ngroups > 0)
			ngroups = getgroups(ngroups, gidset);
		/* The default gid might not be in the list on some systems. */
		for (n = 0; n < ngroups; n++) {
			if (gidset[n] == mygid)
				break;
		}
		if (n == ngroups)
			gidset[ngroups++] = mygid;
		if (verbose > 3) {
			int pos;
			char *gidbuf = new_array(char, ngroups*21+32);
			if (!gidbuf)
				out_of_memory("is_in_group");
			pos = snprintf(gidbuf, 32, "process has %d gid%s: ",
				       ngroups, ngroups == 1? "" : "s");
			for (n = 0; n < ngroups; n++) {
				pos += snprintf(gidbuf+pos, 21, " %d", (int)gidset[n]);
			}
			rprintf(FINFO, "%s\n", gidbuf);
			free(gidbuf);
		}
	}

	last_in = gid;
	for (n = 0; n < ngroups; n++) {
		if (gidset[n] == gid)
			return last_out = 1;
	}
	return last_out = 0;

#else
	static gid_t mygid = GID_NONE;
	if (mygid == GID_NONE) {
		mygid = MY_GID();
		if (verbose > 3)
			rprintf(FINFO, "process has gid %u\n", (unsigned)mygid);
	}
	return gid == mygid;
#endif
}

/* Add a uid to the list of uids.  Only called on receiving side. */
static struct idlist *recv_add_uid(uid_t id, const char *name)
{
	uid_t id2 = name ? map_uid(id, name) : id;
	struct idlist *node;

	node = add_to_list(&uidlist, id, name, id2, 0);

	if (verbose > 3) {
		rprintf(FINFO, "uid %u(%s) maps to %u\n",
			(unsigned)id, name ? name : "", (unsigned)id2);
	}

	return node;
}

/* Add a gid to the list of gids.  Only called on receiving side. */
static struct idlist *recv_add_gid(gid_t id, const char *name)
{
	gid_t id2 = name ? map_gid(id, name) : id;
	struct idlist *node;

	node = add_to_list(&gidlist, id, name, id2,
		!am_root && !is_in_group(id2) ? FLAG_SKIP_GROUP : 0);

	if (verbose > 3) {
		rprintf(FINFO, "gid %u(%s) maps to %u\n",
			(unsigned)id, name ? name : "", (unsigned)id2);
	}

	return node;
}

/* this function is a definate candidate for a faster algorithm */
uid_t match_uid(uid_t uid)
{
	static uid_t last_in, last_out;
	struct idlist *list;

	if (uid == 0)
		return 0;

	if (uid == last_in)
		return last_out;

	last_in = uid;

	for (list = uidlist; list; list = list->next) {
		if (list->id == uid)
			return last_out = list->id2;
	}

	return last_out = uid;
}

gid_t match_gid(gid_t gid, uint16 *flags_ptr)
{
	static struct idlist *last = NULL;
	struct idlist *list;

	if (last && gid == last->id)
		list = last;
	else {
		for (list = gidlist; list; list = list->next) {
			if (list->id == gid)
				break;
		}
		if (!list)
			list = recv_add_gid(gid, NULL);
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

	if (uid == 0)	/* don't map root */
		return NULL;

	for (list = uidlist; list; list = list->next) {
		if (list->id == uid)
			return NULL;
	}

	node = add_to_list(&uidlist, uid, uid_to_name(uid), 0, 0);
	return node->name;
}

/* Add a gid to the list of gids.  Only called on sending side. */
const char *add_gid(gid_t gid)
{
	struct idlist *list;
	struct idlist *node;

	if (gid == 0)	/* don't map root */
		return NULL;

	for (list = gidlist; list; list = list->next) {
		if (list->id == gid)
			return NULL;
	}

	node = add_to_list(&gidlist, gid, gid_to_name(gid), 0, 0);
	return node->name;
}

/* send a complete uid/gid mapping to the peer */
void send_id_list(int f)
{
	struct idlist *list;

	if (preserve_uid || preserve_acls) {
		int len;
		/* we send sequences of uid/byte-length/name */
		for (list = uidlist; list; list = list->next) {
			if (!list->name)
				continue;
			len = strlen(list->name);
			write_varint30(f, list->id);
			write_byte(f, len);
			write_buf(f, list->name, len);
		}

		/* terminate the uid list with a 0 uid. We explicitly exclude
		 * 0 from the list */
		write_varint30(f, 0);
	}

	if (preserve_gid || preserve_acls) {
		int len;
		for (list = gidlist; list; list = list->next) {
			if (!list->name)
				continue;
			len = strlen(list->name);
			write_varint30(f, list->id);
			write_byte(f, len);
			write_buf(f, list->name, len);
		}
		write_varint30(f, 0);
	}
}

uid_t recv_user_name(int f, uid_t uid)
{
	struct idlist *node;
	int len = read_byte(f);
	char *name = new_array(char, len+1);
	if (!name)
		out_of_memory("recv_user_name");
	read_sbuf(f, name, len);
	if (numeric_ids < 0) {
		free(name);
		name = NULL;
	}
	node = recv_add_uid(uid, name); /* node keeps name's memory */
	return node->id2;
}

gid_t recv_group_name(int f, gid_t gid, uint16 *flags_ptr)
{
	struct idlist *node;
	int len = read_byte(f);
	char *name = new_array(char, len+1);
	if (!name)
		out_of_memory("recv_group_name");
	read_sbuf(f, name, len);
	if (numeric_ids < 0) {
		free(name);
		name = NULL;
	}
	node = recv_add_gid(gid, name); /* node keeps name's memory */
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
	}

	if ((preserve_gid || preserve_acls) && numeric_ids <= 0) {
		/* read the gid list */
		while ((id = read_varint30(f)) != 0)
			recv_group_name(f, id, NULL);
	}

	/* Now convert all the uids/gids from sender values to our values. */
#ifdef SUPPORT_ACLS
	if (preserve_acls && !numeric_ids)
		match_acl_ids();
#endif
	if (am_root && preserve_uid && !numeric_ids) {
		for (i = 0; i < flist->used; i++)
			F_OWNER(flist->files[i]) = match_uid(F_OWNER(flist->files[i]));
	}
	if (preserve_gid && (!am_root || !numeric_ids)) {
		for (i = 0; i < flist->used; i++) {
			F_GROUP(flist->files[i]) = match_gid(F_GROUP(flist->files[i]),
							     &flist->files[i]->flags);
		}
	}
}
