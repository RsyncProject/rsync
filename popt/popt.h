/* (C) 1998 Red Hat Software, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.redhat.com/pub/code/popt */

#ifndef H_POPT
#define H_POPT

#ifdef __cplusplus
extern "C" {
#endif

#include <stdio.h>			/* for FILE * */

#define POPT_OPTION_DEPTH	10

#define POPT_ARG_NONE		0
#define POPT_ARG_STRING		1
#define POPT_ARG_INT		2
#define POPT_ARG_LONG		3
#define POPT_ARG_INCLUDE_TABLE	4	/* arg points to table */
#define POPT_ARG_CALLBACK	5	/* table-wide callback... must be
					   set first in table; arg points 
					   to callback, descrip points to 
					   callback data to pass */
#define POPT_ARG_INTL_DOMAIN    6       /* set the translation domain
					   for this table and any
					   included tables; arg points
					   to the domain string */
#define POPT_ARG_VAL		7	/* arg should take value val */
#define POPT_ARG_MASK		0x0000FFFF
#define POPT_ARGFLAG_ONEDASH	0x80000000  /* allow -longoption */
#define POPT_ARGFLAG_DOC_HIDDEN 0x40000000  /* don't show in help/usage */
#define POPT_ARGFLAG_STRIP	0x20000000  /* strip this arg from argv (only applies to long args) */
#define POPT_CBFLAG_PRE		0x80000000  /* call the callback before parse */
#define POPT_CBFLAG_POST	0x40000000  /* call the callback after parse */
#define POPT_CBFLAG_INC_DATA	0x20000000  /* use data from the include line,
					       not the subtable */

#define POPT_ERROR_NOARG	-10
#define POPT_ERROR_BADOPT	-11
#define POPT_ERROR_OPTSTOODEEP	-13
#define POPT_ERROR_BADQUOTE	-15	/* only from poptParseArgString() */
#define POPT_ERROR_ERRNO	-16	/* only from poptParseArgString() */
#define POPT_ERROR_BADNUMBER	-17
#define POPT_ERROR_OVERFLOW	-18

/* poptBadOption() flags */
#define POPT_BADOPTION_NOALIAS  (1 << 0)  /* don't go into an alias */

/* poptGetContext() flags */
#define POPT_CONTEXT_NO_EXEC	(1 << 0)  /* ignore exec expansions */
#define POPT_CONTEXT_KEEP_FIRST	(1 << 1)  /* pay attention to argv[0] */
#define POPT_CONTEXT_POSIXMEHARDER (1 << 2) /* options can't follow args */

struct poptOption {
    /*@observer@*/ /*@null@*/ const char * longName;	/* may be NULL */
    char shortName;		/* may be '\0' */
    int argInfo;
    /*@shared@*/ /*@null@*/ void * arg;		/* depends on argInfo */
    int val;			/* 0 means don't return, just update flag */
    /*@shared@*/ /*@null@*/ const char * descrip;	/* description for autohelp -- may be NULL */
    /*@shared@*/ /*@null@*/ const char * argDescrip;	/* argument description for autohelp */
};

struct poptAlias {
    /*@owned@*/ /*@null@*/ const char * longName;	/* may be NULL */
    char shortName;		/* may be '\0' */
    int argc;
    /*@owned@*/ const char ** argv;		/* must be free()able */
};

extern struct poptOption poptHelpOptions[];
#define POPT_AUTOHELP { NULL, '\0', POPT_ARG_INCLUDE_TABLE, poptHelpOptions, \
			0, "Help options", NULL },

typedef struct poptContext_s * poptContext;
#ifndef __cplusplus
typedef struct poptOption * poptOption;
#endif

enum poptCallbackReason { POPT_CALLBACK_REASON_PRE, 
			  POPT_CALLBACK_REASON_POST,
			  POPT_CALLBACK_REASON_OPTION };
typedef void (*poptCallbackType)(poptContext con, 
				 enum poptCallbackReason reason,
			         const struct poptOption * opt,
				 const char * arg, const void * data);

/*@only@*/ poptContext poptGetContext(/*@keep@*/ const char * name,
		int argc, /*@keep@*/ const char ** argv,
		/*@keep@*/ const struct poptOption * options, int flags);
void poptResetContext(poptContext con);

/* returns 'val' element, -1 on last item, POPT_ERROR_* on error */
int poptGetNextOpt(poptContext con);
/* returns NULL if no argument is available */
/*@observer@*/ /*@null@*/ const char * poptGetOptArg(poptContext con);
/* returns NULL if no more options are available */
/*@observer@*/ /*@null@*/ const char * poptGetArg(poptContext con);
/*@observer@*/ /*@null@*/ const char * poptPeekArg(poptContext con);
/*@observer@*/ /*@null@*/ const char ** poptGetArgs(poptContext con);
/* returns the option which caused the most recent error */
/*@observer@*/ const char * poptBadOption(poptContext con, int flags);
void poptFreeContext( /*@only@*/ poptContext con);
int poptStuffArgs(poptContext con, /*@keep@*/ const char ** argv);
int poptAddAlias(poptContext con, struct poptAlias alias, int flags);
int poptReadConfigFile(poptContext con, const char * fn);
/* like above, but reads /etc/popt and $HOME/.popt along with environment 
   vars */
int poptReadDefaultConfig(poptContext con, int useEnv);
/* argv should be freed -- this allows ', ", and \ quoting, but ' is treated
   the same as " and both may include \ quotes */
int poptDupArgv(int argc, const char **argv,
		/*@out@*/ int * argcPtr, /*@out@*/ const char *** argvPtr);
int poptParseArgvString(const char * s,
		/*@out@*/ int * argcPtr, /*@out@*/ const char *** argvPtr);
/*@observer@*/ const char *const poptStrerror(const int error);
void poptSetExecPath(poptContext con, const char * path, int allowAbsolute);
void poptPrintHelp(poptContext con, FILE * f, int flags);
void poptPrintUsage(poptContext con, FILE * f, int flags);
void poptSetOtherOptionHelp(poptContext con, const char * text);
/*@observer@*/ const char * poptGetInvocationName(poptContext con);
/* shuffles argv pointers to remove stripped args, returns new argc */
int poptStrippedArgv(poptContext con, int argc, char **argv);

#ifdef  __cplusplus
}
#endif

#endif
