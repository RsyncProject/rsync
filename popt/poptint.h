/** \ingroup popt
 * @file
 */

/* (C) 1998-2000 Red Hat, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.rpm.org/pub/rpm/dist. */

#ifndef H_POPTINT
#define H_POPTINT

#include <stdint.h>

/**
 * Wrapper to free(3), hides const compilation noise, permit NULL, return NULL.
 * @param p		memory to free
 * @retval		NULL always
 */
static inline void *
_free(const void * p)
{
    if (p != NULL)	free((void *)p);
    return NULL;
}

/* Bit mask macros. */
typedef	unsigned int __pbm_bits;
#define	__PBM_NBITS		(8 * sizeof (__pbm_bits))
#define	__PBM_IX(d)		((d) / __PBM_NBITS)
#define __PBM_MASK(d)		((__pbm_bits) 1 << (((unsigned)(d)) % __PBM_NBITS))
typedef struct {
    __pbm_bits bits[1];
} pbm_set;
#define	__PBM_BITS(set)	((set)->bits)

#define	PBM_ALLOC(d)	calloc(__PBM_IX (d) + 1, sizeof(pbm_set))
#define	PBM_FREE(s)	_free(s);
#define PBM_SET(d, s)   (__PBM_BITS (s)[__PBM_IX (d)] |= __PBM_MASK (d))
#define PBM_CLR(d, s)   (__PBM_BITS (s)[__PBM_IX (d)] &= ~__PBM_MASK (d))
#define PBM_ISSET(d, s) ((__PBM_BITS (s)[__PBM_IX (d)] & __PBM_MASK (d)) != 0)

extern void poptJlu32lpair(const void *key, size_t size,
                uint32_t *pc, uint32_t *pb);

/** \ingroup popt
 * Typedef's for string and array of strings.
 */
typedef const char * poptString;
typedef poptString * poptArgv;

/** \ingroup popt
 * A union to simplify opt->arg access without casting.
 */
typedef union poptArg_u {
    void * ptr;
    int * intp;
    short * shortp;
    long * longp;
    long long * longlongp;
    float * floatp;
    double * doublep;
    const char ** argv;
    poptCallbackType cb;
    poptOption opt;
} poptArg;

extern unsigned int _poptArgMask;
extern unsigned int _poptGroupMask;

#define	poptArgType(_opt)	((_opt)->argInfo & _poptArgMask)
#define	poptGroup(_opt)		((_opt)->argInfo & _poptGroupMask)

#define	F_ISSET(_opt, _FLAG)	((_opt)->argInfo & POPT_ARGFLAG_##_FLAG)
#define	LF_ISSET(_FLAG)		(argInfo & POPT_ARGFLAG_##_FLAG)
#define	CBF_ISSET(_opt, _FLAG)	((_opt)->argInfo & POPT_CBFLAG_##_FLAG)

/* XXX sick hack to preserve pretense of a popt-1.x ABI. */
#define	poptSubstituteHelpI18N(opt) \
  { if ((opt) == poptHelpOptions) (opt) = poptHelpOptionsI18N; }

struct optionStackEntry {
    int argc;
    poptArgv argv;
    pbm_set * argb;
    int next;
    char * nextArg;
    const char * nextCharArg;
    poptItem currAlias;
    int stuffed;
};

struct poptContext_s {
    struct optionStackEntry optionStack[POPT_OPTION_DEPTH];
    struct optionStackEntry * os;
    poptArgv leftovers;
    int numLeftovers;
    int allocLeftovers;
    int nextLeftover;
    const struct poptOption * options;
    int restLeftover;
    const char * appName;
    poptItem aliases;
    int numAliases;
    unsigned int flags;
    poptItem execs;
    int numExecs;
    char * execFail;
    poptArgv finalArgv;
    int finalArgvCount;
    int finalArgvAlloced;
    int (*maincall) (int argc, const char **argv);
    poptItem doExec;
    const char * execPath;
    int execAbsolute;
    const char * otherHelp;
    pbm_set * arg_strip;
};

#if defined(POPT_fprintf)
#define	POPT_dgettext	dgettext
#else
#ifdef HAVE_ICONV
#include <iconv.h>
#endif

#if defined(HAVE_DCGETTEXT)
char *POPT_dgettext(const char * dom, const char * str);
#endif

FORMAT(printf, 2, 3)
int   POPT_fprintf (FILE* stream, const char *format, ...);
#endif	/* !defined(POPT_fprintf) */

const char *POPT_prev_char (const char *str);
const char *POPT_next_char (const char *str);

#endif

#if defined(ENABLE_NLS) && defined(HAVE_LIBINTL_H)
#include <libintl.h>
#endif

#if defined(ENABLE_NLS) && defined(HAVE_GETTEXT)
#define _(foo) gettext(foo)
#else
#define _(foo) foo
#endif

#if defined(ENABLE_NLS) && defined(HAVE_LIBINTL_H) && defined(HAVE_DCGETTEXT)
#define D_(dom, str) POPT_dgettext(dom, str)
#define POPT_(foo) D_("popt", foo)
#else
#define D_(dom, str) str
#define POPT_(foo) foo
#endif

#define N_(foo) foo

