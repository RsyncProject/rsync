/*
 * Handle passing Access Control Lists between systems.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2006-2009 Wayne Davison
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
#include "lib/sysacls.h"

#ifdef SUPPORT_ACLS

extern int dry_run;
extern int am_root;
extern int read_only;
extern int list_only;
extern int orig_umask;
extern int numeric_ids;
extern int inc_recurse;
extern int preserve_devices;
extern int preserve_specials;

/* Flags used to indicate what items are being transmitted for an entry. */
#define XMIT_USER_OBJ (1<<0)
#define XMIT_GROUP_OBJ (1<<1)
#define XMIT_MASK_OBJ (1<<2)
#define XMIT_OTHER_OBJ (1<<3)
#define XMIT_NAME_LIST (1<<4)

#define NO_ENTRY ((uchar)0x80) /* Default value of a NON-name-list entry. */

#define NAME_IS_USER (1u<<31) /* Bit used only on a name-list entry. */

/* When we send the access bits over the wire, we shift them 2 bits to the
 * left and use the lower 2 bits as flags (relevant only to a name entry).
 * This makes the protocol more efficient than sending a value that would
 * be likely to have its hightest bits set. */
#define XFLAG_NAME_FOLLOWS 0x0001u
#define XFLAG_NAME_IS_USER 0x0002u

/* === ACL structures === */

typedef struct {
	id_t id;
	uint32 access;
} id_access;

typedef struct {
	id_access *idas;
	int count;
} ida_entries;

typedef struct {
	char *name;
	uchar len;
} idname;

typedef struct rsync_acl {
	ida_entries names;
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
	{NULL, 0}, NO_ENTRY, NO_ENTRY, NO_ENTRY, NO_ENTRY
};

static item_list access_acl_list = EMPTY_ITEM_LIST;
static item_list default_acl_list = EMPTY_ITEM_LIST;

static size_t prior_access_count = (size_t)-1;
static size_t prior_default_count = (size_t)-1;

/* === Calculations on ACL types === */

static const char *str_acl_type(SMB_ACL_TYPE_T type)
{
	switch (type) {
	case SMB_ACL_TYPE_ACCESS:
#ifdef HAVE_OSX_ACLS
		return "ACL_TYPE_EXTENDED";
#else
		return "ACL_TYPE_ACCESS";
#endif
	case SMB_ACL_TYPE_DEFAULT:
		return "ACL_TYPE_DEFAULT";
	default:
		break;
	}
	return "unknown ACL type!";
}

static int calc_sacl_entries(const rsync_acl *racl)
{
	/* A System ACL always gets user/group/other permission entries. */
	return racl->names.count
#ifdef ACLS_NEED_MASK
	     + 1
#else
	     + (racl->mask_obj != NO_ENTRY)
#endif
	     + 3;
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
static void rsync_acl_strip_perms(stat_x *sxp)
{
	rsync_acl *racl = sxp->acc_acl;

	racl->user_obj = NO_ENTRY;
	if (racl->mask_obj == NO_ENTRY)
		racl->group_obj = NO_ENTRY;
	else {
		int group_perms = (sxp->st.st_mode >> 3) & 7;
		if (racl->group_obj == group_perms)
			racl->group_obj = NO_ENTRY;
#ifndef HAVE_SOLARIS_ACLS
		if (racl->names.count != 0 && racl->mask_obj == group_perms)
			racl->mask_obj = NO_ENTRY;
#endif
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
	    && ida_entries_equal(&racl1->names, &racl2->names);
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
	return ida_entries_equal(&racl1->names, &racl2->names);
}

static void rsync_acl_free(rsync_acl *racl)
{
	if (racl->names.idas)
		free(racl->names.idas);
	*racl = empty_rsync_acl;
}

void free_acl(stat_x *sxp)
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

#ifdef SMB_ACL_NEED_SORT
static int id_access_sorter(const void *r1, const void *r2)
{
	id_access *ida1 = (id_access *)r1;
	id_access *ida2 = (id_access *)r2;
	id_t rid1 = ida1->id, rid2 = ida2->id;
	if ((ida1->access ^ ida2->access) & NAME_IS_USER)
		return ida1->access & NAME_IS_USER ? -1 : 1;
	return rid1 == rid2 ? 0 : rid1 < rid2 ? -1 : 1;
}
#endif

/* === System ACLs === */

/* Unpack system ACL -> rsync ACL verbatim.  Return whether we succeeded. */
static BOOL unpack_smb_acl(SMB_ACL_T sacl, rsync_acl *racl)
{
	static item_list temp_ida_list = EMPTY_ITEM_LIST;
	SMB_ACL_ENTRY_T entry;
	const char *errfun;
	int rc;

	errfun = "sys_acl_get_entry";
	for (rc = sys_acl_get_entry(sacl, SMB_ACL_FIRST_ENTRY, &entry);
	     rc == 1;
	     rc = sys_acl_get_entry(sacl, SMB_ACL_NEXT_ENTRY, &entry)) {
		SMB_ACL_TAG_T tag_type;
		uint32 access;
		id_t g_u_id;
		id_access *ida;
		if ((rc = sys_acl_get_info(entry, &tag_type, &access, &g_u_id)) != 0) {
			errfun = "sys_acl_get_info";
			break;
		}
		/* continue == done with entry; break == store in temporary ida list */
		switch (tag_type) {
#ifndef HAVE_OSX_ACLS
		case SMB_ACL_USER_OBJ:
			if (racl->user_obj == NO_ENTRY)
				racl->user_obj = access;
			else
				rprintf(FINFO, "unpack_smb_acl: warning: duplicate USER_OBJ entry ignored\n");
			continue;
		case SMB_ACL_GROUP_OBJ:
			if (racl->group_obj == NO_ENTRY)
				racl->group_obj = access;
			else
				rprintf(FINFO, "unpack_smb_acl: warning: duplicate GROUP_OBJ entry ignored\n");
			continue;
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
#endif
		case SMB_ACL_USER:
			access |= NAME_IS_USER;
			break;
		case SMB_ACL_GROUP:
			break;
		default:
			rprintf(FINFO, "unpack_smb_acl: warning: entry with unrecognized tag type ignored\n");
			continue;
		}
		ida = EXPAND_ITEM_LIST(&temp_ida_list, id_access, -10);
		ida->id = g_u_id;
		ida->access = access;
	}
	if (rc) {
		rsyserr(FERROR_XFER, errno, "unpack_smb_acl: %s()", errfun);
		rsync_acl_free(racl);
		return False;
	}

	/* Transfer the count id_access items out of the temp_ida_list
	 * into the names ida_entries list in racl. */
	if (temp_ida_list.count) {
#ifdef SMB_ACL_NEED_SORT
		if (temp_ida_list.count > 1) {
			qsort(temp_ida_list.items, temp_ida_list.count,
			      sizeof (id_access), id_access_sorter);
		}
#endif
		if (!(racl->names.idas = new_array(id_access, temp_ida_list.count)))
			out_of_memory("unpack_smb_acl");
		memcpy(racl->names.idas, temp_ida_list.items,
		       temp_ida_list.count * sizeof (id_access));
	} else
		racl->names.idas = NULL;

	racl->names.count = temp_ida_list.count;

	/* Truncate the temporary list now that its idas have been saved. */
	temp_ida_list.count = 0;

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

#ifndef HAVE_OSX_ACLS
/* Store the permissions in the system ACL entry. */
static int store_access_in_entry(uint32 access, SMB_ACL_ENTRY_T entry)
{
	if (sys_acl_set_access_bits(entry, access)) {
		rsyserr(FERROR_XFER, errno, "store_access_in_entry sys_acl_set_access_bits()");
		return -1;
	}
	return 0;
}
#endif

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
		rsyserr(FERROR_XFER, errno, "pack_smb_acl: sys_acl_init()");
		return False;
	}

#ifndef HAVE_OSX_ACLS
	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_info,(entry, SMB_ACL_USER_OBJ, racl->user_obj & ~NO_ENTRY, 0) );
#endif

	for (ida = racl->names.idas, count = racl->names.count; count; ida++, count--) {
#ifdef SMB_ACL_NEED_SORT
		if (!(ida->access & NAME_IS_USER))
			break;
#endif
		COE( sys_acl_create_entry,(smb_acl, &entry) );
		COE( sys_acl_set_info,
		    (entry,
		     ida->access & NAME_IS_USER ? SMB_ACL_USER : SMB_ACL_GROUP,
		     ida->access & ~NAME_IS_USER, ida->id) );
	}

#ifndef HAVE_OSX_ACLS
	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_info,(entry, SMB_ACL_GROUP_OBJ, racl->group_obj & ~NO_ENTRY, 0) );

#ifdef SMB_ACL_NEED_SORT
	for ( ; count; ida++, count--) {
		COE( sys_acl_create_entry,(smb_acl, &entry) );
		COE( sys_acl_set_info,(entry, SMB_ACL_GROUP, ida->access, ida->id) );
	}
#endif

#ifdef ACLS_NEED_MASK
	mask_bits = racl->mask_obj == NO_ENTRY ? racl->group_obj & ~NO_ENTRY : racl->mask_obj;
	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_info,(entry, SMB_ACL_MASK, mask_bits, NULL) );
#else
	if (racl->mask_obj != NO_ENTRY) {
		COE( sys_acl_create_entry,(smb_acl, &entry) );
		COE( sys_acl_set_info,(entry, SMB_ACL_MASK, racl->mask_obj, 0) );
	}
#endif

	COE( sys_acl_create_entry,(smb_acl, &entry) );
	COE( sys_acl_set_info,(entry, SMB_ACL_OTHER, racl->other_obj & ~NO_ENTRY, 0) );
#endif

#ifdef DEBUG
	if (sys_acl_valid(*smb_acl) < 0)
		rprintf(FERROR_XFER, "pack_smb_acl: warning: system says the ACL I packed is invalid\n");
#endif

	return True;

  error_exit:
	if (errfun) {
		rsyserr(FERROR_XFER, errno, "pack_smb_acl %s()", errfun);
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
	SMB_ACL_T sacl;

#ifdef SUPPORT_XATTRS
	/* --fake-super support: load ACLs from an xattr. */
	if (am_root < 0) {
		char *buf;
		size_t len;
		int cnt;

		if ((buf = get_xattr_acl(fname, type == SMB_ACL_TYPE_ACCESS, &len)) == NULL)
			return 0;
		cnt = (len - 4*4) / (4+4);
		if (len < 4*4 || len != (size_t)cnt*(4+4) + 4*4) {
			free(buf);
			return -1;
		}

		racl->user_obj = IVAL(buf, 0);
		racl->group_obj = IVAL(buf, 4);
		racl->mask_obj = IVAL(buf, 8);
		racl->other_obj = IVAL(buf, 12);

		if (cnt) {
			char *bp = buf + 4*4;
			id_access *ida;
			if (!(ida = racl->names.idas = new_array(id_access, cnt)))
				out_of_memory("get_rsync_acl");
			racl->names.count = cnt;
			for ( ; cnt--; ida++, bp += 4+4) {
				ida->id = IVAL(bp, 0);
				ida->access = IVAL(bp, 4);
			}
		}
		free(buf);
		return 0;
	}
#endif

	if ((sacl = sys_acl_get_file(fname, type)) != 0) {
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
		rsyserr(FERROR_XFER, errno, "get_acl: sys_acl_get_file(%s, %s)",
			fname, str_acl_type(type));
		return -1;
	}

	return 0;
}

/* Return the Access Control List for the given filename. */
int get_acl(const char *fname, stat_x *sxp)
{
	sxp->acc_acl = create_racl();

	if (S_ISREG(sxp->st.st_mode) || S_ISDIR(sxp->st.st_mode)) {
		/* Everyone supports this. */
	} else if (S_ISLNK(sxp->st.st_mode)) {
		return 0;
	} else if (IS_SPECIAL(sxp->st.st_mode)) {
#ifndef NO_SPECIAL_ACLS
		if (!preserve_specials)
#endif
			return 0;
	} else if (IS_DEVICE(sxp->st.st_mode)) {
#ifndef NO_DEVICE_ACLS
		if (!preserve_devices)
#endif
			return 0;
	}

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

/* Send the ida list over the file descriptor. */
static void send_ida_entries(int f, const ida_entries *idal)
{
	id_access *ida;
	size_t count = idal->count;

	write_varint(f, idal->count);

	for (ida = idal->idas; count--; ida++) {
		uint32 xbits = ida->access << 2;
		const char *name;
		if (ida->access & NAME_IS_USER) {
			xbits |= XFLAG_NAME_IS_USER;
			name = numeric_ids ? NULL : add_uid(ida->id);
		} else
			name = numeric_ids ? NULL : add_gid(ida->id);
		write_varint(f, ida->id);
		if (inc_recurse && name) {
			int len = strlen(name);
			write_varint(f, xbits | XFLAG_NAME_FOLLOWS);
			write_byte(f, len);
			write_buf(f, name, len);
		} else
			write_varint(f, xbits);
	}
}

static void send_rsync_acl(int f, rsync_acl *racl, SMB_ACL_TYPE_T type,
			   item_list *racl_list)
{
	int ndx = find_matching_rsync_acl(racl, type, racl_list);

	/* Send 0 (-1 + 1) to indicate that literal ACL data follows. */
	write_varint(f, ndx + 1);

	if (ndx < 0) {
		rsync_acl *new_racl = EXPAND_ITEM_LIST(racl_list, rsync_acl, 1000);
		uchar flags = 0;

		if (racl->user_obj != NO_ENTRY)
			flags |= XMIT_USER_OBJ;
		if (racl->group_obj != NO_ENTRY)
			flags |= XMIT_GROUP_OBJ;
		if (racl->mask_obj != NO_ENTRY)
			flags |= XMIT_MASK_OBJ;
		if (racl->other_obj != NO_ENTRY)
			flags |= XMIT_OTHER_OBJ;
		if (racl->names.count)
			flags |= XMIT_NAME_LIST;

		write_byte(f, flags);

		if (flags & XMIT_USER_OBJ)
			write_varint(f, racl->user_obj);
		if (flags & XMIT_GROUP_OBJ)
			write_varint(f, racl->group_obj);
		if (flags & XMIT_MASK_OBJ)
			write_varint(f, racl->mask_obj);
		if (flags & XMIT_OTHER_OBJ)
			write_varint(f, racl->other_obj);
		if (flags & XMIT_NAME_LIST)
			send_ida_entries(f, &racl->names);

		/* Give the allocated data to the new list object. */
		*new_racl = *racl;
		*racl = empty_rsync_acl;
	}
}

/* Send the ACL from the stat_x structure down the indicated file descriptor.
 * This also frees the ACL data. */
void send_acl(int f, stat_x *sxp)
{
	if (!sxp->acc_acl) {
		sxp->acc_acl = create_racl();
		rsync_acl_fake_perms(sxp->acc_acl, sxp->st.st_mode);
	}
	/* Avoid sending values that can be inferred from other data. */
	rsync_acl_strip_perms(sxp);

	send_rsync_acl(f, sxp->acc_acl, SMB_ACL_TYPE_ACCESS, &access_acl_list);

	if (S_ISDIR(sxp->st.st_mode)) {
		if (!sxp->def_acl)
			sxp->def_acl = create_racl();

		send_rsync_acl(f, sxp->def_acl, SMB_ACL_TYPE_DEFAULT, &default_acl_list);
	}
}

/* === Receive functions === */

static uint32 recv_acl_access(int f, uchar *name_follows_ptr)
{
	uint32 access = read_varint(f);

	if (name_follows_ptr) {
		int flags = access & 3;
		access >>= 2;
		if (am_root >= 0 && access & ~SMB_ACL_VALID_NAME_BITS)
			goto value_error;
		if (flags & XFLAG_NAME_FOLLOWS)
			*name_follows_ptr = 1;
		else
			*name_follows_ptr = 0;
		if (flags & XFLAG_NAME_IS_USER)
			access |= NAME_IS_USER;
	} else if (am_root >= 0 && access & ~SMB_ACL_VALID_OBJ_BITS) {
	  value_error:
		rprintf(FERROR_XFER, "recv_acl_access: value out of range: %x\n",
			access);
		exit_cleanup(RERR_STREAMIO);
	}

	return access;
}

static uchar recv_ida_entries(int f, ida_entries *ent)
{
	uchar computed_mask_bits = 0;
	int i, count = read_varint(f);

	if (count) {
		if (!(ent->idas = new_array(id_access, count)))
			out_of_memory("recv_ida_entries");
	} else
		ent->idas = NULL;

	ent->count = count;

	for (i = 0; i < count; i++) {
		uchar has_name;
		id_t id = read_varint(f);
		uint32 access = recv_acl_access(f, &has_name);

		if (has_name) {
			if (access & NAME_IS_USER)
				id = recv_user_name(f, id);
			else
				id = recv_group_name(f, id, NULL);
		} else if (access & NAME_IS_USER) {
			if (inc_recurse && am_root && !numeric_ids)
				id = match_uid(id);
		} else {
			if (inc_recurse && (!am_root || !numeric_ids))
				id = match_gid(id, NULL);
		}

		ent->idas[i].id = id;
		ent->idas[i].access = access;
		computed_mask_bits |= access;
	}

	return computed_mask_bits & ~NO_ENTRY;
}

static int recv_rsync_acl(int f, item_list *racl_list, SMB_ACL_TYPE_T type, mode_t mode)
{
	uchar computed_mask_bits = 0;
	acl_duo *duo_item;
	uchar flags;
	int ndx = read_varint(f);

	if (ndx < 0 || (size_t)ndx > racl_list->count) {
		rprintf(FERROR_XFER, "recv_acl_index: %s ACL index %d > %d\n",
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
		duo_item->racl.user_obj = recv_acl_access(f, NULL);
	if (flags & XMIT_GROUP_OBJ)
		duo_item->racl.group_obj = recv_acl_access(f, NULL);
	if (flags & XMIT_MASK_OBJ)
		duo_item->racl.mask_obj = recv_acl_access(f, NULL);
	if (flags & XMIT_OTHER_OBJ)
		duo_item->racl.other_obj = recv_acl_access(f, NULL);
	if (flags & XMIT_NAME_LIST)
		computed_mask_bits |= recv_ida_entries(f, &duo_item->racl.names);

#ifdef HAVE_OSX_ACLS
	/* If we received a superfluous mask, throw it away. */
	duo_item->racl.mask_obj = NO_ENTRY;
#else
	if (duo_item->racl.names.count && duo_item->racl.mask_obj == NO_ENTRY) {
		/* Mask must be non-empty with lists. */
		if (type == SMB_ACL_TYPE_ACCESS)
			computed_mask_bits = (mode >> 3) & 7;
		else
			computed_mask_bits |= duo_item->racl.group_obj & ~NO_ENTRY;
		duo_item->racl.mask_obj = computed_mask_bits;
	}
#endif

	duo_item->sacl = NULL;

	return ndx;
}

/* Receive the ACL info the sender has included for this file-list entry. */
void receive_acl(int f, struct file_struct *file)
{
	F_ACL(file) = recv_rsync_acl(f, &access_acl_list, SMB_ACL_TYPE_ACCESS, file->mode);

	if (S_ISDIR(file->mode))
		F_DIR_DEFACL(file) = recv_rsync_acl(f, &default_acl_list, SMB_ACL_TYPE_DEFAULT, 0);
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

/* Turn the ACL data in stat_x into cached ACL data, setting the index
 * values in the file struct. */
void cache_tmp_acl(struct file_struct *file, stat_x *sxp)
{
	if (prior_access_count == (size_t)-1)
		prior_access_count = access_acl_list.count;

	F_ACL(file) = cache_rsync_acl(sxp->acc_acl,
				      SMB_ACL_TYPE_ACCESS, &access_acl_list);

	if (S_ISDIR(sxp->st.st_mode)) {
		if (prior_default_count == (size_t)-1)
			prior_default_count = default_acl_list.count;
		F_DIR_DEFACL(file) = cache_rsync_acl(sxp->def_acl,
				      SMB_ACL_TYPE_DEFAULT, &default_acl_list);
	}
}

static void uncache_duo_acls(item_list *duo_list, size_t start)
{
	acl_duo *duo_item = duo_list->items;
	acl_duo *duo_start = duo_item + start;

	duo_item += duo_list->count;
	duo_list->count = start;

	while (duo_item-- > duo_start) {
		rsync_acl_free(&duo_item->racl);
		if (duo_item->sacl)
			sys_acl_free_acl(duo_item->sacl);
	}
}

void uncache_tmp_acls(void)
{
	if (prior_access_count != (size_t)-1) {
		uncache_duo_acls(&access_acl_list, prior_access_count);
		prior_access_count = (size_t)-1;
	}

	if (prior_default_count != (size_t)-1) {
		uncache_duo_acls(&default_acl_list, prior_default_count);
		prior_default_count = (size_t)-1;
	}
}

#ifndef HAVE_OSX_ACLS
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
		if ((rc = sys_acl_get_tag_type(entry, &tag_type)) != 0) {
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
#ifndef HAVE_SOLARIS_ACLS
#ifndef ACLS_NEED_MASK
			/* mask is only empty when we don't need it. */
			if (racl->mask_obj == NO_ENTRY)
				break;
#endif
			COE2( store_access_in_entry,((mode >> 3) & 7, entry) );
#endif
			break;
		case SMB_ACL_OTHER:
			COE2( store_access_in_entry,(mode & 7, entry) );
			break;
		}
	}
	if (rc) {
	  error_exit:
		if (errfun) {
			rsyserr(FERROR_XFER, errno, "change_sacl_perms: %s()",
				errfun);
		}
		return (mode_t)-1;
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
#endif

static int set_rsync_acl(const char *fname, acl_duo *duo_item,
			 SMB_ACL_TYPE_T type, stat_x *sxp, mode_t mode)
{
	if (type == SMB_ACL_TYPE_DEFAULT
	 && duo_item->racl.user_obj == NO_ENTRY) {
		int rc;
#ifdef SUPPORT_XATTRS
		/* --fake-super support: delete default ACL from xattrs. */
		if (am_root < 0)
			rc = del_def_xattr_acl(fname);
		else
#endif
			rc = sys_acl_delete_def_file(fname);
		if (rc < 0) {
			rsyserr(FERROR_XFER, errno, "set_acl: sys_acl_delete_def_file(%s)",
				fname);
			return -1;
		}
#ifdef SUPPORT_XATTRS
	} else if (am_root < 0) {
		/* --fake-super support: store ACLs in an xattr. */
		int cnt = duo_item->racl.names.count;
		size_t len = 4*4 + cnt * (4+4);
		char *buf = new_array(char, len);
		int rc;

		SIVAL(buf, 0, duo_item->racl.user_obj);
		SIVAL(buf, 4, duo_item->racl.group_obj);
		SIVAL(buf, 8, duo_item->racl.mask_obj);
		SIVAL(buf, 12, duo_item->racl.other_obj);

		if (cnt) {
			char *bp = buf + 4*4;
			id_access *ida = duo_item->racl.names.idas;
			for ( ; cnt--; ida++, bp += 4+4) {
				SIVAL(bp, 0, ida->id);
				SIVAL(bp, 4, ida->access);
			}
		}
		rc = set_xattr_acl(fname, type == SMB_ACL_TYPE_ACCESS, buf, len);
		free(buf);
		return rc;
#endif
	} else {
		mode_t cur_mode = sxp->st.st_mode;
		if (!duo_item->sacl
		 && !pack_smb_acl(&duo_item->sacl, &duo_item->racl))
			return -1;
#ifdef HAVE_OSX_ACLS
		mode = 0; /* eliminate compiler warning */
#else
		if (type == SMB_ACL_TYPE_ACCESS) {
			cur_mode = change_sacl_perms(duo_item->sacl, &duo_item->racl,
						     cur_mode, mode);
			if (cur_mode == (mode_t)-1)
				return 0;
		}
#endif
		if (sys_acl_set_file(fname, type, duo_item->sacl) < 0) {
			rsyserr(FERROR_XFER, errno, "set_acl: sys_acl_set_file(%s, %s)",
				fname, str_acl_type(type));
			return -1;
		}
		if (type == SMB_ACL_TYPE_ACCESS)
			sxp->st.st_mode = cur_mode;
	}

	return 0;
}

/* Given a fname, this sets extended access ACL entries, the default ACL (for a
 * dir), and the regular mode bits on the file.  Call this with fname set to
 * NULL to just check if the ACL is different.
 *
 * If the ACL operation has a side-effect of changing the file's mode, the
 * sxp->st.st_mode value will be changed to match.
 *
 * Returns 0 for an unchanged ACL, 1 for changed, -1 for failed. */
int set_acl(const char *fname, const struct file_struct *file, stat_x *sxp, mode_t new_mode)
{
	int changed = 0;
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
		  && rsync_acl_equal_enough(sxp->acc_acl, &duo_item->racl, new_mode);
		if (!eq) {
			changed = 1;
			if (!dry_run && fname
			 && set_rsync_acl(fname, duo_item, SMB_ACL_TYPE_ACCESS,
					  sxp, new_mode) < 0)
				return -1;
		}
	}

	if (!S_ISDIR(new_mode))
		return changed;

	ndx = F_DIR_DEFACL(file);
	if (ndx >= 0 && (size_t)ndx < default_acl_list.count) {
		acl_duo *duo_item = default_acl_list.items;
		duo_item += ndx;
		eq = sxp->def_acl && rsync_acl_equal(sxp->def_acl, &duo_item->racl);
		if (!eq) {
			changed = 1;
			if (!dry_run && fname
			 && set_rsync_acl(fname, duo_item, SMB_ACL_TYPE_DEFAULT,
					  sxp, new_mode) < 0)
				return -1;
		}
	}

	return changed;
}

/* Non-incremental recursion needs to convert all the received IDs.
 * This is done in a single pass after receiving the whole file-list. */
static void match_racl_ids(const item_list *racl_list)
{
	int list_cnt, name_cnt;
	acl_duo *duo_item = racl_list->items;
	for (list_cnt = racl_list->count; list_cnt--; duo_item++) {
		ida_entries *idal = &duo_item->racl.names;
		id_access *ida = idal->idas;
		for (name_cnt = idal->count; name_cnt--; ida++) {
			if (ida->access & NAME_IS_USER)
				ida->id = match_uid(ida->id);
			else
				ida->id = match_gid(ida->id, NULL);
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
		case EINVAL:
			/* If SMB_ACL_TYPE_DEFAULT isn't valid, then the ACLs must be non-POSIX. */
			break;
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
			rprintf(FWARNING,
				"default_perms_for_dir: sys_acl_get_file(%s, %s): %s, falling back on umask\n",
				dir, str_acl_type(SMB_ACL_TYPE_DEFAULT), strerror(errno));
		}
		return perms;
	}

	/* Convert it. */
	racl = empty_rsync_acl;
	ok = unpack_smb_acl(sacl, &racl);
	sys_acl_free_acl(sacl);
	if (!ok) {
		rprintf(FWARNING, "default_perms_for_dir: unpack_smb_acl failed, falling back on umask\n");
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
