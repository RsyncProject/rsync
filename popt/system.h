#ifdef HAVE_CONFIG_H
#include "config.h"
#endif

#if defined (__GLIBC__) && defined(__LCLINT__)
/*@-declundef@*/
/*@unchecked@*/
extern __const __int32_t *__ctype_tolower;
/*@unchecked@*/
extern __const __int32_t *__ctype_toupper;
/*@=declundef@*/
#endif

#include <ctype.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>

#if HAVE_MCHECK_H 
#include <mcheck.h>
#endif

#include <stdio.h>
#ifdef HAVE_SYS_TYPES_H
# include <sys/types.h>
#endif
#ifdef STDC_HEADERS
# include <stdlib.h>
# include <stddef.h>
#else
# ifdef HAVE_STDLIB_H
#  include <stdlib.h>
# endif
#endif
#ifdef HAVE_STRING_H
# if !defined STDC_HEADERS && defined HAVE_MEMORY_H
#  include <memory.h>
# endif
# include <string.h>
#endif
#ifdef HAVE_STRINGS_H
# include <strings.h>
#endif
#ifdef HAVE_UNISTD_H
# include <unistd.h>
#endif

#ifndef __GNUC__
#define __attribute__(x) 
#endif

#ifdef __NeXT
/* access macros are not declared in non posix mode in unistd.h -
 don't try to use posix on NeXTstep 3.3 ! */
#include <libc.h>
#endif

#if defined(__LCLINT__)
/*@-declundef -incondefs @*/ /* LCL: missing annotation */
/*@only@*/ /*@out@*/
void * alloca (size_t __size)
	/*@ensures MaxSet(result) == (__size - 1) @*/
	/*@*/;
/*@=declundef =incondefs @*/
#endif

/* AIX requires this to be the first thing in the file.  */ 
#ifndef __GNUC__
# if HAVE_ALLOCA_H
#  include <alloca.h>
# else
#  ifdef _AIX
#pragma alloca
#  else
#   ifdef HAVE_ALLOCA
#    ifndef alloca /* predefined by HP cc +Olibcalls */
char *alloca(size_t size);
#    endif
#   else
#    ifdef alloca
#     undef alloca
#    endif
#    define alloca(sz) malloc(sz) /* Kludge this for now */
#   endif
#  endif
# endif
#elif !defined(alloca)
#define alloca __builtin_alloca
#endif

#ifndef HAVE_STRLCPY
size_t strlcpy(char *d, const char *s, size_t bufsize);
#endif

#ifndef HAVE_STRLCAT
size_t strlcat(char *d, const char *s, size_t bufsize);
#endif

#if HAVE_MCHECK_H && defined(__GNUC__)
static inline char *
xstrdup(const char *s)
{
    size_t memsize = strlen(s) + 1;
    char *ptr = malloc(memsize);
    if (!ptr) {
	fprintf(stderr, "virtual memory exhausted.\n");
	exit(EXIT_FAILURE);
    }
    strlcpy(ptr, s, memsize);
    return ptr;
}
#else
#define	xstrdup(_str)	strdup(_str)
#endif  /* HAVE_MCHECK_H && defined(__GNUC__) */

#if HAVE___SECURE_GETENV && !defined(__LCLINT__)
#define	getenv(_s)	__secure_getenv(_s)
#endif

#if !defined HAVE_SNPRINTF || !defined HAVE_C99_VSNPRINTF
#define snprintf rsync_snprintf
int snprintf(char *str,size_t count,const char *fmt,...);
#endif

#define UNUSED(x) x __attribute__((__unused__))

#define PACKAGE "rsync"

#include "popt.h"
