#ifdef SUPPORT_XATTRS

#if defined HAVE_ATTR_XATTR_H
#include <attr/xattr.h>
#elif defined HAVE_SYS_XATTR_H
#include <sys/xattr.h>
#elif defined HAVE_SYS_EXTATTR_H
#include <sys/extattr.h>
#endif

/* Linux 2.4 does not define this as a distinct errno value: */
#ifndef ENOATTR
#define ENOATTR ENODATA
#endif

ssize_t sys_lgetxattr(const char *path, const char *name, void *value, size_t size);
ssize_t sys_fgetxattr(int filedes, const char *name, void *value, size_t size);
int sys_lsetxattr(const char *path, const char *name, const void *value, size_t size);
int sys_lremovexattr(const char *path, const char *name);
ssize_t sys_llistxattr(const char *path, char *list, size_t size);

#else

/* No xattrs available */

#endif
