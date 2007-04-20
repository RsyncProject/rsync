/*
 * Handle passing Access Control Lists between systems.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
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

#include "rsync.h"
#include "lib/sysacls.h"

#ifdef SUPPORT_ACLS

extern int dry_run;
extern int am_root;
extern int read_only;
extern int list_only;
extern int orig_umask;
extern int protocol_version;
extern int numeric_ids;
extern int inc_recurse;

#define XMIT_USER_OBJ (1<<0)
#define XMIT_USER_LIST (1<<1)
#define XMIT_GROUP_OBJ (1<<2)
#define XMIT_GROUP_LIST (1<<3)
#define XMIT_MASK_OBJ (1<<4)
#define XMIT_OTHER_OBJ (1<<5)

/* === ACL structures === */

typedef struct {
	id_t id;
	uchar access;
} id_access;

typedef struct {
	id_access *idas;
	int count;
} ida_entries;

typedef struct {
	char *name;
	uchar len;
} idname;

#define NO_ENTRY ((uchar)0x80)
typedef struct rsync_acl {
	ida_entries users;
	ida_entries groups;
	/* These will be NO_ENTRY if there's no such entry. */
	uchar user_obj;
	uchar group_obj;
	uchar mask_obj;
	uchar other_obj;
} rsync_acl;

typedef struct {
	rsync_acl racl;
	SMB_ACL_T sacl;
} acl_duo;

static const rsync_acl empty_rsync_acl = {
	{NULL, 0}, {NULL, 0}, NO_ENTRY, NO_ENTRY, NO_ENTRY, NO_ENTRY
};

static item_list access_acl_list = EMPTY_ITEM_LIST;
static item_list default_acl_list = EMPTY_ITEM_LIST;

/* === Calculations on ACL types === */

static const char *str_acl_type(SMB_ACL_TYPE_T type)
{
	return type == SMB_ACL_TYPE_ACCESS ? "SMB_ACL_TYPE_ACCESS"
	     : type == SMB_ACL_TYPE_DEFAULT ? "SMB_ACL_TYPE_DEFAULT"
	     : "unknown SMB_ACL_TYPE_T";
}

static int calc_sacl_entries(const rsync_acl *racl)
{
	/* A System ACL always gets user/group/other permission entries. */
	return racl->users.count + racl->groups.count
#ifdef ACLS_NEED_MASK
	     + 4;
#else
	     + (racl->mask_obj != NO_ENTRY) + 3;
#endif
}

/* Extracts and returns the permission bits from the ACL.  This cannot be
 * called on an rsync_acl that has NO_ENTRY in any spot but the mask. */
static int rsync_acl_get_perms(const rsync_acl *racl)
{
	return (racl->user_obj << 6)
	     + ((racl->mask_obj != NO_ENTRY ? racl->mask_obj : racl->group_obj) << 3)
	     + racl->other_obj;
}

/* Removes the permission-bit entries from the ACL because these
 * can be reconstructed from the file's mode. */
static void rsync_acl_strip_perms(rsync_acl *racl)
{
	racl->user_obj = NO_ENTRY;
	if (racl->mask_obj == NO_ENTRY)
		racl->group_obj = NO_ENTRY;
	else {
		if (racl->group_obj == racl->mask_obj)
			racl->group_obj = NO_ENTRY;
		racl->mask_obj = NO_ENTRY;
	}
	racl->other_obj = NO_ENTRY;
}

/* Given an empty rsync_acl, fake up the permission bits. */
static void rsync_acl_fake_perms(rsync_acl *racl, mode_t mode)
{
	racl->user_obj = (mode >> 6) & 7;
	racl->group_obj = (mode >> 3) & 7;
	racl->other_obj = mode & 7;
}

/* === Rsync ACL functions === */

static rsync_acl *create_racl(void)
{
	rsync_acl *racl = new(rsync_acl);

	if (!racl)
		out_of_memory("create_racl");
	*racl = empty_rsync_acl;

	return racl;
}

static BOOL ida_entries_equal(const ida_entries *ial1, const ida_entries *ial2)
{
	id_access *ida1, *ida2;
	int count = ial1->count;
	if (count != ial2->count)
		return False;
	ida1 = ial1->idas;
	ida2 = ial2->idas;
	for (; count--; ida1++, ida2++) {
		if (ida1->access != ida2->access || ida1->id != ida2->id)
			return False;
	}
	return True;
}

static BOOL rsync_acl_equal(const rsync_acl *racl1, const rsync_acl *racl2)
{
	return racl1->user_obj == racl2->user_obj
	    && racl1->group_obj == racl2->group_obj
	    && racl1->mask_obj == racl2->mask_obj
	    && racl1->other_obj == racl2->other_obj
	    && ida_entries_equal(&racl1->users, &racl2->users)
	    && ida_entries_equal(&racl1->groups, &racl2->groups);
}

/* Are the extended (non-permission-bit) entries equal?  If so, the rest of
 * the ACL will be handled by the normal mode-preservation code.  This is
 * only meaningful for access ACLs!  Note: the 1st arg is a fully-populated
 * rsync_acl, but the 2nd parameter can be a condensed rsync_acl, which means
 * that it might have several of its permission objects set to NO_ENTRY. */
static BOOL rsync_acl_equal_enough(const rsync_acl *racl1,
				   const rsync_acl *racl2, mode_t m)
{
	if ((racl1->mask_obj ^ racl2->mask_obj) & NO_ENTRY)
		return False; /* One has a mask and the other doesn't */

	/* When there's a mask, the group_obj becomes an extended entry. */
	if (racl1->mask_obj != NO_ENTRY) {
		/* A condensed rsync_acl with a mask can only have no
		 * group_obj when it was identical to the mask.  This
		 * means that it was also identical to the group attrs
		 * from the mode. */
		if (racl2->group_obj == NO_ENTRY) {
			if (racl1->group_obj != ((m >> 3) & 7))
				return False;
		} else if (racl1->group_obj != racl2->group_obj)
			return False;
	}
	return ida_entries_equal(&racl1->users, &racl2->users)
	    && ida_entries_equal(&racl1->groups, &racl2->groups);
}

static void rsync_acl_free(rsync_acl *racl)
{
	if (racl->users.idas)
		free(racl->users.idas);
	if (racl->groups.idas)
		free(racl->groups.idas);
	*racl = empty_rsync_acl;
}

void free_acl(statx *sxp)
{
	if (sxp->acc_acl) {
		rsync_acl_free(sxp->acc_acl);
		free(sxp->acc_acl);
		sxp->acc_acl = NULL;
	}
	if (sxp->def_acl) {
		rsync_acl_free(sxp->def_acl);
		free(sxp->def_acl);
		sxp->def_acl = NULL;
	}
}

static int id_access_sorter(const void *r1, const void *r2)
{
	id_access *ida1 = (id_access *)r1;
	id_access *ida2 = (id_access *)r2;
	id_t rid1 = ida1->id, rid2 = ida2->id;
	return rid1 == rid2 ? 0 : rid1 < rid2 ? -1 : 1;
}

static void sort_ida_entries(ida_entries *idal)
{
	if (!idal->count)
		return;
	qsort(idal->idas, idal->count, sizeof idal->idas[0], id_access_sorter);
}

/* === System ACLs === */

/* Transfer the count id_access items out of the temp_ida_list into either
 * the users or groups ida_entries list in racl. */
static void save_idas(rsync_acl *racl, SMB_ACL_TAG_T type, item_list *temp_ida_list)
{
	id_access *idas;
	ida_entries *ent;

	if (temp_ida_list->count) {
		int cnt = temp_ida_list->count;
		id_access *temp_idas = temp_ida_list->items;
		if (!(idas = new_array(id_access, cnt)))
			out_of_memory("save_idas");
		memcpy(idas, temp_idas, cnt * sizeof *temp_idas);
	} else
		idas = NULL;

	ent = type == SMB_ACL_USER ? &racl->users : &racl->groups;

	if (ent->count) {
		rprintf(FERROR, "save_idas: disjoint list found for type %d\n", type);
		exit_cleanup(RERR_UNSUPPORTED);
	}
	ent->count = temp_ida_list->count;
	ent->idas = idas;

	/* Truncate the temporary list now that its idas have been saved. */
	temp_ida_list->count = 0;
}

/* Unpack system ACL -> rsync ACL verbatim.  Return whether we succeeded. */
static BOOL unpack_smb_acl(SMB_ACL_T sacl, rsync_acl *racl)
{
	static item_list temp_ida_list = EMPTY_ITEM_LIST;
	SMB_ACL_TAG_T prior_list_type = 0;
	SMB_ACL_ENTRY_T entry;
	const char *errfun;
	int rc;

	errfun = "sys_acl_get_entry";
	for (rc = sys_acl_get_entry(sacl, SMB_ACL_FIRST_ENTRY, &entry);
	     rc == 1;
	     rc = sys_acl_get_entry(sacl, SMB_ACL_NEXT_ENTRY, &entry)) {
		SMB_ACL_TAG_T tag_type;
		SMB_ACL_PERMSET_T permset;
		uchar access;
		void *qualifier;
		id_access *ida;
		if ((rc = sys_acl_get_tag_type(entry, &tag_type))) {
			errfun = "sys_acl_get_tag_type";
			break;
		}
		if ((rc = sys_acl_get_permset(entry, &permset))) {
			errfun = "sys_acl_get_tag_type";
			break;
		}
		access = (sys_acl_get_perm(permset, SMB_ACL_READ) ? 4 : 0)
		       | (sys_acl_get_perm(permset, SMB_ACL_WRITE) ? 2 : 0)
		       | (sys_acl_get_perm(permset, SMB_ACL_EXECUTE) ? 1 : 0);
		/* continue == done with entry; break == store in temporary ida list */
		switch (tag_type) {
		case SMB_ACL_USER_OBJ:
			if (racl->user_obj == NO_ENTRY)
				racl->user_obj = access;
			else
				rprintf(FINFO, "unpack_smb_acl: warning: duplicate USER_OBJ entry ignored\n");
			continue;
		case SMB_ACL_USER:
			break;
		case SMB_ACL_GROUP_OBJ:
			if (racl->group_obj == NO_ENTRY)
				racl->group_obj = access;
			else
				rprintf(FINFO, "unpack_smb_acl: warning: duplicate GROUP_OBJ entry ignored\n");
			continue;
		case SMB_ACL_GROUP:
			break;
		case SMB_ACL_MASK:
			if (racl->mask_obj == NO_ENTRY)
				racl->mask_obj = access;
			else
				rprintf(FINFO, "unpack_smb_acl: warning: duplicate MASK entry ignored\n");
			continue;
		case SMB_ACL_OTHER:
			if (racl->other_obj == NO_ENTRY)
				racl->other_obj = access;
			else
				rprintf(FINFO, "unpack_smb_acl: warning: duplicate OTHER entry ignored\n");
			continue;
		default:
			rprintf(FINFO, "unpack_smb_acl: warning: entry with unrecognized tag type ignored\n");
			continue;
		}
		if (!(qualifier = sys_acl_get_qualifier(entry))) {
			errfun = "sys_acl_get_tag_type";
			rc = EINVAL;
			break;
		}
		if (tag_type != prior_list_type) {
			if (prior_list_type)
				save_idas(racl, prior_list_type, &temp_ida_list);
			prior_list_type = tag_type;
		}
		ida = EXPAND_ITEM_LIST(&temp_ida_list, id_access, -10);
		ida->id = *((id_t *)qualifier);
		ida->access = access;
		sys_acl_free_qualifier(qualifier, tag_type);
	}
	if (rc) {
		rsyserr(FERROR, errno, "unpack_smb_acl: %s()", errfun);
		rsync_acl_free(racl);
		return False;
	}
	if (prior_list_type)
		save_idas(racl, prior_list_type, &temp_ida_list);

	sort_ida_entries(&racl->users);
	sort_ida_entries(&racl->groups);

#ifdef ACLS_NEED_MASK
	if (!racl->users.count && !racl->groups.count && racl->mask_obj != NO_ENTRY) {
		/* Throw away a superfluous mask, but mask off the
		 * group perms with it first. */
		racl->group_obj &= racl->mask_obj;
		racl->mask_obj = NO_ENTRY;
	}
#endif

	return True;
}

/* Synactic sugar for system calls */

#define CALL_OR_ERROR(func,args,str) \
	do { \
		if (func args) { \
			errfun = str; \
			goto error_exit; \
		} \
	} while (0)

#define COE(func,args) CALL_OR_ERROR(func,args,#func)
#define COE2(func,args) CALL_OR_ERROR(func,args,NULL)

/* Store the permissions in the system ACL entry. */
static int store_access_in_entry(uchar access, SMB_ACL_ENTRY_T entry)
{
	const char *errfun = NULL;
	SMB_ACL_PERMSET_T permset;

	COE( sys_acl_get_permset,(entry, &permset) );
	COE( sys_acl_clear_perms,(permset) );
	if (access & 4)
		COE( sys_acl_add_perm,(permset, SMB_ACL_READ) );
	if (access & 2)
		COE( sys_acl_add_perm,(permset, SMB_ACL_WRITE) );
	if (access & 1)
		COE( sys_acl_add_perm,(permset, SMB_ACL_EXECUTE) );
	COE( sys_acl_set_permset,(entry, permset) );

	return 0;

  error_exit:
	rsyserr(FERROR, errno, "store_access_in_entry %s()", errfun);
	return -1;
}

/* Pack rsync ACL -> system ACL verbatim.  Return whether we succeeded. */
static BOOL pack_smb_acl(SMB_ACL_T *smb_acl, const rsync_acl *racl)
{
#ifdef ACLS_NEED_MASK
	uchar mask_bits;
#endif
	size_t count;
	id_access *ida;
	const char *errfun = NULL;
	SMB_ACL_ENTRY_T entry;

	if (!(*smb_acl = sys_acl_init(calc_sacl_entries(racl)))) {
		rsyserr(FERROR, errno, "pack_smb_acl: sys_acl_init()");
		return False;
	}

	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_tag_type,(entry, SMB_ACL_USER_OBJ) );
	COE2( store_access_in_entry,(racl->user_obj & 7, entry) );

	for (ida = racl->users.idas, count = racl->users.count; count--; ida++) {
		COE( sys_acl_create_entry,(smb_acl, &entry) );
		COE( sys_acl_set_tag_type,(entry, SMB_ACL_USER) );
		COE( sys_acl_set_qualifier,(entry, (void*)&ida->id) );
		COE2( store_access_in_entry,(ida->access, entry) );
	}

	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_tag_type,(entry, SMB_ACL_GROUP_OBJ) );
	COE2( store_access_in_entry,(racl->group_obj & 7, entry) );

	for (ida = racl->groups.idas, count = racl->groups.count; count--; ida++) {
		COE( sys_acl_create_entry,(smb_acl, &entry) );
		COE( sys_acl_set_tag_type,(entry, SMB_ACL_GROUP) );
		COE( sys_acl_set_qualifier,(entry, (void*)&ida->id) );
		COE2( store_access_in_entry,(ida->access, entry) );
	}

#ifdef ACLS_NEED_MASK
	mask_bits = racl->mask_obj == NO_ENTRY ? racl->group_obj & 7 : racl->mask_obj;
	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_tag_type,(entry, SMB_ACL_MASK) );
	COE2( store_access_in_entry,(mask_bits, entry) );
#else
	if (racl->mask_obj != NO_ENTRY) {
		COE( sys_acl_create_entry,(smb_acl, &entry) );
		COE( sys_acl_set_tag_type,(entry, SMB_ACL_MASK) );
		COE2( store_access_in_entry,(racl->mask_obj, entry) );
	}
#endif

	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_tag_type,(entry, SMB_ACL_OTHER) );
	COE2( store_access_in_entry,(racl->other_obj & 7, entry) );

#ifdef DEBUG
	if (sys_acl_valid(*smb_acl) < 0)
		rprintf(FERROR, "pack_smb_acl: warning: system says the ACL I packed is invalid\n");
#endif

	return True;

  error_exit:
	if (errfun) {
		rsyserr(FERROR, errno, "pack_smb_acl %s()", errfun);
	}
	sys_acl_free_acl(*smb_acl);
	return False;
}

static int find_matching_rsync_acl(const rsync_acl *racl, SMB_ACL_TYPE_T type,
				   const item_list *racl_list)
{
	static int access_match = -1, default_match = -1;
	int *match = type == SMB_ACL_TYPE_ACCESS ? &access_match : &default_match;
	size_t count = racl_list->count;

	/* If this is the first time through or we didn't match the last
	 * time, then start at the end of the list, which should be the
	 * best place to start hunting. */
	if (*match == -1)
		*match = racl_list->count - 1;
	while (count--) {
		rsync_acl *base = racl_list->items;
		if (rsync_acl_equal(base + *match, racl))
			return *match;
		if (!(*match)--)
			*match = racl_list->count - 1;
	}

	*match = -1;
	return *match;
}

static int get_rsync_acl(const char *fname, rsync_acl *racl,
			 SMB_ACL_TYPE_T type, mode_t mode)
{
	SMB_ACL_T sacl = sys_acl_get_file(fname, type);

	if (sacl) {
		BOOL ok = unpack_smb_acl(sacl, racl);

		sys_acl_free_acl(sacl);
		if (!ok) {
			return -1;
		}
	} else if (no_acl_syscall_error(errno)) {
		/* ACLs are not supported, so pretend we have a basic ACL. */
		if (type == SMB_ACL_TYPE_ACCESS)
			rsync_acl_fake_perms(racl, mode);
	} else {
		rsyserr(FERROR, errno, "get_acl: sys_acl_get_file(%s, %s)",
			fname, str_acl_type(type));
		return -1;
	}

	return 0;
}

/* Return the Access Control List for the given filename. */
int get_acl(const char *fname, statx *sxp)
{
	sxp->acc_acl = create_racl();
	if (get_rsync_acl(fname, sxp->acc_acl, SMB_ACL_TYPE_ACCESS,
			  sxp->st.st_mode) < 0) {
		free_acl(sxp);
		return -1;
	}

	if (S_ISDIR(sxp->st.st_mode)) {
		sxp->def_acl = create_racl();
		if (get_rsync_acl(fname, sxp->def_acl, SMB_ACL_TYPE_DEFAULT,
				  sxp->st.st_mode) < 0) {
			free_acl(sxp);
			return -1;
		}
	}

	return 0;
}

/* === Send functions === */

/* The general strategy with the tag_type <-> character mapping is that
 * lowercase implies that no qualifier follows, where uppercase does.
 * A similar idiom for the ACL type (access or default) itself, but
 * lowercase in this instance means there's no ACL following, so the
 * ACL is a repeat, so the receiver should reuse the last of the same
 * type ACL. */

/* Send the ida list over the file descriptor. */
static void send_ida_entries(const ida_entries *idal, int user_names, int f)
{
	id_access *ida;
	size_t count = idal->count;

	write_varint(f, idal->count);

	for (ida = idal->idas; count--; ida++) {
		char *name = user_names ? add_uid(ida->id) : add_gid(ida->id);
		write_varint(f, ida->id);
		if (inc_recurse && name) {
			int len = strlen(name);
			write_byte(f, ida->access | (uchar)0x80);
			write_byte(f, len);
			write_buf(f, name, len);
		} else
			write_byte(f, ida->access);
	}
}

static void send_rsync_acl(rsync_acl *racl, SMB_ACL_TYPE_T type,
			   item_list *racl_list, int f)
{
	int ndx = find_matching_rsync_acl(racl, type, racl_list);

	/* Send 0 (-1 + 1) to indicate that literal ACL data follows. */
	write_varint(f, ndx + 1);

	if (ndx < 0) {
		rsync_acl *new_racl = EXPAND_ITEM_LIST(racl_list, rsync_acl, 1000);
		uchar flags = 0;

		if (racl->user_obj != NO_ENTRY)
			flags |= XMIT_USER_OBJ;
		if (racl->users.count)
			flags |= XMIT_USER_LIST;

		if (racl->group_obj != NO_ENTRY)
			flags |= XMIT_GROUP_OBJ;
		if (racl->groups.count)
			flags |= XMIT_GROUP_LIST;

		if (racl->mask_obj != NO_ENTRY)
			flags |= XMIT_MASK_OBJ;

		if (racl->other_obj != NO_ENTRY)
			flags |= XMIT_OTHER_OBJ;

		write_byte(f, flags);

		if (flags & XMIT_USER_OBJ)
			write_byte(f, racl->user_obj);
		if (flags & XMIT_USER_LIST)
			send_ida_entries(&racl->users, 1, f);

		if (flags & XMIT_GROUP_OBJ)
			write_byte(f, racl->group_obj);
		if (flags & XMIT_GROUP_LIST)
			send_ida_entries(&racl->groups, 0, f);

		if (flags & XMIT_MASK_OBJ)
			write_byte(f, racl->mask_obj);

		if (flags & XMIT_OTHER_OBJ)
			write_byte(f, racl->other_obj);

		/* Give the allocated data to the new list object. */
		*new_racl = *racl;
		*racl = empty_rsync_acl;
	}
}

/* Send the ACL from the statx structure down the indicated file descriptor.
 * This also frees the ACL data. */
void send_acl(statx *sxp, int f)
{
	if (!sxp->acc_acl) {
		sxp->acc_acl = create_racl();
		rsync_acl_fake_perms(sxp->acc_acl, sxp->st.st_mode);
	}
	/* Avoid sending values that can be inferred from other data. */
	rsync_acl_strip_perms(sxp->acc_acl);

	send_rsync_acl(sxp->acc_acl, SMB_ACL_TYPE_ACCESS, &access_acl_list, f);

	if (S_ISDIR(sxp->st.st_mode)) {
		if (!sxp->def_acl)
			sxp->def_acl = create_racl();

		send_rsync_acl(sxp->def_acl, SMB_ACL_TYPE_DEFAULT, &default_acl_list, f);
	}
}

/* === Receive functions === */

static uchar recv_acl_access(uchar *name_follows_val, int f)
{
	uchar access = read_byte(f);

	if (name_follows_val) {
		if (access & (uchar)0x80) {
			access &= ~(uchar)0x80;
			*name_follows_val = 1;
		} else
			*name_follows_val = 0;
	}

	if (access & ~(4 | 2 | 1)) {
		rprintf(FERROR, "recv_acl_access: bogus permset %o\n", access);
		exit_cleanup(RERR_STREAMIO);
	}

	return access;
}

static uchar recv_ida_entries(ida_entries *ent, int user_names, int f)
{
	uchar computed_mask_bits = 0;
	int i, count = read_varint(f);

	if (count) {
		if (!(ent->idas = new_array(id_access, ent->count)))
			out_of_memory("recv_ida_entries");
	} else
		ent->idas = NULL;

	ent->count = count;

	for (i = 0; i < count; i++) {
		uchar has_name;
		id_t id = read_varint(f);
		int access = recv_acl_access(&has_name, f);

		if (has_name) {
			if (user_names)
				id = recv_user_name(f, id);
			else
				id = recv_group_name(f, id);
		} else if (user_names) {
			if (inc_recurse && am_root && !numeric_ids)
				id = match_uid(id);
		} else {
			if (inc_recurse && (!am_root || !numeric_ids))
				id = match_gid(id);
		}

		ent->idas[i].id = id;
		ent->idas[i].access = access;
		computed_mask_bits |= access;
	}

	return computed_mask_bits;
}

static int recv_rsync_acl(item_list *racl_list, SMB_ACL_TYPE_T type, int f)
{
	uchar computed_mask_bits = 0;
	acl_duo *duo_item;
	uchar flags;
	int ndx = read_varint(f);

	if (ndx < 0 || (size_t)ndx > racl_list->count) {
		rprintf(FERROR, "recv_acl_index: %s ACL index %d > %d\n",
			str_acl_type(type), ndx, (int)racl_list->count);
		exit_cleanup(RERR_STREAMIO);
	}

	if (ndx != 0)
		return ndx - 1;
	
	ndx = racl_list->count;
	duo_item = EXPAND_ITEM_LIST(racl_list, acl_duo, 1000);
	duo_item->racl = empty_rsync_acl;

	flags = read_byte(f);

	if (flags & XMIT_USER_OBJ)
		duo_item->racl.user_obj = recv_acl_access(NULL, f);

	if (flags & XMIT_USER_LIST)
		computed_mask_bits |= recv_ida_entries(&duo_item->racl.users, 1, f);

	if (flags & XMIT_GROUP_OBJ)
		duo_item->racl.group_obj = recv_acl_access(NULL, f);

	if (flags & XMIT_GROUP_LIST)
		computed_mask_bits |= recv_ida_entries(&duo_item->racl.groups, 0, f);

	if (flags & XMIT_MASK_OBJ)
		duo_item->racl.mask_obj = recv_acl_access(NULL, f);

	if (flags & XMIT_OTHER_OBJ)
		duo_item->racl.other_obj = recv_acl_access(NULL, f);

	if (!duo_item->racl.users.count && !duo_item->racl.groups.count) {
		/* If we received a superfluous mask, throw it away. */
		if (duo_item->racl.mask_obj != NO_ENTRY) {
			/* Mask off the group perms with it first. */
			duo_item->racl.group_obj &= duo_item->racl.mask_obj | NO_ENTRY;
			duo_item->racl.mask_obj = NO_ENTRY;
		}
	} else if (duo_item->racl.mask_obj == NO_ENTRY) /* Must be non-empty with lists. */
		duo_item->racl.mask_obj = computed_mask_bits | (duo_item->racl.group_obj & 7);

	duo_item->sacl = NULL;

	return ndx;
}

/* Receive the ACL info the sender has included for this file-list entry. */
void receive_acl(struct file_struct *file, int f)
{
	F_ACL(file) = recv_rsync_acl(&access_acl_list, SMB_ACL_TYPE_ACCESS, f);

	if (S_ISDIR(file->mode))
		F_DEF_ACL(file) = recv_rsync_acl(&default_acl_list, SMB_ACL_TYPE_DEFAULT, f);
}

static int cache_rsync_acl(rsync_acl *racl, SMB_ACL_TYPE_T type, item_list *racl_list)
{
	int ndx;

	if (!racl)
		ndx = -1;
	else if ((ndx = find_matching_rsync_acl(racl, type, racl_list)) == -1) {
		acl_duo *new_duo;
		ndx = racl_list->count;
		new_duo = EXPAND_ITEM_LIST(racl_list, acl_duo, 1000);
		new_duo->racl = *racl;
		new_duo->sacl = NULL;
		*racl = empty_rsync_acl;
	}

	return ndx;
}

/* Turn the ACL data in statx into cached ACL data, setting the index
 * values in the file struct. */
void cache_acl(struct file_struct *file, statx *sxp)
{
	F_ACL(file) = cache_rsync_acl(sxp->acc_acl,
				      SMB_ACL_TYPE_ACCESS, &access_acl_list);

	if (S_ISDIR(sxp->st.st_mode)) {
		F_DEF_ACL(file) = cache_rsync_acl(sxp->def_acl,
				      SMB_ACL_TYPE_DEFAULT, &default_acl_list);
	}
}

static mode_t change_sacl_perms(SMB_ACL_T sacl, rsync_acl *racl, mode_t old_mode, mode_t mode)
{
	SMB_ACL_ENTRY_T entry;
	const char *errfun;
	int rc;

	if (S_ISDIR(mode)) {
		/* If the sticky bit is going on, it's not safe to allow all
		 * the new ACL to go into effect before it gets set. */
#ifdef SMB_ACL_LOSES_SPECIAL_MODE_BITS
		if (mode & S_ISVTX)
			mode &= ~0077;
#else
		if (mode & S_ISVTX && !(old_mode & S_ISVTX))
			mode &= ~0077;
	} else {
		/* If setuid or setgid is going off, it's not safe to allow all
		 * the new ACL to go into effect before they get cleared. */
		if ((old_mode & S_ISUID && !(mode & S_ISUID))
		 || (old_mode & S_ISGID && !(mode & S_ISGID)))
			mode &= ~0077;
#endif
	}

	errfun = "sys_acl_get_entry";
	for (rc = sys_acl_get_entry(sacl, SMB_ACL_FIRST_ENTRY, &entry);
	     rc == 1;
	     rc = sys_acl_get_entry(sacl, SMB_ACL_NEXT_ENTRY, &entry)) {
		SMB_ACL_TAG_T tag_type;
		if ((rc = sys_acl_get_tag_type(entry, &tag_type))) {
			errfun = "sys_acl_get_tag_type";
			break;
		}
		switch (tag_type) {
		case SMB_ACL_USER_OBJ:
			COE2( store_access_in_entry,((mode >> 6) & 7, entry) );
			break;
		case SMB_ACL_GROUP_OBJ:
			/* group is only empty when identical to group perms. */
			if (racl->group_obj != NO_ENTRY)
				break;
			COE2( store_access_in_entry,((mode >> 3) & 7, entry) );
			break;
		case SMB_ACL_MASK:
#ifndef ACLS_NEED_MASK
			/* mask is only empty when we don't need it. */
			if (racl->mask_obj == NO_ENTRY)
				break;
#endif
			COE2( store_access_in_entry,((mode >> 3) & 7, entry) );
			break;
		case SMB_ACL_OTHER:
			COE2( store_access_in_entry,(mode & 7, entry) );
			break;
		}
	}
	if (rc) {
	  error_exit:
		if (errfun) {
			rsyserr(FERROR, errno, "change_sacl_perms: %s()",
				errfun);
		}
		return (mode_t)~0;
	}

#ifdef SMB_ACL_LOSES_SPECIAL_MODE_BITS
	/* Ensure that chmod() will be called to restore any lost setid bits. */
	if (old_mode & (S_ISUID | S_ISGID | S_ISVTX)
	 && BITS_EQUAL(old_mode, mode, CHMOD_BITS))
		old_mode &= ~(S_ISUID | S_ISGID | S_ISVTX);
#endif

	/* Return the mode of the file on disk, as we will set them. */
	return (old_mode & ~ACCESSPERMS) | (mode & ACCESSPERMS);
}

static int set_rsync_acl(const char *fname, acl_duo *duo_item,
			 SMB_ACL_TYPE_T type, statx *sxp, mode_t mode)
{
	if (type == SMB_ACL_TYPE_DEFAULT
	 && duo_item->racl.user_obj == NO_ENTRY) {
		if (sys_acl_delete_def_file(fname) < 0) {
			rsyserr(FERROR, errno, "set_acl: sys_acl_delete_def_file(%s)",
				fname);
			return -1;
		}
	} else {
		mode_t cur_mode = sxp->st.st_mode;
		if (!duo_item->sacl
		 && !pack_smb_acl(&duo_item->sacl, &duo_item->racl))
			return -1;
		if (type == SMB_ACL_TYPE_ACCESS) {
			cur_mode = change_sacl_perms(duo_item->sacl, &duo_item->racl,
						     cur_mode, mode);
			if (cur_mode == (mode_t)~0)
				return 0;
		}
		if (sys_acl_set_file(fname, type, duo_item->sacl) < 0) {
			rsyserr(FERROR, errno, "set_acl: sys_acl_set_file(%s, %s)",
			fname, str_acl_type(type));
			return -1;
		}
		if (type == SMB_ACL_TYPE_ACCESS)
			sxp->st.st_mode = cur_mode;
	}

	return 0;
}

/* Set ACL on indicated filename.
 *
 * This sets extended access ACL entries and default ACL.  If convenient,
 * it sets permission bits along with the access ACL and signals having
 * done so by modifying sxp->st.st_mode.
 *
 * Returns 1 for unchanged, 0 for changed, -1 for failed.  Call this
 * with fname set to NULL to just check if the ACL is unchanged. */
int set_acl(const char *fname, const struct file_struct *file, statx *sxp)
{
	int unchanged = 1;
	int32 ndx;
	BOOL eq;

	if (!dry_run && (read_only || list_only)) {
		errno = EROFS;
		return -1;
	}

	ndx = F_ACL(file);
	if (ndx >= 0 && (size_t)ndx < access_acl_list.count) {
		acl_duo *duo_item = access_acl_list.items;
		duo_item += ndx;
		eq = sxp->acc_acl
		  && rsync_acl_equal_enough(sxp->acc_acl, &duo_item->racl, file->mode);
		if (!eq) {
			unchanged = 0;
			if (!dry_run && fname
			 && set_rsync_acl(fname, duo_item, SMB_ACL_TYPE_ACCESS,
					  sxp, file->mode) < 0)
				unchanged = -1;
		}
	}

	if (!S_ISDIR(sxp->st.st_mode))
		return unchanged;

	ndx = F_DEF_ACL(file);
	if (ndx >= 0 && (size_t)ndx < default_acl_list.count) {
		acl_duo *duo_item = default_acl_list.items;
		duo_item += ndx;
		eq = sxp->def_acl && rsync_acl_equal(sxp->def_acl, &duo_item->racl);
		if (!eq) {
			if (unchanged > 0)
				unchanged = 0;
			if (!dry_run && fname
			 && set_rsync_acl(fname, duo_item, SMB_ACL_TYPE_DEFAULT,
					  sxp, file->mode) < 0)
				unchanged = -1;
		}
	}

	return unchanged;
}

/* Non-incremental recursion needs to convert all the received IDs
 * in a single pass after the file-list is complete. */
static void match_racl_ids(const item_list *racl_list)
{
	int list_cnt, name_cnt;
	acl_duo *duo_item = racl_list->items;
	for (list_cnt = racl_list->count; list_cnt--; duo_item++) {
		ida_entries *idal = &duo_item->racl.users;
		for (name_cnt = idal->count; name_cnt--; idal++) {
			id_access *ida = idal->idas;
			ida->id = match_uid(ida->id);
		}
		idal = &duo_item->racl.groups;
		for (name_cnt = idal->count; name_cnt--; idal++) {
			id_access *ida = idal->idas;
			ida->id = match_gid(ida->id);
		}
	}
}

void match_acl_ids(void)
{
	match_racl_ids(&access_acl_list);
	match_racl_ids(&default_acl_list);
}

/* This is used by dest_mode(). */
int default_perms_for_dir(const char *dir)
{
	rsync_acl racl;
	SMB_ACL_T sacl;
	BOOL ok;
	int perms;

	if (dir == NULL)
		dir = ".";
	perms = ACCESSPERMS & ~orig_umask;
	/* Read the directory's default ACL.  If it has none, this will successfully return an empty ACL. */
	sacl = sys_acl_get_file(dir, SMB_ACL_TYPE_DEFAULT);
	if (sacl == NULL) {
		/* Couldn't get an ACL.  Darn. */
		switch (errno) {
#ifdef ENOTSUP
		case ENOTSUP:
#endif
		case ENOSYS:
			/* No ACLs are available. */
			break;
		case ENOENT:
			if (dry_run) {
				/* We're doing a dry run, so the containing directory
				 * wasn't actually created.  Don't worry about it. */
				break;
			}
			/* Otherwise fall through. */
		default:
			rprintf(FERROR, "default_perms_for_dir: sys_acl_get_file(%s, %s): %s, falling back on umask\n",
				dir, str_acl_type(SMB_ACL_TYPE_DEFAULT), strerror(errno));
		}
		return perms;
	}

	/* Convert it. */
	racl = empty_rsync_acl;
	ok = unpack_smb_acl(sacl, &racl);
	sys_acl_free_acl(sacl);
	if (!ok) {
		rprintf(FERROR, "default_perms_for_dir: unpack_smb_acl failed, falling back on umask\n");
		return perms;
	}

	/* Apply the permission-bit entries of the default ACL, if any. */
	if (racl.user_obj != NO_ENTRY) {
		perms = rsync_acl_get_perms(&racl);
		if (verbose > 2)
			rprintf(FINFO, "got ACL-based default perms %o for directory %s\n", perms, dir);
	}

	rsync_acl_free(&racl);
	return perms;
}

#endif /* SUPPORT_ACLS */
