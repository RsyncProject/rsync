#ifdef SUPPORT_ACLS

#ifdef HAVE_SYS_ACL_H
#include <sys/acl.h>
#endif
#ifdef HAVE_ACL_LIBACL_H
#include <acl/libacl.h>
#endif
#include "smb_acls.h"

#define SMB_MALLOC(cnt) new_array(char, cnt)
#define SMB_MALLOC_P(obj) new_array(obj, 1)
#define SMB_MALLOC_ARRAY(obj, cnt) new_array(obj, cnt)
#define SMB_REALLOC(mem, cnt) realloc_array(mem, char, cnt)
#define slprintf snprintf

int sys_acl_get_entry(SMB_ACL_T the_acl, int entry_id, SMB_ACL_ENTRY_T *entry_p);
int sys_acl_get_tag_type(SMB_ACL_ENTRY_T entry_d, SMB_ACL_TAG_T *tag_type_p);
int sys_acl_get_permset(SMB_ACL_ENTRY_T entry_d, SMB_ACL_PERMSET_T *permset_p);
void *sys_acl_get_qualifier(SMB_ACL_ENTRY_T entry_d);
SMB_ACL_T sys_acl_get_file(const char *path_p, SMB_ACL_TYPE_T type);
SMB_ACL_T sys_acl_get_fd(int fd);
int sys_acl_clear_perms(SMB_ACL_PERMSET_T permset);
int sys_acl_add_perm(SMB_ACL_PERMSET_T permset, SMB_ACL_PERM_T perm);
int sys_acl_get_perm(SMB_ACL_PERMSET_T permset, SMB_ACL_PERM_T perm);
char *sys_acl_to_text(SMB_ACL_T the_acl, ssize_t *plen);
SMB_ACL_T sys_acl_init(int count);
int sys_acl_create_entry(SMB_ACL_T *pacl, SMB_ACL_ENTRY_T *pentry);
int sys_acl_set_tag_type(SMB_ACL_ENTRY_T entry, SMB_ACL_TAG_T tagtype);
int sys_acl_set_qualifier(SMB_ACL_ENTRY_T entry, void *qual);
int sys_acl_set_permset(SMB_ACL_ENTRY_T entry, SMB_ACL_PERMSET_T permset);
int sys_acl_valid(SMB_ACL_T theacl);
int sys_acl_set_file(const char *name, SMB_ACL_TYPE_T acltype, SMB_ACL_T theacl);
int sys_acl_set_fd(int fd, SMB_ACL_T theacl);
int sys_acl_delete_def_file(const char *name);
int sys_acl_free_text(char *text);
int sys_acl_free_acl(SMB_ACL_T the_acl);
int sys_acl_free_qualifier(void *qual, SMB_ACL_TAG_T tagtype);

#endif /* SUPPORT_ACLS */
