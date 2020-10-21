/*
 * Unix SMB/CIFS implementation.
 * Based on the Samba ACL support code.
 * Copyright (C) Jeremy Allison 2000.
 * Copyright (C) 2007-2020 Wayne Davison
 *
 * The permission functions have been changed to get/set all bits via
 * one call.  Some functions that rsync doesn't need were also removed.
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
 * You should have received a copy of the GNU General Public License
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"
#include "sysacls.h"

#ifdef SUPPORT_ACLS

#ifdef DEBUG
#undef DEBUG
#endif
#define DEBUG(x, y)

void SAFE_FREE(void *mem)
{
	if (mem)
		free(mem);
}

/*
 This file wraps all differing system ACL interfaces into a consistent
 one based on the POSIX interface. It also returns the correct errors
 for older UNIX systems that don't support ACLs.

 The interfaces that each ACL implementation must support are as follows :

 int sys_acl_get_entry(SMB_ACL_T theacl, int entry_id, SMB_ACL_ENTRY_T *entry_p)
 int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *tag_type_p)
 int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
 SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
 SMB_ACL_T sys_acl_get_fd(int fd)
 SMB_ACL_T sys_acl_init(int count)
 int sys_acl_create_entry(SMB_ACL_T *pacl, SMB_ACL_ENTRY_T *pentry)
 int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
 int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry, uint32 bits)
 int sys_acl_valid(SMB_ACL_T theacl)
 int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T acltype, SMB_ACL_T theacl)
 int sys_acl_set_fd(int fd, SMB_ACL_T theacl)
 int sys_acl_delete_def_file(const char *path)
 int sys_acl_free_acl(SMB_ACL_T posix_acl)

*/

#if defined(HAVE_POSIX_ACLS) /*--------------------------------------------*/

/* Identity mapping - easy. */

int sys_acl_get_entry(SMB_ACL_T the_acl, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	return acl_get_entry(the_acl, entry_id, entry_p);
}

int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *tag_type_p)
{
	return acl_get_tag_type(entry_d, tag_type_p);
}

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	return acl_get_file(path_p, type);
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	return acl_get_fd(fd);
}
#endif

#if defined(HAVE_ACL_GET_PERM_NP)
#define acl_get_perm(p, b) acl_get_perm_np(p, b)
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	acl_permset_t permset;

	if (acl_get_tag_type(entry, tag_type_p) != 0
	 || acl_get_permset(entry, &permset) != 0)
		return -1;

	*bits_p = (acl_get_perm(permset, ACL_READ) ? 4 : 0)
		| (acl_get_perm(permset, ACL_WRITE) ? 2 : 0)
		| (acl_get_perm(permset, ACL_EXECUTE) ? 1 : 0);

	if (*tag_type_p == SMB_ACL_USER || *tag_type_p == SMB_ACL_GROUP) {
		void *qual;
		if ((qual = acl_get_qualifier(entry)) == NULL)
			return -1;
		*u_g_id_p = *(id_t*)qual;
		acl_free(qual);
	}

	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	return acl_init(count);
}

int sys_acl_create_entry(SMB_ACL_T *pacl, SMB_ACL_ENTRY_T *pentry)
{
	return acl_create_entry(pacl, pentry);
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	if (acl_set_tag_type(entry, tag_type) != 0)
		return -1;

	if (tag_type == SMB_ACL_USER || tag_type == SMB_ACL_GROUP) {
		if (acl_set_qualifier(entry, (void*)&u_g_id) != 0)
			return -1;
	}

	return sys_acl_set_access_bits(entry, bits);
}

int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry, uint32 bits)
{
	acl_permset_t permset;
	int rc;
	if ((rc = acl_get_permset(entry, &permset)) != 0)
		return rc;
	acl_clear_perms(permset);
	if (bits & 4)
		acl_add_perm(permset, ACL_READ);
	if (bits & 2)
		acl_add_perm(permset, ACL_WRITE);
	if (bits & 1)
		acl_add_perm(permset, ACL_EXECUTE);
	return acl_set_permset(entry, permset);
}

int sys_acl_valid(SMB_ACL_T theacl)
{
	return acl_valid(theacl);
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T acltype, SMB_ACL_T theacl)
{
	return acl_set_file(name, acltype, theacl);
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T theacl)
{
	return acl_set_fd(fd, theacl);
}
#endif

int sys_acl_delete_def_file(const char *name)
{
	return acl_delete_def_file(name);
}

int sys_acl_free_acl(SMB_ACL_T the_acl) 
{
	return acl_free(the_acl);
}

#elif defined(HAVE_TRU64_ACLS) /*--------------------------------------------*/
/*
 * The interface to DEC/Compaq Tru64 UNIX ACLs
 * is based on Draft 13 of the POSIX spec which is
 * slightly different from the Draft 16 interface.
 * 
 * Also, some of the permset manipulation functions
 * such as acl_clear_perm() and acl_add_perm() appear
 * to be broken on Tru64 so we have to manipulate
 * the permission bits in the permset directly.
 */
int sys_acl_get_entry(SMB_ACL_T the_acl, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	SMB_ACL_ENTRY_T	entry;

	if (entry_id == SMB_ACL_FIRST_ENTRY && acl_first_entry(the_acl) != 0) {
		return -1;
	}

	errno = 0;
	if ((entry = acl_get_entry(the_acl)) != NULL) {
		*entry_p = entry;
		return 1;
	}

	return errno ? -1 : 0;
}

int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *tag_type_p)
{
	return acl_get_tag_type(entry_d, tag_type_p);
}

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	return acl_get_file((char *)path_p, type);
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	return acl_get_fd(fd, ACL_TYPE_ACCESS);
}
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	acl_permset_t permset;

	if (acl_get_tag_type(entry, tag_type_p) != 0
	 || acl_get_permset(entry, &permset) != 0)
		return -1;

	*bits_p = *permset & 7;	/* Tru64 doesn't have acl_get_perm() */

	if (*tag_type_p == SMB_ACL_USER || *tag_type_p == SMB_ACL_GROUP) {
		void *qual;
		if ((qual = acl_get_qualifier(entry)) == NULL)
			return -1;
		*u_g_id_p = *(id_t*)qual;
		acl_free_qualifier(qual, *tag_type_p);
	}

	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	return acl_init(count);
}

int sys_acl_create_entry(SMB_ACL_T *pacl, SMB_ACL_ENTRY_T *pentry)
{
	SMB_ACL_ENTRY_T entry;

	if ((entry = acl_create_entry(pacl)) == NULL) {
		return -1;
	}

	*pentry = entry;
	return 0;
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	if (acl_set_tag_type(entry, tag_type) != 0)
		return -1;

	if (tag_type == SMB_ACL_USER || tag_type == SMB_ACL_GROUP) {
		if (acl_set_qualifier(entry, (void*)&u_g_id) != 0)
			return -1;
	}

	return sys_acl_set_access_bits(entry, bits);
}

int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry, uint32 bits)
{
	acl_permset_t permset;
	int rc;
	if ((rc = acl_get_permset(entry, &permset)) != 0)
		return rc;
	*permset = bits & 7;
	return acl_set_permset(entry, permset);
}

int sys_acl_valid(SMB_ACL_T theacl)
{
	acl_entry_t	entry;

	return acl_valid(theacl, &entry);
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T acltype, SMB_ACL_T theacl)
{
	return acl_set_file((char *)name, acltype, theacl);
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T theacl)
{
	return acl_set_fd(fd, ACL_TYPE_ACCESS, theacl);
}
#endif

int sys_acl_delete_def_file(const char *name)
{
	return acl_delete_def_file((char *)name);
}

int sys_acl_free_acl(SMB_ACL_T the_acl) 
{
	return acl_free(the_acl);
}

#elif defined(HAVE_UNIXWARE_ACLS) || defined(HAVE_SOLARIS_ACLS) /*-----------*/

/*
 * Donated by Michael Davidson <md@sco.COM> for UnixWare / OpenUNIX.
 * Modified by Toomas Soome <tsoome@ut.ee> for Solaris.
 */

/*
 * Note that while this code implements sufficient functionality
 * to support the sys_acl_* interfaces it does not provide all
 * of the semantics of the POSIX ACL interfaces.
 *
 * In particular, an ACL entry descriptor (SMB_ACL_ENTRY_T) returned
 * from a call to sys_acl_get_entry() should not be assumed to be
 * valid after calling any of the following functions, which may
 * reorder the entries in the ACL.
 *
 *	sys_acl_valid()
 *	sys_acl_set_file()
 *	sys_acl_set_fd()
 */

/*
 * The only difference between Solaris and UnixWare / OpenUNIX is
 * that the #defines for the ACL operations have different names
 */
#if defined(HAVE_UNIXWARE_ACLS)

#define	SETACL		ACL_SET
#define	GETACL		ACL_GET
#define	GETACLCNT	ACL_CNT

#endif


int sys_acl_get_entry(SMB_ACL_T acl_d, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	if (entry_id != SMB_ACL_FIRST_ENTRY && entry_id != SMB_ACL_NEXT_ENTRY) {
		errno = EINVAL;
		return -1;
	}

	if (entry_p == NULL) {
		errno = EINVAL;
		return -1;
	}

	if (entry_id == SMB_ACL_FIRST_ENTRY) {
		acl_d->next = 0;
	}

	if (acl_d->next < 0) {
		errno = EINVAL;
		return -1;
	}

	if (acl_d->next >= acl_d->count) {
		return 0;
	}

	*entry_p = &acl_d->acl[acl_d->next++];

	return 1;
}

int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *type_p)
{
	*type_p = entry_d->a_type;

	return 0;
}

/*
 * There is no way of knowing what size the ACL returned by
 * GETACL will be unless you first call GETACLCNT which means
 * making an additional system call.
 *
 * In the hope of avoiding the cost of the additional system
 * call in most cases, we initially allocate enough space for
 * an ACL with INITIAL_ACL_SIZE entries. If this turns out to
 * be too small then we use GETACLCNT to find out the actual
 * size, reallocate the ACL buffer, and then call GETACL again.
 */

#define	INITIAL_ACL_SIZE	16

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	SMB_ACL_T	acl_d;
	int		count;		/* # of ACL entries allocated	*/
	int		naccess;	/* # of access ACL entries	*/
	int		ndefault;	/* # of default ACL entries	*/

	if (type != SMB_ACL_TYPE_ACCESS && type != SMB_ACL_TYPE_DEFAULT) {
		errno = EINVAL;
		return NULL;
	}

	count = INITIAL_ACL_SIZE;
	if ((acl_d = sys_acl_init(count)) == NULL) {
		return NULL;
	}

	/*
	 * If there isn't enough space for the ACL entries we use
	 * GETACLCNT to determine the actual number of ACL entries
	 * reallocate and try again. This is in a loop because it
	 * is possible that someone else could modify the ACL and
	 * increase the number of entries between the call to
	 * GETACLCNT and the call to GETACL.
	 */
	while ((count = acl(path_p, GETACL, count, &acl_d->acl[0])) < 0
	    && errno == ENOSPC) {

		sys_acl_free_acl(acl_d);

		if ((count = acl(path_p, GETACLCNT, 0, NULL)) < 0) {
			return NULL;
		}

		if ((acl_d = sys_acl_init(count)) == NULL) {
			return NULL;
		}
	}

	if (count < 0) {
		sys_acl_free_acl(acl_d);
		return NULL;
	}

	/*
	 * calculate the number of access and default ACL entries
	 *
	 * Note: we assume that the acl() system call returned a
	 * well formed ACL which is sorted so that all of the
	 * access ACL entries precede any default ACL entries
	 */
	for (naccess = 0; naccess < count; naccess++) {
		if (acl_d->acl[naccess].a_type & ACL_DEFAULT)
			break;
	}
	ndefault = count - naccess;
	
	/*
	 * if the caller wants the default ACL we have to copy
	 * the entries down to the start of the acl[] buffer
	 * and mask out the ACL_DEFAULT flag from the type field
	 */
	if (type == SMB_ACL_TYPE_DEFAULT) {
		int	i, j;

		for (i = 0, j = naccess; i < ndefault; i++, j++) {
			acl_d->acl[i] = acl_d->acl[j];
			acl_d->acl[i].a_type &= ~ACL_DEFAULT;
		}

		acl_d->count = ndefault;
	} else {
		acl_d->count = naccess;
	}

	return acl_d;
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	SMB_ACL_T	acl_d;
	int		count;		/* # of ACL entries allocated	*/
	int		naccess;	/* # of access ACL entries	*/

	count = INITIAL_ACL_SIZE;
	if ((acl_d = sys_acl_init(count)) == NULL) {
		return NULL;
	}

	while ((count = facl(fd, GETACL, count, &acl_d->acl[0])) < 0
	    && errno == ENOSPC) {

		sys_acl_free_acl(acl_d);

		if ((count = facl(fd, GETACLCNT, 0, NULL)) < 0) {
			return NULL;
		}

		if ((acl_d = sys_acl_init(count)) == NULL) {
			return NULL;
		}
	}

	if (count < 0) {
		sys_acl_free_acl(acl_d);
		return NULL;
	}

	/*
	 * calculate the number of access ACL entries
	 */
	for (naccess = 0; naccess < count; naccess++) {
		if (acl_d->acl[naccess].a_type & ACL_DEFAULT)
			break;
	}
	
	acl_d->count = naccess;

	return acl_d;
}
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	*tag_type_p = entry->a_type;

	*bits_p = entry->a_perm;

	if (*tag_type_p == SMB_ACL_USER || *tag_type_p == SMB_ACL_GROUP)
		*u_g_id_p = entry->a_id;
	
	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	SMB_ACL_T	a;

	if (count < 0) {
		errno = EINVAL;
		return NULL;
	}

	/*
	 * note that since the definition of the structure pointed
	 * to by the SMB_ACL_T includes the first element of the
	 * acl[] array, this actually allocates an ACL with room
	 * for (count+1) entries
	 */
	if ((a = (SMB_ACL_T)SMB_MALLOC(sizeof a[0] + count * sizeof (struct acl))) == NULL) {
		errno = ENOMEM;
		return NULL;
	}

	a->size = count + 1;
	a->count = 0;
	a->next = -1;

	return a;
}


int sys_acl_create_entry(SMB_ACL_T *acl_p, SMB_ACL_ENTRY_T *entry_p)
{
	SMB_ACL_T	acl_d;
	SMB_ACL_ENTRY_T	entry_d;

	if (acl_p == NULL || entry_p == NULL || (acl_d = *acl_p) == NULL) {
		errno = EINVAL;
		return -1;
	}

	if (acl_d->count >= acl_d->size) {
		errno = ENOSPC;
		return -1;
	}

	entry_d		= &acl_d->acl[acl_d->count++];
	entry_d->a_type	= 0;
	entry_d->a_id	= -1;
	entry_d->a_perm	= 0;
	*entry_p	= entry_d;

	return 0;
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	entry->a_type = tag_type;

	if (tag_type == SMB_ACL_USER || tag_type == SMB_ACL_GROUP)
		entry->a_id = u_g_id;

	entry->a_perm = bits;

	return 0;
}

int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry_d, uint32 bits)
{
	entry_d->a_perm = bits;
	return 0;
}

/*
 * sort the ACL and check it for validity
 *
 * if it's a minimal ACL with only 4 entries then we
 * need to recalculate the mask permissions to make
 * sure that they are the same as the GROUP_OBJ
 * permissions as required by the UnixWare acl() system call.
 *
 * (note: since POSIX allows minimal ACLs which only contain
 * 3 entries - ie there is no mask entry - we should, in theory,
 * check for this and add a mask entry if necessary - however
 * we "know" that the caller of this interface always specifies
 * a mask so, in practice "this never happens" (tm) - if it *does*
 * happen aclsort() will fail and return an error and someone will
 * have to fix it ...)
 */

static int acl_sort(SMB_ACL_T acl_d)
{
	int     fixmask = (acl_d->count <= 4);

	if (aclsort(acl_d->count, fixmask, acl_d->acl) != 0) {
		errno = EINVAL;
		return -1;
	}
	return 0;
}
 
int sys_acl_valid(SMB_ACL_T acl_d)
{
	return acl_sort(acl_d);
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T type, SMB_ACL_T acl_d)
{
	struct stat	s;
	struct acl	*acl_p;
	int		acl_count;
	struct acl	*acl_buf	= NULL;
	int		ret;

	if (type != SMB_ACL_TYPE_ACCESS && type != SMB_ACL_TYPE_DEFAULT) {
		errno = EINVAL;
		return -1;
	}

	if (acl_sort(acl_d) != 0) {
		return -1;
	}

	acl_p		= &acl_d->acl[0];
	acl_count	= acl_d->count;

	/*
	 * if it's a directory there is extra work to do
	 * since the acl() system call will replace both
	 * the access ACLs and the default ACLs (if any)
	 */
	if (stat(name, &s) != 0) {
		return -1;
	}
	if (S_ISDIR(s.st_mode)) {
		SMB_ACL_T	acc_acl;
		SMB_ACL_T	def_acl;
		SMB_ACL_T	tmp_acl;
		int		i;

		if (type == SMB_ACL_TYPE_ACCESS) {
			acc_acl = acl_d;
			def_acl = tmp_acl = sys_acl_get_file(name, SMB_ACL_TYPE_DEFAULT);

		} else {
			def_acl = acl_d;
			acc_acl = tmp_acl = sys_acl_get_file(name, SMB_ACL_TYPE_ACCESS);
		}

		if (tmp_acl == NULL) {
			return -1;
		}

		/*
		 * allocate a temporary buffer for the complete ACL
		 */
		acl_count = acc_acl->count + def_acl->count;
		acl_p = acl_buf = SMB_MALLOC_ARRAY(struct acl, acl_count);

		if (acl_buf == NULL) {
			sys_acl_free_acl(tmp_acl);
			errno = ENOMEM;
			return -1;
		}

		/*
		 * copy the access control and default entries into the buffer
		 */
		memcpy(&acl_buf[0], &acc_acl->acl[0],
			acc_acl->count * sizeof acl_buf[0]);

		memcpy(&acl_buf[acc_acl->count], &def_acl->acl[0],
			def_acl->count * sizeof acl_buf[0]);

		/*
		 * set the ACL_DEFAULT flag on the default entries
		 */
		for (i = acc_acl->count; i < acl_count; i++) {
			acl_buf[i].a_type |= ACL_DEFAULT;
		}

		sys_acl_free_acl(tmp_acl);

	} else if (type != SMB_ACL_TYPE_ACCESS) {
		errno = EINVAL;
		return -1;
	}

	ret = acl(name, SETACL, acl_count, acl_p);

	SAFE_FREE(acl_buf);

	return ret;
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T acl_d)
{
	if (acl_sort(acl_d) != 0) {
		return -1;
	}

	return facl(fd, SETACL, acl_d->count, &acl_d->acl[0]);
}
#endif

int sys_acl_delete_def_file(const char *path)
{
	SMB_ACL_T	acl_d;
	int		ret;

	/*
	 * fetching the access ACL and rewriting it has
	 * the effect of deleting the default ACL
	 */
	if ((acl_d = sys_acl_get_file(path, SMB_ACL_TYPE_ACCESS)) == NULL) {
		return -1;
	}

	ret = acl(path, SETACL, acl_d->count, acl_d->acl);

	sys_acl_free_acl(acl_d);
	
	return ret;
}

int sys_acl_free_acl(SMB_ACL_T acl_d) 
{
	SAFE_FREE(acl_d);
	return 0;
}

#elif defined(HAVE_HPUX_ACLS) /*---------------------------------------------*/

#ifdef HAVE_DL_H
#include <dl.h>
#endif

/*
 * Based on the Solaris/SCO code - with modifications.
 */

/*
 * Note that while this code implements sufficient functionality
 * to support the sys_acl_* interfaces it does not provide all
 * of the semantics of the POSIX ACL interfaces.
 *
 * In particular, an ACL entry descriptor (SMB_ACL_ENTRY_T) returned
 * from a call to sys_acl_get_entry() should not be assumed to be
 * valid after calling any of the following functions, which may
 * reorder the entries in the ACL.
 *
 *	sys_acl_valid()
 *	sys_acl_set_file()
 *	sys_acl_set_fd()
 */

/* This checks if the POSIX ACL system call is defined */
/* which basically corresponds to whether JFS 3.3 or   */
/* higher is installed. If acl() was called when it    */
/* isn't defined, it causes the process to core dump   */
/* so it is important to check this and avoid acl()    */
/* calls if it isn't there.                            */

#ifdef __TANDEM
inline int do_acl(const char *path_p, int cmd, int nentries, struct acl *aclbufp)
{
	return acl((char*)path_p, cmd, nentries, aclbufp);
}
#define acl(p,c,n,a) do_acl(p,c,n,a)
#endif

static BOOL hpux_acl_call_presence(void)
{
#ifndef __TANDEM
	shl_t handle = NULL;
	void *value;
	int ret_val=0;
	static BOOL already_checked=0;

	if (already_checked)
		return True;

	ret_val = shl_findsym(&handle, "acl", TYPE_PROCEDURE, &value);

	if (ret_val != 0) {
		DEBUG(5, ("hpux_acl_call_presence: shl_findsym() returned %d, errno = %d, error %s\n",
			ret_val, errno, strerror(errno)));
		DEBUG(5, ("hpux_acl_call_presence: acl() system call is not present. Check if you have JFS 3.3 and above?\n"));
		return False;
	}

	DEBUG(10, ("hpux_acl_call_presence: acl() system call is present. We have JFS 3.3 or above \n"));

	already_checked = True;
#endif
	return True;
}

int sys_acl_get_entry(SMB_ACL_T acl_d, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	if (entry_id != SMB_ACL_FIRST_ENTRY && entry_id != SMB_ACL_NEXT_ENTRY) {
		errno = EINVAL;
		return -1;
	}

	if (entry_p == NULL) {
		errno = EINVAL;
		return -1;
	}

	if (entry_id == SMB_ACL_FIRST_ENTRY) {
		acl_d->next = 0;
	}

	if (acl_d->next < 0) {
		errno = EINVAL;
		return -1;
	}

	if (acl_d->next >= acl_d->count) {
		return 0;
	}

	*entry_p = &acl_d->acl[acl_d->next++];

	return 1;
}

int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *type_p)
{
	*type_p = entry_d->a_type;

	return 0;
}

/*
 * There is no way of knowing what size the ACL returned by
 * ACL_GET will be unless you first call ACL_CNT which means
 * making an additional system call.
 *
 * In the hope of avoiding the cost of the additional system
 * call in most cases, we initially allocate enough space for
 * an ACL with INITIAL_ACL_SIZE entries. If this turns out to
 * be too small then we use ACL_CNT to find out the actual
 * size, reallocate the ACL buffer, and then call ACL_GET again.
 */

#define	INITIAL_ACL_SIZE	16

#ifndef NACLENTRIES
#define NACLENTRIES 0
#endif

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	SMB_ACL_T	acl_d;
	int		count;		/* # of ACL entries allocated	*/
	int		naccess;	/* # of access ACL entries	*/
	int		ndefault;	/* # of default ACL entries	*/

	if (hpux_acl_call_presence() == False) {
		/* Looks like we don't have the acl() system call on HPUX. 
		 * May be the system doesn't have the latest version of JFS.
		 */
		return NULL; 
	}

	if (type != SMB_ACL_TYPE_ACCESS && type != SMB_ACL_TYPE_DEFAULT) {
		errno = EINVAL;
		return NULL;
	}

	count = INITIAL_ACL_SIZE;
	if ((acl_d = sys_acl_init(count)) == NULL) {
		return NULL;
	}

	/*
	 * If there isn't enough space for the ACL entries we use
	 * ACL_CNT to determine the actual number of ACL entries
	 * reallocate and try again. This is in a loop because it
	 * is possible that someone else could modify the ACL and
	 * increase the number of entries between the call to
	 * ACL_CNT and the call to ACL_GET.
	 */
	while ((count = acl(path_p, ACL_GET, count, &acl_d->acl[0])) < 0 && errno == ENOSPC) {

		sys_acl_free_acl(acl_d);

		if ((count = acl(path_p, ACL_CNT, NACLENTRIES, NULL)) < 0) {
			return NULL;
		}

		if ((acl_d = sys_acl_init(count)) == NULL) {
			return NULL;
		}
	}

	if (count < 0) {
		sys_acl_free_acl(acl_d);
		return NULL;
	}

	/*
	 * calculate the number of access and default ACL entries
	 *
	 * Note: we assume that the acl() system call returned a
	 * well formed ACL which is sorted so that all of the
	 * access ACL entries precede any default ACL entries
	 */
	for (naccess = 0; naccess < count; naccess++) {
		if (acl_d->acl[naccess].a_type & ACL_DEFAULT)
			break;
	}
	ndefault = count - naccess;
	
	/*
	 * if the caller wants the default ACL we have to copy
	 * the entries down to the start of the acl[] buffer
	 * and mask out the ACL_DEFAULT flag from the type field
	 */
	if (type == SMB_ACL_TYPE_DEFAULT) {
		int	i, j;

		for (i = 0, j = naccess; i < ndefault; i++, j++) {
			acl_d->acl[i] = acl_d->acl[j];
			acl_d->acl[i].a_type &= ~ACL_DEFAULT;
		}

		acl_d->count = ndefault;
	} else {
		acl_d->count = naccess;
	}

	return acl_d;
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	/*
	 * HPUX doesn't have the facl call. Fake it using the path.... JRA.
	 */

	files_struct *fsp = file_find_fd(fd);

	if (fsp == NULL) {
		errno = EBADF;
		return NULL;
	}

	/*
	 * We know we're in the same conn context. So we
	 * can use the relative path.
	 */

	return sys_acl_get_file(fsp->fsp_name, SMB_ACL_TYPE_ACCESS);
}
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	*tag_type_p = entry->a_type;

	*bits_p = entry->a_perm;

	if (*tag_type_p == SMB_ACL_USER || *tag_type_p == SMB_ACL_GROUP)
		*u_g_id_p = entry->a_id;

	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	SMB_ACL_T	a;

	if (count < 0) {
		errno = EINVAL;
		return NULL;
	}

	/*
	 * note that since the definition of the structure pointed
	 * to by the SMB_ACL_T includes the first element of the
	 * acl[] array, this actually allocates an ACL with room
	 * for (count+1) entries
	 */
	if ((a = (SMB_ACL_T)SMB_MALLOC(sizeof a[0] + count * sizeof (struct acl))) == NULL) {
		errno = ENOMEM;
		return NULL;
	}

	a->size = count + 1;
	a->count = 0;
	a->next = -1;

	return a;
}


int sys_acl_create_entry(SMB_ACL_T *acl_p, SMB_ACL_ENTRY_T *entry_p)
{
	SMB_ACL_T	acl_d;
	SMB_ACL_ENTRY_T	entry_d;

	if (acl_p == NULL || entry_p == NULL || (acl_d = *acl_p) == NULL) {
		errno = EINVAL;
		return -1;
	}

	if (acl_d->count >= acl_d->size) {
		errno = ENOSPC;
		return -1;
	}

	entry_d		= &acl_d->acl[acl_d->count++];
	entry_d->a_type	= 0;
	entry_d->a_id	= -1;
	entry_d->a_perm	= 0;
	*entry_p	= entry_d;

	return 0;
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	entry->a_type = tag_type;

	if (tag_type == SMB_ACL_USER || tag_type == SMB_ACL_GROUP)
		entry->a_id = u_g_id;

	entry->a_perm = bits;

	return 0;
}

int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry_d, uint32 bits)
{
	entry_d->a_perm = bits;

	return 0;
}

/* Structure to capture the count for each type of ACE. */

struct hpux_acl_types {
	int n_user;
	int n_def_user;
	int n_user_obj;
	int n_def_user_obj;

	int n_group;
	int n_def_group;
	int n_group_obj;
	int n_def_group_obj;

	int n_other;
	int n_other_obj;
	int n_def_other_obj;

	int n_class_obj;
	int n_def_class_obj;

	int n_illegal_obj;
};

/* count_obj:
 * Counts the different number of objects in a given array of ACL
 * structures.
 * Inputs:
 *
 * acl_count      - Count of ACLs in the array of ACL structures.
 * aclp           - Array of ACL structures.
 * acl_type_count - Pointer to acl_types structure. Should already be
 *                  allocated.
 * Output: 
 *
 * acl_type_count - This structure is filled up with counts of various 
 *                  acl types.
 */

static void hpux_count_obj(int acl_count, struct acl *aclp, struct hpux_acl_types *acl_type_count)
{
	int i;

	memset(acl_type_count, 0, sizeof (struct hpux_acl_types));

	for (i = 0; i < acl_count; i++) {
		switch (aclp[i].a_type) {
		case USER: 
			acl_type_count->n_user++;
			break;
		case USER_OBJ: 
			acl_type_count->n_user_obj++;
			break;
		case DEF_USER_OBJ: 
			acl_type_count->n_def_user_obj++;
			break;
		case GROUP: 
			acl_type_count->n_group++;
			break;
		case GROUP_OBJ: 
			acl_type_count->n_group_obj++;
			break;
		case DEF_GROUP_OBJ: 
			acl_type_count->n_def_group_obj++;
			break;
		case OTHER_OBJ: 
			acl_type_count->n_other_obj++;
			break;
		case DEF_OTHER_OBJ: 
			acl_type_count->n_def_other_obj++;
			break;
		case CLASS_OBJ:
			acl_type_count->n_class_obj++;
			break;
		case DEF_CLASS_OBJ:
			acl_type_count->n_def_class_obj++;
			break;
		case DEF_USER:
			acl_type_count->n_def_user++;
			break;
		case DEF_GROUP:
			acl_type_count->n_def_group++;
			break;
		default: 
			acl_type_count->n_illegal_obj++;
			break;
		}
	}
}

/* swap_acl_entries:  Swaps two ACL entries. 
 *
 * Inputs: aclp0, aclp1 - ACL entries to be swapped.
 */

static void hpux_swap_acl_entries(struct acl *aclp0, struct acl *aclp1)
{
	struct acl temp_acl;

	temp_acl.a_type = aclp0->a_type;
	temp_acl.a_id = aclp0->a_id;
	temp_acl.a_perm = aclp0->a_perm;

	aclp0->a_type = aclp1->a_type;
	aclp0->a_id = aclp1->a_id;
	aclp0->a_perm = aclp1->a_perm;

	aclp1->a_type = temp_acl.a_type;
	aclp1->a_id = temp_acl.a_id;
	aclp1->a_perm = temp_acl.a_perm;
}

/* prohibited_duplicate_type
 * Identifies if given ACL type can have duplicate entries or 
 * not.
 *
 * Inputs: acl_type - ACL Type.
 *
 * Outputs: 
 *
 * Return.. 
 *
 * True - If the ACL type matches any of the prohibited types.
 * False - If the ACL type doesn't match any of the prohibited types.
 */ 

static BOOL hpux_prohibited_duplicate_type(int acl_type)
{
	switch (acl_type) {
	case USER:
	case GROUP:
	case DEF_USER: 
	case DEF_GROUP:
		return True;
	default:
		return False;
	}
}

/* get_needed_class_perm
 * Returns the permissions of a ACL structure only if the ACL
 * type matches one of the pre-determined types for computing 
 * CLASS_OBJ permissions.
 *
 * Inputs: aclp - Pointer to ACL structure.
 */

static int hpux_get_needed_class_perm(struct acl *aclp)
{
	switch (aclp->a_type) {
	case USER: 
	case GROUP_OBJ: 
	case GROUP: 
	case DEF_USER_OBJ: 
	case DEF_USER:
	case DEF_GROUP_OBJ: 
	case DEF_GROUP:
	case DEF_CLASS_OBJ:
	case DEF_OTHER_OBJ: 
		return aclp->a_perm;
	default: 
		return 0;
	}
}

/* acl_sort for HPUX.
 * Sorts the array of ACL structures as per the description in
 * aclsort man page. Refer to aclsort man page for more details
 *
 * Inputs:
 *
 * acl_count - Count of ACLs in the array of ACL structures.
 * calclass  - If this is not zero, then we compute the CLASS_OBJ
 *             permissions.
 * aclp      - Array of ACL structures.
 *
 * Outputs:
 *
 * aclp     - Sorted array of ACL structures.
 *
 * Outputs:
 *
 * Returns 0 for success -1 for failure. Prints a message to the Samba
 * debug log in case of failure.
 */

static int hpux_acl_sort(int acl_count, int calclass, struct acl *aclp)
{
#if !defined(HAVE_HPUX_ACLSORT)
	/*
	 * The aclsort() system call is available on the latest HPUX General
	 * Patch Bundles. So for HPUX, we developed our version of acl_sort 
	 * function. Because, we don't want to update to a new 
	 * HPUX GR bundle just for aclsort() call.
	 */

	struct hpux_acl_types acl_obj_count;
	int n_class_obj_perm = 0;
	int i, j;
 
	if (!acl_count) {
		DEBUG(10, ("Zero acl count passed. Returning Success\n"));
		return 0;
	}

	if (aclp == NULL) {
		DEBUG(0, ("Null ACL pointer in hpux_acl_sort. Returning Failure. \n"));
		return -1;
	}

	/* Count different types of ACLs in the ACLs array */

	hpux_count_obj(acl_count, aclp, &acl_obj_count);

	/* There should be only one entry each of type USER_OBJ, GROUP_OBJ, 
	 * CLASS_OBJ and OTHER_OBJ 
	 */

	if (acl_obj_count.n_user_obj != 1
	 || acl_obj_count.n_group_obj != 1
	 || acl_obj_count.n_class_obj != 1
	 || acl_obj_count.n_other_obj != 1) {
		DEBUG(0, ("hpux_acl_sort: More than one entry or no entries for \
USER OBJ or GROUP_OBJ or OTHER_OBJ or CLASS_OBJ\n"));
		return -1;
	}

	/* If any of the default objects are present, there should be only
	 * one of them each.
	 */
	if (acl_obj_count.n_def_user_obj > 1 || acl_obj_count.n_def_group_obj > 1
	 || acl_obj_count.n_def_other_obj > 1 || acl_obj_count.n_def_class_obj > 1) {
		DEBUG(0, ("hpux_acl_sort: More than one entry for DEF_CLASS_OBJ \
or DEF_USER_OBJ or DEF_GROUP_OBJ or DEF_OTHER_OBJ\n"));
		return -1;
	}

	/* We now have proper number of OBJ and DEF_OBJ entries. Now sort the acl 
	 * structures.  
	 *
	 * Sorting crieteria - First sort by ACL type. If there are multiple entries of
	 * same ACL type, sort by ACL id.
	 *
	 * I am using the trivial kind of sorting method here because, performance isn't 
	 * really effected by the ACLs feature. More over there aren't going to be more
	 * than 17 entries on HPUX. 
	 */

	for (i = 0; i < acl_count; i++) {
		for (j = i+1; j < acl_count; j++) {
			if (aclp[i].a_type > aclp[j].a_type) {
				/* ACL entries out of order, swap them */
				hpux_swap_acl_entries((aclp+i), (aclp+j));
			} else if (aclp[i].a_type == aclp[j].a_type) {
				/* ACL entries of same type, sort by id */
				if (aclp[i].a_id > aclp[j].a_id) {
					hpux_swap_acl_entries((aclp+i), (aclp+j));
				} else if (aclp[i].a_id == aclp[j].a_id) {
					/* We have a duplicate entry. */
					if (hpux_prohibited_duplicate_type(aclp[i].a_type)) {
						DEBUG(0, ("hpux_acl_sort: Duplicate entry: Type(hex): %x Id: %d\n",
							aclp[i].a_type, aclp[i].a_id));
						return -1;
					}
				}
			}
		}
	}

	/* set the class obj permissions to the computed one. */
	if (calclass) {
		int n_class_obj_index = -1;

		for (i = 0;i < acl_count; i++) {
			n_class_obj_perm |= hpux_get_needed_class_perm((aclp+i));
			if (aclp[i].a_type == CLASS_OBJ)
				n_class_obj_index = i;
		}
		aclp[n_class_obj_index].a_perm = n_class_obj_perm;
	}

	return 0;
#else
	return aclsort(acl_count, calclass, aclp);
#endif
}

/*
 * sort the ACL and check it for validity
 *
 * if it's a minimal ACL with only 4 entries then we
 * need to recalculate the mask permissions to make
 * sure that they are the same as the GROUP_OBJ
 * permissions as required by the UnixWare acl() system call.
 *
 * (note: since POSIX allows minimal ACLs which only contain
 * 3 entries - ie there is no mask entry - we should, in theory,
 * check for this and add a mask entry if necessary - however
 * we "know" that the caller of this interface always specifies
 * a mask so, in practice "this never happens" (tm) - if it *does*
 * happen aclsort() will fail and return an error and someone will
 * have to fix it ...)
 */

static int acl_sort(SMB_ACL_T acl_d)
{
	int fixmask = (acl_d->count <= 4);

	if (hpux_acl_sort(acl_d->count, fixmask, acl_d->acl) != 0) {
		errno = EINVAL;
		return -1;
	}
	return 0;
}
 
int sys_acl_valid(SMB_ACL_T acl_d)
{
	return acl_sort(acl_d);
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T type, SMB_ACL_T acl_d)
{
	struct stat	s;
	struct acl	*acl_p;
	int		acl_count;
	struct acl	*acl_buf	= NULL;
	int		ret;

	if (hpux_acl_call_presence() == False) {
		/* Looks like we don't have the acl() system call on HPUX. 
		 * May be the system doesn't have the latest version of JFS.
		 */
		errno=ENOSYS;
		return -1; 
	}

	if (type != SMB_ACL_TYPE_ACCESS && type != SMB_ACL_TYPE_DEFAULT) {
		errno = EINVAL;
		return -1;
	}

	if (acl_sort(acl_d) != 0) {
		return -1;
	}

	acl_p		= &acl_d->acl[0];
	acl_count	= acl_d->count;

	/*
	 * if it's a directory there is extra work to do
	 * since the acl() system call will replace both
	 * the access ACLs and the default ACLs (if any)
	 */
	if (stat(name, &s) != 0) {
		return -1;
	}
	if (S_ISDIR(s.st_mode)) {
		SMB_ACL_T	acc_acl;
		SMB_ACL_T	def_acl;
		SMB_ACL_T	tmp_acl;
		int		i;

		if (type == SMB_ACL_TYPE_ACCESS) {
			acc_acl = acl_d;
			def_acl = tmp_acl = sys_acl_get_file(name, SMB_ACL_TYPE_DEFAULT);

		} else {
			def_acl = acl_d;
			acc_acl = tmp_acl = sys_acl_get_file(name, SMB_ACL_TYPE_ACCESS);
		}

		if (tmp_acl == NULL) {
			return -1;
		}

		/*
		 * allocate a temporary buffer for the complete ACL
		 */
		acl_count = acc_acl->count + def_acl->count;
		acl_p = acl_buf = SMB_MALLOC_ARRAY(struct acl, acl_count);

		if (acl_buf == NULL) {
			sys_acl_free_acl(tmp_acl);
			errno = ENOMEM;
			return -1;
		}

		/*
		 * copy the access control and default entries into the buffer
		 */
		memcpy(&acl_buf[0], &acc_acl->acl[0],
			acc_acl->count * sizeof acl_buf[0]);

		memcpy(&acl_buf[acc_acl->count], &def_acl->acl[0],
			def_acl->count * sizeof acl_buf[0]);

		/*
		 * set the ACL_DEFAULT flag on the default entries
		 */
		for (i = acc_acl->count; i < acl_count; i++) {
			acl_buf[i].a_type |= ACL_DEFAULT;
		}

		sys_acl_free_acl(tmp_acl);

	} else if (type != SMB_ACL_TYPE_ACCESS) {
		errno = EINVAL;
		return -1;
	}

	ret = acl(name, ACL_SET, acl_count, acl_p);

	if (acl_buf) {
		free(acl_buf);
	}

	return ret;
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T acl_d)
{
	/*
	 * HPUX doesn't have the facl call. Fake it using the path.... JRA.
	 */

	files_struct *fsp = file_find_fd(fd);

	if (fsp == NULL) {
		errno = EBADF;
		return NULL;
	}

	if (acl_sort(acl_d) != 0) {
		return -1;
	}

	/*
	 * We know we're in the same conn context. So we
	 * can use the relative path.
	 */

	return sys_acl_set_file(fsp->fsp_name, SMB_ACL_TYPE_ACCESS, acl_d);
}
#endif

int sys_acl_delete_def_file(const char *path)
{
	SMB_ACL_T	acl_d;
	int		ret;

	/*
	 * fetching the access ACL and rewriting it has
	 * the effect of deleting the default ACL
	 */
	if ((acl_d = sys_acl_get_file(path, SMB_ACL_TYPE_ACCESS)) == NULL) {
		return -1;
	}

	ret = acl(path, ACL_SET, acl_d->count, acl_d->acl);

	sys_acl_free_acl(acl_d);
	
	return ret;
}

int sys_acl_free_acl(SMB_ACL_T acl_d) 
{
	free(acl_d);
	return 0;
}

#elif defined(HAVE_IRIX_ACLS) /*---------------------------------------------*/

int sys_acl_get_entry(SMB_ACL_T acl_d, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	if (entry_id != SMB_ACL_FIRST_ENTRY && entry_id != SMB_ACL_NEXT_ENTRY) {
		errno = EINVAL;
		return -1;
	}

	if (entry_p == NULL) {
		errno = EINVAL;
		return -1;
	}

	if (entry_id == SMB_ACL_FIRST_ENTRY) {
		acl_d->next = 0;
	}

	if (acl_d->next < 0) {
		errno = EINVAL;
		return -1;
	}

	if (acl_d->next >= acl_d->aclp->acl_cnt) {
		return 0;
	}

	*entry_p = &acl_d->aclp->acl_entry[acl_d->next++];

	return 1;
}

int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *type_p)
{
	*type_p = entry_d->ae_tag;

	return 0;
}

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	SMB_ACL_T	a;

	if ((a = SMB_MALLOC_P(struct SMB_ACL_T)) == NULL) {
		errno = ENOMEM;
		return NULL;
	}
	if ((a->aclp = acl_get_file(path_p, type)) == NULL) {
		SAFE_FREE(a);
		return NULL;
	}
	a->next = -1;
	a->freeaclp = True;
	return a;
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	SMB_ACL_T	a;

	if ((a = SMB_MALLOC_P(struct SMB_ACL_T)) == NULL) {
		errno = ENOMEM;
		return NULL;
	}
	if ((a->aclp = acl_get_fd(fd)) == NULL) {
		SAFE_FREE(a);
		return NULL;
	}
	a->next = -1;
	a->freeaclp = True;
	return a;
}
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	*tag_type_p = entry->ae_tag;

	*bits_p = entry->ae_perm;

	if (*tag_type_p == SMB_ACL_USER || *tag_type_p == SMB_ACL_GROUP)
		*u_g_id_p = entry->ae_id;

	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	SMB_ACL_T	a;

	if (count < 0) {
		errno = EINVAL;
		return NULL;
	}

	if ((a = (SMB_ACL_T)SMB_MALLOC(sizeof a[0] + sizeof (struct acl))) == NULL) {
		errno = ENOMEM;
		return NULL;
	}

	a->next = -1;
	a->freeaclp = False;
	a->aclp = (struct acl *)((char *)a + sizeof a[0]);
	a->aclp->acl_cnt = 0;

	return a;
}


int sys_acl_create_entry(SMB_ACL_T *acl_p, SMB_ACL_ENTRY_T *entry_p)
{
	SMB_ACL_T	acl_d;
	SMB_ACL_ENTRY_T	entry_d;

	if (acl_p == NULL || entry_p == NULL || (acl_d = *acl_p) == NULL) {
		errno = EINVAL;
		return -1;
	}

	if (acl_d->aclp->acl_cnt >= ACL_MAX_ENTRIES) {
		errno = ENOSPC;
		return -1;
	}

	entry_d		= &acl_d->aclp->acl_entry[acl_d->aclp->acl_cnt++];
	entry_d->ae_tag	= 0;
	entry_d->ae_id	= 0;
	entry_d->ae_perm	= 0;
	*entry_p	= entry_d;

	return 0;
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	entry->ae_tag = tag_type;

	if (tag_type == SMB_ACL_USER || tag_type == SMB_ACL_GROUP)
		entry->ae_id = u_g_id;

	entry->ae_perm = bits;

	return 0;
}

int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry_d, uint32 bits)
{
	entry_d->ae_perm = bits;

	return 0;
}

int sys_acl_valid(SMB_ACL_T acl_d)
{
	return acl_valid(acl_d->aclp);
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T type, SMB_ACL_T acl_d)
{
	return acl_set_file(name, type, acl_d->aclp);
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T acl_d)
{
	return acl_set_fd(fd, acl_d->aclp);
}
#endif

int sys_acl_delete_def_file(const char *name)
{
	return acl_delete_def_file(name);
}

int sys_acl_free_acl(SMB_ACL_T acl_d) 
{
	if (acl_d->freeaclp) {
		acl_free(acl_d->aclp);
	}
	acl_free(acl_d);
	return 0;
}

#elif defined(HAVE_AIX_ACLS) /*----------------------------------------------*/

/* Donated by Medha Date, mdate@austin.ibm.com, for IBM */

int sys_acl_get_entry(SMB_ACL_T theacl, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	struct acl_entry_link *link;
	int keep_going;

	if (entry_id == SMB_ACL_FIRST_ENTRY)
		theacl->count = 0;
	else if (entry_id != SMB_ACL_NEXT_ENTRY) {
		errno = EINVAL;
		return -1;
	}

	DEBUG(10, ("This is the count: %d\n", theacl->count));

	/* Check if count was previously set to -1. *
	 * If it was, that means we reached the end *
	 * of the acl last time.                    */
	if (theacl->count == -1)
		return 0;

	link = theacl;
	/* To get to the next acl, traverse linked list until index *
	 * of acl matches the count we are keeping.  This count is  *
	 * incremented each time we return an acl entry.            */

	for (keep_going = 0; keep_going < theacl->count; keep_going++)
		link = link->nextp;

	*entry_p =  link->entryp;

#if 0
	{
		struct new_acl_entry *entry = *entry_p;
		DEBUG(10, ("*entry_p is %lx\n", (long)entry));
		DEBUG(10, ("*entry_p->ace_access is %d\n", entry->ace_access));
	}
#endif

	/* Increment count */
	theacl->count++;
	if (link->nextp == NULL)
		theacl->count = -1;

	return 1;
}

int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *tag_type_p)
{
	/* Initialize tag type */

	*tag_type_p = -1;
	DEBUG(10, ("the tagtype is %d\n", entry_d->ace_id->id_type));

	/* Depending on what type of entry we have, *
	 * return tag type.                         */
	switch (entry_d->ace_id->id_type) {
	case ACEID_USER:
		*tag_type_p = SMB_ACL_USER;
		break;
	case ACEID_GROUP:
		*tag_type_p = SMB_ACL_GROUP;
		break;

	case SMB_ACL_USER_OBJ:
	case SMB_ACL_GROUP_OBJ:
	case SMB_ACL_OTHER:
		*tag_type_p = entry_d->ace_id->id_type;
		break;

	default:
		return -1;
	}

	return 0;
}

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	struct acl *file_acl = (struct acl *)NULL;
	struct acl_entry *acl_entry;
	struct new_acl_entry *new_acl_entry;
	struct ace_id *idp;
	struct acl_entry_link *acl_entry_link;
	struct acl_entry_link *acl_entry_link_head;
	int i;
	int rc = 0;

	/* AIX has no DEFAULT */
	if  (type == SMB_ACL_TYPE_DEFAULT) {
#ifdef ENOTSUP
		errno = ENOTSUP;
#else
		errno = ENOSYS;
#endif
		return NULL;
	}

	/* Get the acl using statacl */
 
	DEBUG(10, ("Entering sys_acl_get_file\n"));
	DEBUG(10, ("path_p is %s\n", path_p));

	file_acl = (struct acl *)SMB_MALLOC(BUFSIZ);
 
	if (file_acl == NULL) {
		errno=ENOMEM;
		DEBUG(0, ("Error in AIX sys_acl_get_file: %d\n", errno));
		return NULL;
	}

	memset(file_acl, 0, BUFSIZ);

	rc = statacl((char *)path_p, 0, file_acl, BUFSIZ);
	if (rc == -1) {
		DEBUG(0, ("statacl returned %d with errno %d\n", rc, errno));
		SAFE_FREE(file_acl);
		return NULL;
	}

	DEBUG(10, ("Got facl and returned it\n"));

	/* Point to the first acl entry in the acl */
	acl_entry =  file_acl->acl_ext;

	/* Begin setting up the head of the linked list *
	 * that will be used for the storing the acl    *
	 * in a way that is useful for the posix_acls.c *
	 * code.                                          */

	acl_entry_link_head = acl_entry_link = sys_acl_init(0);
	if (acl_entry_link_head == NULL)
		return NULL;

	acl_entry_link->entryp = SMB_MALLOC_P(struct new_acl_entry);
	if (acl_entry_link->entryp == NULL) {
		SAFE_FREE(file_acl);
		errno = ENOMEM;
		DEBUG(0, ("Error in AIX sys_acl_get_file is %d\n", errno));
		return NULL;
	}

	DEBUG(10, ("acl_entry is %d\n", acl_entry));
	DEBUG(10, ("acl_last(file_acl) id %d\n", acl_last(file_acl)));

	/* Check if the extended acl bit is on.   *
	 * If it isn't, do not show the           *
	 * contents of the acl since AIX intends *
	 * the extended info to remain unused     */

	if (file_acl->acl_mode & S_IXACL){
		/* while we are not pointing to the very end */
		while (acl_entry < acl_last(file_acl)) {
			/* before we malloc anything, make sure this is  */
			/* a valid acl entry and one that we want to map */
			idp = id_nxt(acl_entry->ace_id);
			if ((acl_entry->ace_type == ACC_SPECIFY || acl_entry->ace_type == ACC_PERMIT)
			 && idp != id_last(acl_entry)) {
				acl_entry = acl_nxt(acl_entry);
				continue;
			}

			idp = acl_entry->ace_id;

			/* Check if this is the first entry in the linked list. *
			 * The first entry needs to keep prevp pointing to NULL *
			 * and already has entryp allocated.                  */

			if (acl_entry_link_head->count != 0) {
				acl_entry_link->nextp = SMB_MALLOC_P(struct acl_entry_link);

				if (acl_entry_link->nextp == NULL) {
					SAFE_FREE(file_acl);
					errno = ENOMEM;
					DEBUG(0, ("Error in AIX sys_acl_get_file is %d\n", errno));
					return NULL;
				}

				acl_entry_link->nextp->prevp = acl_entry_link;
				acl_entry_link = acl_entry_link->nextp;
				acl_entry_link->entryp = SMB_MALLOC_P(struct new_acl_entry);
				if (acl_entry_link->entryp == NULL) {
					SAFE_FREE(file_acl);
					errno = ENOMEM;
					DEBUG(0, ("Error in AIX sys_acl_get_file is %d\n", errno));
					return NULL;
				}
				acl_entry_link->nextp = NULL;
			}

			acl_entry_link->entryp->ace_len = acl_entry->ace_len;

			/* Don't really need this since all types are going *
			 * to be specified but, it's better than leaving it 0 */

			acl_entry_link->entryp->ace_type = acl_entry->ace_type;
 
			acl_entry_link->entryp->ace_access = acl_entry->ace_access;
 
			memcpy(acl_entry_link->entryp->ace_id, idp, sizeof (struct ace_id));

			/* The access in the acl entries must be left shifted by *
			 * three bites, because they will ultimately be compared *
			 * to S_IRUSR, S_IWUSR, and S_IXUSR.                  */

			switch (acl_entry->ace_type){
			case ACC_PERMIT:
			case ACC_SPECIFY:
				acl_entry_link->entryp->ace_access = acl_entry->ace_access;
				acl_entry_link->entryp->ace_access <<= 6;
				acl_entry_link_head->count++;
				break;
			case ACC_DENY:
				/* Since there is no way to return a DENY acl entry *
				 * change to PERMIT and then shift.                 */
				DEBUG(10, ("acl_entry->ace_access is %d\n", acl_entry->ace_access));
				acl_entry_link->entryp->ace_access = ~acl_entry->ace_access & 7;
				DEBUG(10, ("acl_entry_link->entryp->ace_access is %d\n", acl_entry_link->entryp->ace_access));
				acl_entry_link->entryp->ace_access <<= 6;
				acl_entry_link_head->count++;
				break;
			default:
				return 0;
			}

			DEBUG(10, ("acl_entry = %d\n", acl_entry));
			DEBUG(10, ("The ace_type is %d\n", acl_entry->ace_type));
 
			acl_entry = acl_nxt(acl_entry);
		}
	} /* end of if enabled */

	/* Since owner, group, other acl entries are not *
	 * part of the acl entries in an acl, they must  *
	 * be dummied up to become part of the list.     */

	for (i = 1; i < 4; i++) {
		DEBUG(10, ("i is %d\n", i));
		if (acl_entry_link_head->count != 0) {
			acl_entry_link->nextp = SMB_MALLOC_P(struct acl_entry_link);
			if (acl_entry_link->nextp == NULL) {
				SAFE_FREE(file_acl);
				errno = ENOMEM;
				DEBUG(0, ("Error in AIX sys_acl_get_file is %d\n", errno));
				return NULL;
			}

			acl_entry_link->nextp->prevp = acl_entry_link;
			acl_entry_link = acl_entry_link->nextp;
			acl_entry_link->entryp = SMB_MALLOC_P(struct new_acl_entry);
			if (acl_entry_link->entryp == NULL) {
				SAFE_FREE(file_acl);
				errno = ENOMEM;
				DEBUG(0, ("Error in AIX sys_acl_get_file is %d\n", errno));
				return NULL;
			}
		}

		acl_entry_link->nextp = NULL;

		new_acl_entry = acl_entry_link->entryp;
		idp = new_acl_entry->ace_id;

		new_acl_entry->ace_len = sizeof (struct acl_entry);
		new_acl_entry->ace_type = ACC_PERMIT;
		idp->id_len = sizeof (struct ace_id);
		DEBUG(10, ("idp->id_len = %d\n", idp->id_len));
		memset(idp->id_data, 0, sizeof (uid_t));

		switch (i) {
		case 2:
			new_acl_entry->ace_access = file_acl->g_access << 6;
			idp->id_type = SMB_ACL_GROUP_OBJ;
			break;

		case 3:
			new_acl_entry->ace_access = file_acl->o_access << 6;
			idp->id_type = SMB_ACL_OTHER;
			break;
 
		case 1:
			new_acl_entry->ace_access = file_acl->u_access << 6;
			idp->id_type = SMB_ACL_USER_OBJ;
			break;
 
		default:
			return NULL;

		}

		acl_entry_link_head->count++;
		DEBUG(10, ("new_acl_entry->ace_access = %d\n", new_acl_entry->ace_access));
	}

	acl_entry_link_head->count = 0;
	SAFE_FREE(file_acl);

	return acl_entry_link_head;
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	struct acl *file_acl = (struct acl *)NULL;
	struct acl_entry *acl_entry;
	struct new_acl_entry *new_acl_entry;
	struct ace_id *idp;
	struct acl_entry_link *acl_entry_link;
	struct acl_entry_link *acl_entry_link_head;
	int i;
	int rc = 0;

	/* Get the acl using fstatacl */
   
	DEBUG(10, ("Entering sys_acl_get_fd\n"));
	DEBUG(10, ("fd is %d\n", fd));
	file_acl = (struct acl *)SMB_MALLOC(BUFSIZ);

	if (file_acl == NULL) {
		errno=ENOMEM;
		DEBUG(0, ("Error in sys_acl_get_fd is %d\n", errno));
		return NULL;
	}

	memset(file_acl, 0, BUFSIZ);

	rc = fstatacl(fd, 0, file_acl, BUFSIZ);
	if (rc == -1) {
		DEBUG(0, ("The fstatacl call returned %d with errno %d\n", rc, errno));
		SAFE_FREE(file_acl);
		return NULL;
	}

	DEBUG(10, ("Got facl and returned it\n"));

	/* Point to the first acl entry in the acl */

	acl_entry =  file_acl->acl_ext;
	/* Begin setting up the head of the linked list *
	 * that will be used for the storing the acl    *
	 * in a way that is useful for the posix_acls.c *
	 * code.                                        */

	acl_entry_link_head = acl_entry_link = sys_acl_init(0);
	if (acl_entry_link_head == NULL){
		SAFE_FREE(file_acl);
		return NULL;
	}

	acl_entry_link->entryp = SMB_MALLOC_P(struct new_acl_entry);

	if (acl_entry_link->entryp == NULL) {
		errno = ENOMEM;
		DEBUG(0, ("Error in sys_acl_get_fd is %d\n", errno));
		SAFE_FREE(file_acl);
		return NULL;
	}

	DEBUG(10, ("acl_entry is %d\n", acl_entry));
	DEBUG(10, ("acl_last(file_acl) id %d\n", acl_last(file_acl)));
 
	/* Check if the extended acl bit is on.   *
	 * If it isn't, do not show the           *
	 * contents of the acl since AIX intends  *
	 * the extended info to remain unused     */
 
	if (file_acl->acl_mode & S_IXACL){
		/* while we are not pointing to the very end */
		while (acl_entry < acl_last(file_acl)) {
			/* before we malloc anything, make sure this is  */
			/* a valid acl entry and one that we want to map */

			idp = id_nxt(acl_entry->ace_id);
			if ((acl_entry->ace_type == ACC_SPECIFY ||
				(acl_entry->ace_type == ACC_PERMIT)) && (idp != id_last(acl_entry))) {
					acl_entry = acl_nxt(acl_entry);
					continue;
			}

			idp = acl_entry->ace_id;
 
			/* Check if this is the first entry in the linked list. *
			 * The first entry needs to keep prevp pointing to NULL *
			 * and already has entryp allocated.                 */

			if (acl_entry_link_head->count != 0) {
				acl_entry_link->nextp = SMB_MALLOC_P(struct acl_entry_link);
				if (acl_entry_link->nextp == NULL) {
					errno = ENOMEM;
					DEBUG(0, ("Error in sys_acl_get_fd is %d\n", errno));
					SAFE_FREE(file_acl);
					return NULL;
				}
				acl_entry_link->nextp->prevp = acl_entry_link;
				acl_entry_link = acl_entry_link->nextp;
				acl_entry_link->entryp = SMB_MALLOC_P(struct new_acl_entry);
				if (acl_entry_link->entryp == NULL) {
					errno = ENOMEM;
					DEBUG(0, ("Error in sys_acl_get_fd is %d\n", errno));
					SAFE_FREE(file_acl);
					return NULL;
				}

				acl_entry_link->nextp = NULL;
			}

			acl_entry_link->entryp->ace_len = acl_entry->ace_len;

			/* Don't really need this since all types are going *
			 * to be specified but, it's better than leaving it 0 */

			acl_entry_link->entryp->ace_type = acl_entry->ace_type;
			acl_entry_link->entryp->ace_access = acl_entry->ace_access;

			memcpy(acl_entry_link->entryp->ace_id, idp, sizeof (struct ace_id));

			/* The access in the acl entries must be left shifted by *
			 * three bites, because they will ultimately be compared *
			 * to S_IRUSR, S_IWUSR, and S_IXUSR.                  */

			switch (acl_entry->ace_type){
			case ACC_PERMIT:
			case ACC_SPECIFY:
				acl_entry_link->entryp->ace_access = acl_entry->ace_access;
				acl_entry_link->entryp->ace_access <<= 6;
				acl_entry_link_head->count++;
				break;
			case ACC_DENY:
				/* Since there is no way to return a DENY acl entry *
				 * change to PERMIT and then shift.                 */
				DEBUG(10, ("acl_entry->ace_access is %d\n", acl_entry->ace_access));
				acl_entry_link->entryp->ace_access = ~acl_entry->ace_access & 7;
				DEBUG(10, ("acl_entry_link->entryp->ace_access is %d\n", acl_entry_link->entryp->ace_access));
				acl_entry_link->entryp->ace_access <<= 6;
				acl_entry_link_head->count++;
				break;
			default:
				return 0;
			}

			DEBUG(10, ("acl_entry = %d\n", acl_entry));
			DEBUG(10, ("The ace_type is %d\n", acl_entry->ace_type));
 
			acl_entry = acl_nxt(acl_entry);
		}
	} /* end of if enabled */

	/* Since owner, group, other acl entries are not *
	 * part of the acl entries in an acl, they must  *
	 * be dummied up to become part of the list.     */

	for (i = 1; i < 4; i++) {
		DEBUG(10, ("i is %d\n", i));
		if (acl_entry_link_head->count != 0){
			acl_entry_link->nextp = SMB_MALLOC_P(struct acl_entry_link);
			if (acl_entry_link->nextp == NULL) {
				errno = ENOMEM;
				DEBUG(0, ("Error in sys_acl_get_fd is %d\n", errno));
				SAFE_FREE(file_acl);
				return NULL;
			}

			acl_entry_link->nextp->prevp = acl_entry_link;
			acl_entry_link = acl_entry_link->nextp;
			acl_entry_link->entryp = SMB_MALLOC_P(struct new_acl_entry);

			if (acl_entry_link->entryp == NULL) {
				SAFE_FREE(file_acl);
				errno = ENOMEM;
				DEBUG(0, ("Error in sys_acl_get_fd is %d\n", errno));
				return NULL;
			}
		}

		acl_entry_link->nextp = NULL;
 
		new_acl_entry = acl_entry_link->entryp;
		idp = new_acl_entry->ace_id;
 
		new_acl_entry->ace_len = sizeof (struct acl_entry);
		new_acl_entry->ace_type = ACC_PERMIT;
		idp->id_len = sizeof (struct ace_id);
		DEBUG(10, ("idp->id_len = %d\n", idp->id_len));
		memset(idp->id_data, 0, sizeof (uid_t));
 
		switch (i) {
		case 2:
			new_acl_entry->ace_access = file_acl->g_access << 6;
			idp->id_type = SMB_ACL_GROUP_OBJ;
			break;
 
		case 3:
			new_acl_entry->ace_access = file_acl->o_access << 6;
			idp->id_type = SMB_ACL_OTHER;
			break;
 
		case 1:
			new_acl_entry->ace_access = file_acl->u_access << 6;
			idp->id_type = SMB_ACL_USER_OBJ;
			break;
 
		default:
			return NULL;
		}
 
		acl_entry_link_head->count++;
		DEBUG(10, ("new_acl_entry->ace_access = %d\n", new_acl_entry->ace_access));
	}

	acl_entry_link_head->count = 0;
	SAFE_FREE(file_acl);
 
	return acl_entry_link_head;
}
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	uint *permset;

	if (sys_acl_get_tag_type(entry, tag_type_p) != 0)
		return -1;

	if (*tag_type_p == SMB_ACL_USER || *tag_type_p == SMB_ACL_GROUP)
		memcpy(u_g_id_p, entry->ace_id->id_data, sizeof (id_t));

	permset = &entry->ace_access;

	DEBUG(10, ("*permset is %d\n", *permset));
	*bits_p = (*permset & S_IRUSR ? 4 : 0)
		| (*permset & S_IWUSR ? 2 : 0)
		| (*permset & S_IXUSR ? 1 : 0);

	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	struct acl_entry_link *theacl = NULL;
 
	if (count < 0) {
		errno = EINVAL;
		return NULL;
	}

	DEBUG(10, ("Entering sys_acl_init\n"));

	theacl = SMB_MALLOC_P(struct acl_entry_link);
	if (theacl == NULL) {
		errno = ENOMEM;
		DEBUG(0, ("Error in sys_acl_init is %d\n", errno));
		return NULL;
	}

	theacl->count = 0;
	theacl->nextp = NULL;
	theacl->prevp = NULL;
	theacl->entryp = NULL;
	DEBUG(10, ("Exiting sys_acl_init\n"));
	return theacl;
}

int sys_acl_create_entry(SMB_ACL_T *pacl, SMB_ACL_ENTRY_T *pentry)
{
	struct acl_entry_link *theacl;
	struct acl_entry_link *acl_entryp;
	struct acl_entry_link *temp_entry = NULL;
	int counting;

	DEBUG(10, ("Entering the sys_acl_create_entry\n"));

	theacl = acl_entryp = *pacl;

	/* Get to the end of the acl before adding entry */

	for (counting = 0; counting < theacl->count; counting++){
		DEBUG(10, ("The acl_entryp is %d\n", acl_entryp));
		temp_entry = acl_entryp;
		acl_entryp = acl_entryp->nextp;
	}

	if (theacl->count != 0){
		temp_entry->nextp = acl_entryp = SMB_MALLOC_P(struct acl_entry_link);
		if (acl_entryp == NULL) {
			errno = ENOMEM;
			DEBUG(0, ("Error in sys_acl_create_entry is %d\n", errno));
			return -1;
		}

		DEBUG(10, ("The acl_entryp is %d\n", acl_entryp));
		acl_entryp->prevp = temp_entry;
		DEBUG(10, ("The acl_entryp->prevp is %d\n", acl_entryp->prevp));
	}

	*pentry = acl_entryp->entryp = SMB_MALLOC_P(struct new_acl_entry);
	if (*pentry == NULL) {
		errno = ENOMEM;
		DEBUG(0, ("Error in sys_acl_create_entry is %d\n", errno));
		return -1;
	}

	memset(*pentry, 0, sizeof (struct new_acl_entry));
	acl_entryp->entryp->ace_len = sizeof (struct acl_entry);
	acl_entryp->entryp->ace_type = ACC_PERMIT;
	acl_entryp->entryp->ace_id->id_len = sizeof (struct ace_id);
	acl_entryp->nextp = NULL;
	theacl->count++;
	DEBUG(10, ("Exiting sys_acl_create_entry\n"));
	return 0;
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	entry->ace_id->id_type = tag_type;
	DEBUG(10, ("The tag type is %d\n", entry->ace_id->id_type));

	if (tag_type == SMB_ACL_USER || tag_type == SMB_ACL_GROUP)
		memcpy(entry->ace_id->id_data, &u_g_id, sizeof (id_t));

	entry->ace_access = bits;
	DEBUG(10, ("entry->ace_access = %d\n", entry->ace_access));

	return 0;
}

int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry, uint32 bits)
{
	DEBUG(10, ("Starting AIX sys_acl_set_permset\n"));
	entry->ace_access = bits;
	DEBUG(10, ("entry->ace_access = %d\n", entry->ace_access));
	DEBUG(10, ("Ending AIX sys_acl_set_permset\n"));
	return 0;
}

int sys_acl_valid(SMB_ACL_T theacl)
{
	int user_obj = 0;
	int group_obj = 0;
	int other_obj = 0;
	struct acl_entry_link *acl_entry;

	for (acl_entry=theacl; acl_entry != NULL; acl_entry = acl_entry->nextp) {
		user_obj += (acl_entry->entryp->ace_id->id_type == SMB_ACL_USER_OBJ);
		group_obj += (acl_entry->entryp->ace_id->id_type == SMB_ACL_GROUP_OBJ);
		other_obj += (acl_entry->entryp->ace_id->id_type == SMB_ACL_OTHER);
	}

	DEBUG(10, ("user_obj=%d, group_obj=%d, other_obj=%d\n", user_obj, group_obj, other_obj));
 
	if (user_obj != 1 || group_obj != 1 || other_obj != 1)
		return -1; 

	return 0;
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T acltype, SMB_ACL_T theacl)
{
	struct acl_entry_link *acl_entry_link = NULL;
	struct acl *file_acl = NULL;
	struct acl *file_acl_temp = NULL;
	struct acl_entry *acl_entry = NULL;
	struct ace_id *ace_id = NULL;
	uint id_type;
	uint user_id;
	uint acl_length;
	uint rc;

	DEBUG(10, ("Entering sys_acl_set_file\n"));
	DEBUG(10, ("File name is %s\n", name));
 
	/* AIX has no default ACL */
	if (acltype == SMB_ACL_TYPE_DEFAULT)
		return 0;

	acl_length = BUFSIZ;
	file_acl = (struct acl *)SMB_MALLOC(BUFSIZ);

	if (file_acl == NULL) {
		errno = ENOMEM;
		DEBUG(0, ("Error in sys_acl_set_file is %d\n", errno));
		return -1;
	}

	memset(file_acl, 0, BUFSIZ);

	file_acl->acl_len = ACL_SIZ;
	file_acl->acl_mode = S_IXACL;

	for (acl_entry_link=theacl; acl_entry_link != NULL; acl_entry_link = acl_entry_link->nextp) {
		acl_entry_link->entryp->ace_access >>= 6;
		id_type = acl_entry_link->entryp->ace_id->id_type;

		switch (id_type) {
		case SMB_ACL_USER_OBJ:
			file_acl->u_access = acl_entry_link->entryp->ace_access;
			continue;
		case SMB_ACL_GROUP_OBJ:
			file_acl->g_access = acl_entry_link->entryp->ace_access;
			continue;
		case SMB_ACL_OTHER:
			file_acl->o_access = acl_entry_link->entryp->ace_access;
			continue;
		case SMB_ACL_MASK:
			continue;
		}

		if ((file_acl->acl_len + sizeof (struct acl_entry)) > acl_length) {
			acl_length += sizeof (struct acl_entry);
			file_acl_temp = (struct acl *)SMB_MALLOC(acl_length);
			if (file_acl_temp == NULL) {
				SAFE_FREE(file_acl);
				errno = ENOMEM;
				DEBUG(0, ("Error in sys_acl_set_file is %d\n", errno));
				return -1;
			}  

			memcpy(file_acl_temp, file_acl, file_acl->acl_len);
			SAFE_FREE(file_acl);
			file_acl = file_acl_temp;
		}

		acl_entry = (struct acl_entry *)((char *)file_acl + file_acl->acl_len);
		file_acl->acl_len += sizeof (struct acl_entry);
		acl_entry->ace_len = acl_entry_link->entryp->ace_len;
		acl_entry->ace_access = acl_entry_link->entryp->ace_access;
 
		/* In order to use this, we'll need to wait until we can get denies */
		/* if (!acl_entry->ace_access && acl_entry->ace_type == ACC_PERMIT)
			acl_entry->ace_type = ACC_SPECIFY; */

		acl_entry->ace_type = ACC_SPECIFY;
 
		ace_id = acl_entry->ace_id;
 
		ace_id->id_type = acl_entry_link->entryp->ace_id->id_type;
		DEBUG(10, ("The id type is %d\n", ace_id->id_type));
		ace_id->id_len = acl_entry_link->entryp->ace_id->id_len;
		memcpy(&user_id, acl_entry_link->entryp->ace_id->id_data, sizeof (uid_t));
		memcpy(acl_entry->ace_id->id_data, &user_id, sizeof (uid_t));
	}

	rc = chacl((char*)name, file_acl, file_acl->acl_len);
	DEBUG(10, ("errno is %d\n", errno));
	DEBUG(10, ("return code is %d\n", rc));
	SAFE_FREE(file_acl);
	DEBUG(10, ("Exiting the sys_acl_set_file\n"));
	return rc;
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T theacl)
{
	struct acl_entry_link *acl_entry_link = NULL;
	struct acl *file_acl = NULL;
	struct acl *file_acl_temp = NULL;
	struct acl_entry *acl_entry = NULL;
	struct ace_id *ace_id = NULL;
	uint id_type;
	uint user_id;
	uint acl_length;
	uint rc;
 
	DEBUG(10, ("Entering sys_acl_set_fd\n"));
	acl_length = BUFSIZ;
	file_acl = (struct acl *)SMB_MALLOC(BUFSIZ);

	if (file_acl == NULL) {
		errno = ENOMEM;
		DEBUG(0, ("Error in sys_acl_set_fd is %d\n", errno));
		return -1;
	}

	memset(file_acl, 0, BUFSIZ);
 
	file_acl->acl_len = ACL_SIZ;
	file_acl->acl_mode = S_IXACL;

	for (acl_entry_link=theacl; acl_entry_link != NULL; acl_entry_link = acl_entry_link->nextp) {
		acl_entry_link->entryp->ace_access >>= 6;
		id_type = acl_entry_link->entryp->ace_id->id_type;
		DEBUG(10, ("The id_type is %d\n", id_type));

		switch (id_type) {
		case SMB_ACL_USER_OBJ:
			file_acl->u_access = acl_entry_link->entryp->ace_access;
			continue;
		case SMB_ACL_GROUP_OBJ:
			file_acl->g_access = acl_entry_link->entryp->ace_access;
			continue;
		case SMB_ACL_OTHER:
			file_acl->o_access = acl_entry_link->entryp->ace_access;
			continue;
		case SMB_ACL_MASK:
			continue;
		}

		if ((file_acl->acl_len + sizeof (struct acl_entry)) > acl_length) {
			acl_length += sizeof (struct acl_entry);
			file_acl_temp = (struct acl *)SMB_MALLOC(acl_length);
			if (file_acl_temp == NULL) {
				SAFE_FREE(file_acl);
				errno = ENOMEM;
				DEBUG(0, ("Error in sys_acl_set_fd is %d\n", errno));
				return -1;
			}

			memcpy(file_acl_temp, file_acl, file_acl->acl_len);
			SAFE_FREE(file_acl);
			file_acl = file_acl_temp;
		}

		acl_entry = (struct acl_entry *)((char *)file_acl + file_acl->acl_len);
		file_acl->acl_len += sizeof (struct acl_entry);
		acl_entry->ace_len = acl_entry_link->entryp->ace_len;
		acl_entry->ace_access = acl_entry_link->entryp->ace_access;
 
		/* In order to use this, we'll need to wait until we can get denies */
		/* if (!acl_entry->ace_access && acl_entry->ace_type == ACC_PERMIT)
			acl_entry->ace_type = ACC_SPECIFY; */
 
		acl_entry->ace_type = ACC_SPECIFY;
 
		ace_id = acl_entry->ace_id;
 
		ace_id->id_type = acl_entry_link->entryp->ace_id->id_type;
		DEBUG(10, ("The id type is %d\n", ace_id->id_type));
		ace_id->id_len = acl_entry_link->entryp->ace_id->id_len;
		memcpy(&user_id, acl_entry_link->entryp->ace_id->id_data, sizeof (uid_t));
		memcpy(ace_id->id_data, &user_id, sizeof (uid_t));
	}
 
	rc = fchacl(fd, file_acl, file_acl->acl_len);
	DEBUG(10, ("errno is %d\n", errno));
	DEBUG(10, ("return code is %d\n", rc));
	SAFE_FREE(file_acl);
	DEBUG(10, ("Exiting sys_acl_set_fd\n"));
	return rc;
}
#endif

int sys_acl_delete_def_file(UNUSED(const char *name))
{
	/* AIX has no default ACL */
	return 0;
}

int sys_acl_free_acl(SMB_ACL_T posix_acl)
{
	struct acl_entry_link *acl_entry_link;

	for (acl_entry_link = posix_acl->nextp; acl_entry_link->nextp != NULL; acl_entry_link = acl_entry_link->nextp) {
		SAFE_FREE(acl_entry_link->prevp->entryp);
		SAFE_FREE(acl_entry_link->prevp);
	}

	SAFE_FREE(acl_entry_link->prevp->entryp);
	SAFE_FREE(acl_entry_link->prevp);
	SAFE_FREE(acl_entry_link->entryp);
	SAFE_FREE(acl_entry_link);
 
	return 0;
}

#elif defined(HAVE_OSX_ACLS) /*----------------------------------------------*/

#define OSX_BROKEN_GETENTRY /* returns 0 instead of 1 */

#include <membership.h>

int sys_acl_get_entry(SMB_ACL_T the_acl, int entry_id, SMB_ACL_ENTRY_T *entry_p)
{
	int ret = acl_get_entry(the_acl, entry_id, entry_p);
#ifdef OSX_BROKEN_GETENTRY
	if (ret == 0)
		ret = 1;
	else if (ret == -1 && errno == 22)
		ret = 0;
#endif
	return ret;
}

SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type)
{
	if (type == ACL_TYPE_DEFAULT) {
		errno = ENOTSUP;
		return NULL;
	}
	errno = 0;
	return acl_get_file(path_p, type);
}

#if 0
SMB_ACL_T sys_acl_get_fd(int fd)
{
	return acl_get_fd(fd);
}
#endif

int sys_acl_get_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T *tag_type_p, uint32 *bits_p, id_t *u_g_id_p)
{
	uuid_t *uup;
	acl_tag_t tag;
	acl_flagset_t flagset;
	acl_permset_t permset;
	uint32 bits, fb, bb, pb;
	int id_type = -1;
	int rc;

	if (acl_get_tag_type(entry, &tag) != 0
	 || acl_get_flagset_np(entry, &flagset) != 0
	 || acl_get_permset(entry, &permset) != 0
	 || (uup = acl_get_qualifier(entry)) == NULL)
		return -1;

	rc = mbr_uuid_to_id(*uup, u_g_id_p, &id_type);
	acl_free(uup);
	if (rc != 0)
		return rc;

	if (id_type == ID_TYPE_UID)
		*tag_type_p = SMB_ACL_USER;
	else
		*tag_type_p = SMB_ACL_GROUP;

	bits = tag == ACL_EXTENDED_ALLOW ? 1 : 0;

	for (fb = (1u<<4), bb = (1u<<1); bb < (1u<<12); fb *= 2, bb *= 2) {
		if (acl_get_flag_np(flagset, fb) == 1)
			bits |= bb;
	}

	for (pb = (1u<<1), bb = (1u<<12); bb < (1u<<25); pb *= 2, bb *= 2) {
		if (acl_get_perm_np(permset, pb) == 1)
			bits |= bb;
	}

	*bits_p = bits;

	return 0;
}

SMB_ACL_T sys_acl_init(int count)
{
	return acl_init(count);
}

int sys_acl_create_entry(SMB_ACL_T *pacl, SMB_ACL_ENTRY_T *pentry)
{
	return acl_create_entry(pacl, pentry);
}

int sys_acl_set_info(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tag_type, uint32 bits, id_t u_g_id)
{
	acl_flagset_t flagset;
	acl_permset_t permset;
	uint32 fb, bb, pb;
	int is_user = tag_type == SMB_ACL_USER;
	uuid_t uu;
	int rc;

	tag_type = bits & 1 ? ACL_EXTENDED_ALLOW : ACL_EXTENDED_DENY;

	if (acl_get_flagset_np(entry, &flagset) != 0
	 || acl_get_permset(entry, &permset) != 0)
		return -1;

	acl_clear_flags_np(flagset);
	acl_clear_perms(permset);

	for (fb = (1u<<4), bb = (1u<<1); bb < (1u<<12); fb *= 2, bb *= 2) {
		if (bits & bb)
			acl_add_flag_np(flagset, fb);
	}

	for (pb = (1u<<1), bb = (1u<<12); bb < (1u<<25); pb *= 2, bb *= 2) {
		if (bits & bb)
			acl_add_perm(permset, pb);
	}

	if (is_user)
		rc = mbr_uid_to_uuid(u_g_id, uu);
	else
		rc = mbr_gid_to_uuid(u_g_id, uu);
	if (rc != 0)
		return rc;

	if (acl_set_tag_type(entry, tag_type) != 0
	 || acl_set_qualifier(entry, &uu) != 0
	 || acl_set_permset(entry, permset) != 0
	 || acl_set_flagset_np(entry, flagset) != 0)
		return -1;

	return 0;
}

#if 0
int sys_acl_set_access_bits(SMB_ACL_ENTRY_T entry, uint32 bits)
{
	return -1; /* Not needed for OS X. */
}
#endif

int sys_acl_valid(SMB_ACL_T theacl)
{
	return acl_valid(theacl);
}

int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T acltype, SMB_ACL_T theacl)
{
	return acl_set_file(name, acltype, theacl);
}

#if 0
int sys_acl_set_fd(int fd, SMB_ACL_T theacl)
{
	return acl_set_fd(fd, theacl);
}
#endif

int sys_acl_delete_def_file(const char *name)
{
	return acl_delete_def_file(name);
}

int sys_acl_free_acl(SMB_ACL_T the_acl)
{
	return acl_free(the_acl);
}

#else /* No ACLs. */

#error No ACL functions defined for this platform!

#endif

/************************************************************************
 Deliberately outside the ACL defines. Return 1 if this is a "no acls"
 errno, 0 if not.
************************************************************************/

int no_acl_syscall_error(int err)
{
#ifdef HAVE_OSX_ACLS
	if (err == ENOENT)
		return 1; /* Weird problem with directory ACLs. */
#endif
#if defined(ENOSYS)
	if (err == ENOSYS) {
		return 1;
	}
#endif
#if defined(ENOTSUP)
	if (err == ENOTSUP) {
		return 1;
	}
#endif
	if (err == EINVAL) {
		/* If the type of SMB_ACL_TYPE_ACCESS or SMB_ACL_TYPE_DEFAULT
		 * isn't valid, then the ACLs must be non-POSIX. */
		return 1;
	}
	return 0;
}

#endif /* SUPPORT_ACLS */
