/*
 * Handle the mapping of uid/gid and user/group names between systems.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2004, 2005, 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

/* If the source username/group does not exist on the target then use
 * the numeric IDs.  Never do any mapping for uid=0 or gid=0 as these
 * are special. */

#include "rsync.h"

#ifdef HAVE_GETGROUPS
# ifndef GETGROUPS_T
#  define GETGROUPS_T gid_t
# endif
#endif

extern int verbose;
extern int am_root;
extern int preserve_uid;
extern int preserve_gid;
extern int numeric_ids;

struct idlist {
	struct idlist *next;
	int id, id2;
	char *name;
};

static struct idlist *uidlist;
static struct idlist *gidlist;

static struct idlist *add_to_list(struct idlist **root, int id, char *name,
				  int id2)
{
	struct idlist *node = new(struct idlist);
	if (!node)
		out_of_memory("add_to_list");
	node->next = *root;
	node->name = name;
	node->id = id;
	node->id2 = id2;
	*root = node;
	return node;
}

/* turn a uid into a user name */
static char *uid_to_name(uid_t uid)
{
	struct passwd *pass = getpwuid(uid);
	if (pass)
		return strdup(pass->pw_name);
	return NULL;
}

/* turn a gid into a group name */
static char *gid_to_name(gid_t gid)
{
	struct group *grp = getgrgid(gid);
	if (grp)
		return strdup(grp->gr_name);
	return NULL;
}

static uid_t map_uid(uid_t id, char *name)
{
	uid_t uid;
	if (id != 0 && name_to_uid(name, &uid))
		return uid;
	return id;
}

static gid_t map_gid(gid_t id, char *name)
{
	gid_t gid;
	if (id != 0 && name_to_gid(name, &gid))
		return gid;
	return id;
}

static int is_in_group(gid_t gid)
{
#ifdef HAVE_GETGROUPS
	static gid_t last_in = GID_NONE, last_out;
	static int ngroups = -2;
	static GETGROUPS_T *gidset;
	int n;

	if (gid == last_in)
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
			rprintf(FINFO, "process has gid %d\n", (int)mygid);
	}
	return gid == mygid;
#endif
}

/* Add a uid to the list of uids.  Only called on receiving side. */
static uid_t recv_add_uid(uid_t id, char *name)
{
	uid_t id2 = name ? map_uid(id, name) : id;
	struct idlist *node;

	node = add_to_list(&uidlist, (int)id, name, (int)id2);

	if (verbose > 3) {
		rprintf(FINFO, "uid %d(%s) maps to %d\n",
			(int)id, name ? name : "", (int)id2);
	}

	return id2;
}

/* Add a gid to the list of gids.  Only called on receiving side. */
static gid_t recv_add_gid(gid_t id, char *name)
{
	gid_t id2 = name ? map_gid(id, name) : id;
	struct idlist *node;

	if (!am_root && !is_in_group(id2))
		id2 = GID_NONE;
	node = add_to_list(&gidlist, (int)id, name, (int)id2);

	if (verbose > 3) {
		rprintf(FINFO, "gid %d(%s) maps to %d\n",
			(int)id, name ? name : "", (int)id2);
	}

	return id2;
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
		if (list->id == (int)uid)
			return last_out = (uid_t)list->id2;
	}

	return last_out = uid;
}

gid_t match_gid(gid_t gid)
{
	static gid_t last_in = GID_NONE, last_out = GID_NONE;
	struct idlist *list;

	if (gid == GID_NONE)
		return GID_NONE;

	if (gid == last_in)
		return last_out;

	last_in = gid;

	for (list = gidlist; list; list = list->next) {
		if (list->id == (int)gid)
			return last_out = (gid_t)list->id2;
	}

	return last_out = recv_add_gid(gid, NULL);
}

/* Add a uid to the list of uids.  Only called on sending side. */
char *add_uid(uid_t uid)
{
	struct idlist *list;
	struct idlist *node;

	if (uid == 0)	/* don't map root */
		return NULL;

	for (list = uidlist; list; list = list->next) {
		if (list->id == (int)uid)
			return NULL;
	}

	node = add_to_list(&uidlist, (int)uid, uid_to_name(uid), 0);
	return node->name;
}

/* Add a gid to the list of gids.  Only called on sending side. */
char *add_gid(gid_t gid)
{
	struct idlist *list;
	struct idlist *node;

	if (gid == 0)	/* don't map root */
		return NULL;

	for (list = gidlist; list; list = list->next) {
		if (list->id == (int)gid)
			return NULL;
	}

	node = add_to_list(&gidlist, (int)gid, gid_to_name(gid), 0);
	return node->name;
}

/* send a complete uid/gid mapping to the peer */
void send_uid_list(int f)
{
	struct idlist *list;

	if (preserve_uid) {
		int len;
		/* we send sequences of uid/byte-length/name */
		for (list = uidlist; list; list = list->next) {
			if (!list->name)
				continue;
			len = strlen(list->name);
			write_int(f, list->id);
			write_byte(f, len);
			write_buf(f, list->name, len);
		}

		/* terminate the uid list with a 0 uid. We explicitly exclude
		 * 0 from the list */
		write_int(f, 0);
	}

	if (preserve_gid) {
		int len;
		for (list = gidlist; list; list = list->next) {
			if (!list->name)
				continue;
			len = strlen(list->name);
			write_int(f, list->id);
			write_byte(f, len);
			write_buf(f, list->name, len);
		}
		write_int(f, 0);
	}
}

uid_t recv_user_name(int f, uid_t uid)
{
	int len = read_byte(f);
	char *name = new_array(char, len+1);
	if (!name)
		out_of_memory("recv_user_name");
	read_sbuf(f, name, len);
	return recv_add_uid(uid, name); /* node keeps name's memory */
}

gid_t recv_group_name(int f, gid_t gid)
{
	int len = read_byte(f);
	char *name = new_array(char, len+1);
	if (!name)
		out_of_memory("recv_group_name");
	read_sbuf(f, name, len);
	return recv_add_gid(gid, name); /* node keeps name's memory */
}

/* recv a complete uid/gid mapping from the peer and map the uid/gid
 * in the file list to local names */
void recv_uid_list(int f, struct file_list *flist)
{
	int id, i;

	if (preserve_uid && !numeric_ids) {
		/* read the uid list */
		while ((id = read_int(f)) != 0)
			recv_user_name(f, (uid_t)id);
	}

	if (preserve_gid && !numeric_ids) {
		/* read the gid list */
		while ((id = read_int(f)) != 0)
			recv_group_name(f, (gid_t)id);
	}

	/* Now convert all the uids/gids from sender values to our values. */
	if (am_root && preserve_uid && !numeric_ids) {
		for (i = 0; i < flist->count; i++)
			F_UID(flist->files[i]) = match_uid(F_UID(flist->files[i]));
	}
	if (preserve_gid && (!am_root || !numeric_ids)) {
		for (i = 0; i < flist->count; i++)
			F_GID(flist->files[i]) = match_gid(F_GID(flist->files[i]));
	}
}
