/* (C) 1998 Red Hat Software, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.redhat.com/pub/code/popt */

#ifndef H_POPTINT
#define H_POPTINT

/* Bit mask macros. */
typedef	unsigned int __pbm_bits;
#define	__PBM_NBITS		(8 * sizeof (__pbm_bits))
#define	__PBM_IX(d)		((d) / __PBM_NBITS)
#define __PBM_MASK(d)		((__pbm_bits) 1 << ((d) % __PBM_NBITS))
typedef struct {
    __pbm_bits bits[1];
} pbm_set;
#define	__PBM_BITS(set)	((set)->bits)

#define	PBM_ALLOC(d)	calloc(__PBM_IX (d) + 1, sizeof(__pbm_bits))
#define	PBM_FREE(s)	free(s);
#define PBM_SET(d, s)   (__PBM_BITS (s)[__PBM_IX (d)] |= __PBM_MASK (d))
#define PBM_CLR(d, s)   (__PBM_BITS (s)[__PBM_IX (d)] &= ~__PBM_MASK (d))
#define PBM_ISSET(d, s) ((__PBM_BITS (s)[__PBM_IX (d)] & __PBM_MASK (d)) != 0)

struct optionStackEntry {
    int argc;
    /*@only@*/ const char ** argv;
    /*@only@*/ pbm_set * argb;
    int next;
    /*@only@*/ const char * nextArg;
    /*@keep@*/ const char * nextCharArg;
    /*@dependent@*/ struct poptAlias * currAlias;
    int stuffed;
};

struct execEntry {
    const char * longName;
    char shortName;
    const char * script;
};

struct poptContext_s {
    struct optionStackEntry optionStack[POPT_OPTION_DEPTH];
    /*@dependent@*/ struct optionStackEntry * os;
    /*@owned@*/ const char ** leftovers;
    int numLeftovers;
    int nextLeftover;
    /*@keep@*/ const struct poptOption * options;
    int restLeftover;
    /*@only@*/ const char * appName;
    /*@only@*/ struct poptAlias * aliases;
    int numAliases;
    int flags;
    struct execEntry * execs;
    int numExecs;
    /*@only@*/ const char ** finalArgv;
    int finalArgvCount;
    int finalArgvAlloced;
    /*@dependent@*/ struct execEntry * doExec;
    /*@only@*/ const char * execPath;
    int execAbsolute;
    /*@only@*/ const char * otherHelp;
    pbm_set * arg_strip;
};

#define	xfree(_a)	free((void *)_a)

#ifdef HAVE_LIBINTL_H
#include <libintl.h>
#endif

#if defined(HAVE_GETTEXT) && !defined(__LCLINT__)
#define _(foo) gettext(foo)
#else
#define _(foo) (foo)
#endif

#if defined(HAVE_DGETTEXT) && !defined(__LCLINT__)
#define D_(dom, str) dgettext(dom, str)
#define POPT_(foo) D_("popt", foo)
#else
#define POPT_(foo) (foo)
#define D_(dom, str) (str)
#endif

#define N_(foo) (foo)

#endif
