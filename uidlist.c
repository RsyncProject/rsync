/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
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

/* handle the mapping of uid/gid and user/group names between systems.
   If the source username/group does not exist on the target then use
   the numeric ids. Never do any mapping for uid=0 or gid=0 as these
   are special.
*/

#include "rsync.h"

extern int preserve_uid;
extern int preserve_gid;
extern int numeric_ids;
extern int am_root;

struct idlist {
	struct idlist *next;
	int id, id2;
	char *name;
};

static struct idlist *uidlist;
static struct idlist *gidlist;

static struct idlist *add_list(int id, char *name)
{
	struct idlist *list = new(struct idlist);
	if (!list) out_of_memory("add_list");
	list->next = NULL;
	list->name = strdup(name);
	if (!list->name) out_of_memory("add_list");
	list->id = (int)id;
	return list;
}



/* turn a uid into a user name */
static char *uid_to_name(uid_t uid)
{
	struct passwd *pass = getpwuid(uid);
	if (pass) return(pass->pw_name);
	return NULL;
}

/* turn a gid into a group name */
static char *gid_to_name(gid_t gid)
{
	struct group *grp = getgrgid(gid);
	if (grp) return(grp->gr_name);
	return NULL;
}

static int map_uid(int id, char *name)
{
	uid_t uid;
	if (name_to_uid(name, &uid) && uid != 0)
		return uid;
	return id;
}

static int map_gid(int id, char *name)
{
	gid_t gid;
	if (name_to_gid(name, &gid) && gid != 0)
		return gid;
	return id;
}

/* this function is a definate candidate for a faster algorithm */
static uid_t match_uid(uid_t uid)
{
	static uid_t last_in, last_out;
	struct idlist *list = uidlist;

	if (uid == last_in) return last_out;

	last_in = uid;

	while (list) {
		if (list->id == (int)uid) {
			last_out = (uid_t)list->id2;
			return last_out;
		}
		list = list->next;
	}
	
	last_out = uid;
	return last_out;
}

static gid_t match_gid(gid_t gid)
{
	static gid_t last_in, last_out;
	struct idlist *list = gidlist;

	if (gid == last_in) return last_out;

	last_in = gid;

	while (list) {
		if (list->id == (int)gid) {
			last_out = (gid_t)list->id2;
			return last_out;
		}
		list = list->next;
	}
	
	if (am_root)
		last_out = gid;
	else
		last_out = (gid_t) -1;
	return last_out;
}

/* add a uid to the list of uids */
void add_uid(uid_t uid)
{
	struct idlist *list = uidlist;
	char *name;

	if (numeric_ids) return;

	/* don't map root */
	if (uid==0) return;

	if (!list) {
		if (!(name = uid_to_name(uid))) return;
		uidlist = add_list((int)uid, name);
		return;
	}

	while (list->next) {
		if (list->id == (int)uid) return;
		list = list->next;
	}

	if (list->id == (int)uid) return;

	if (!(name = uid_to_name(uid))) return;

	list->next = add_list((int)uid, name);
}

/* add a gid to the list of gids */
void add_gid(gid_t gid)
{
	struct idlist *list = gidlist;
	char *name;

	if (numeric_ids) return;

	/* don't map root */
	if (gid==0) return;

	if (!list) {
		if (!(name = gid_to_name(gid))) return;
		gidlist = add_list((int)gid, name);
		return;
	}

	while (list->next) {
		if (list->id == (int)gid) return;
		list = list->next;
	}

	if (list->id == (int)gid) return;

	if (!(name = gid_to_name(gid))) return;

	list->next = add_list((int)gid, name);
}


/* send a complete uid/gid mapping to the peer */
void send_uid_list(int f)
{
	struct idlist *list;

	if (numeric_ids) return;

	if (preserve_uid) {
		/* we send sequences of uid/byte-length/name */
		list = uidlist;
		while (list) {
			int len = strlen(list->name);
			write_int(f, list->id);
			write_byte(f, len);
			write_buf(f, list->name, len);
			list = list->next;
		}

		/* terminate the uid list with a 0 uid. We explicitly exclude
		   0 from the list */
		write_int(f, 0);
	}

	if (preserve_gid) {
		list = gidlist;
		while (list) {
			int len = strlen(list->name);
			write_int(f, list->id);
			write_byte(f, len);
			write_buf(f, list->name, len);
			list = list->next;
		}
		write_int(f, 0);
	}
}

/* recv a complete uid/gid mapping from the peer and map the uid/gid
   in the file list to local names */
void recv_uid_list(int f, struct file_list *flist)
{
	int id, i;
	char *name;
	struct idlist *list;

	if (numeric_ids) return;

	if (preserve_uid) {
		/* read the uid list */
		list = uidlist;
		id = read_int(f);
		while (id != 0) {
			int len = read_byte(f);
			name = new_array(char, len+1);
			if (!name) out_of_memory("recv_uid_list");
			read_sbuf(f, name, len);
			if (!list) {
				uidlist = add_list(id, name);
				list = uidlist;
			} else {
				list->next = add_list(id, name);
				list = list->next;
			}
			list->id2 = map_uid(id, name);
			free(name);
			id = read_int(f);
		}
	}


	if (preserve_gid) {
		/* and the gid list */
		list = gidlist;
		id = read_int(f);
		while (id != 0) {
			int len = read_byte(f);
			name = new_array(char, len+1);
			if (!name) out_of_memory("recv_uid_list");
			read_sbuf(f, name, len);
			if (!list) {
				gidlist = add_list(id, name);
				list = gidlist;
			} else {
				list->next = add_list(id, name);
				list = list->next;
			}
			list->id2 = map_gid(id, name);
			free(name);
			id = read_int(f);
		}
	}

	if (!(am_root && preserve_uid) && !preserve_gid) return;

	/* now convert the uid/gid of all files in the list to the mapped
	   uid/gid */
	for (i=0;i<flist->count;i++) {
		if (am_root && preserve_uid && flist->files[i]->uid != 0) {
			flist->files[i]->uid = match_uid(flist->files[i]->uid);
		}
		if (preserve_gid && flist->files[i]->gid != 0) {
			flist->files[i]->gid = match_gid(flist->files[i]->gid);
		}
	}
}
