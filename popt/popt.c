/** \ingroup popt
 * @file
 */

/* (C) 1998-2002 Red Hat, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from
   ftp://ftp.rpm.org/pub/rpm/dist */

#undef	MYDEBUG

#include "system.h"

#include <float.h>
#include <math.h>
#include <unistd.h>
#include <limits.h>
#include <errno.h>

#include "poptint.h"

#ifdef HAVE_STDALIGN_H
#include <stdalign.h>
#define ALIGNOF(x) alignof(x)
#elif defined __GNUC__
#define ALIGNOF(x) __alignof__(x)
#else
#define ALIGNOF(x) sizeof(x)
#endif

#ifdef	MYDEBUG
int _popt_debug = 0;
#endif

unsigned int _poptArgMask = POPT_ARG_MASK;
unsigned int _poptGroupMask = POPT_GROUP_MASK;

#if !defined(HAVE_STRERROR)
static char * strerror(int errno)
{
    extern int sys_nerr;
    extern char * sys_errlist[];

    if ((0 <= errno) && (errno < sys_nerr))
	return sys_errlist[errno];
    else
	return POPT_("unknown errno");
}
#endif

#ifdef MYDEBUG
static void prtcon(const char *msg, poptContext con)
{
    if (msg) fprintf(stderr, "%s", msg);
    fprintf(stderr, "\tcon %p os %p nextCharArg \"%s\" nextArg \"%s\" argv[%d] \"%s\"\n",
	con, con->os,
	(con->os->nextCharArg ? con->os->nextCharArg : ""),
	(con->os->nextArg ? con->os->nextArg : ""),
	con->os->next,
	(con->os->argv && con->os->argv[con->os->next]
		? con->os->argv[con->os->next] : ""));
}
#endif

void poptSetExecPath(poptContext con, const char * path, int allowAbsolute)
{
    con->execPath = _free(con->execPath);
    con->execPath = xstrdup(path);
    con->execAbsolute = allowAbsolute;
    return;
}

static void invokeCallbacksPRE(poptContext con, const struct poptOption * opt)
{
    if (opt != NULL)
    for (; opt->longName || opt->shortName || opt->arg; opt++) {
	poptArg arg = { .ptr = opt->arg };
	if (arg.ptr)
	switch (poptArgType(opt)) {
	case POPT_ARG_INCLUDE_TABLE:	/* Recurse on included sub-tables. */
	    poptSubstituteHelpI18N(arg.opt);	/* XXX side effects */
	    invokeCallbacksPRE(con, arg.opt);
	    break;
	case POPT_ARG_CALLBACK:		/* Perform callback. */
	    if (!CBF_ISSET(opt, PRE))
		break;
	    arg.cb(con, POPT_CALLBACK_REASON_PRE, NULL, NULL, opt->descrip);
	    break;
	}
    }
}

static void invokeCallbacksPOST(poptContext con, const struct poptOption * opt)
{
    if (opt != NULL)
    for (; opt->longName || opt->shortName || opt->arg; opt++) {
	poptArg arg = { .ptr = opt->arg };
	if (arg.ptr)
	switch (poptArgType(opt)) {
	case POPT_ARG_INCLUDE_TABLE:	/* Recurse on included sub-tables. */
	    poptSubstituteHelpI18N(arg.opt);	/* XXX side effects */
	    invokeCallbacksPOST(con, arg.opt);
	    break;
	case POPT_ARG_CALLBACK:		/* Perform callback. */
	    if (!CBF_ISSET(opt, POST))
		break;
	    arg.cb(con, POPT_CALLBACK_REASON_POST, NULL, NULL, opt->descrip);
	    break;
	}
    }
}

static void invokeCallbacksOPTION(poptContext con,
				const struct poptOption * opt,
				const struct poptOption * myOpt,
				const void * myData, int shorty)
{
    const struct poptOption * cbopt = NULL;
    poptArg cbarg = { .ptr = NULL };

    if (opt != NULL)
    for (; opt->longName || opt->shortName || opt->arg; opt++) {
	poptArg arg = { .ptr = opt->arg };
	switch (poptArgType(opt)) {
	case POPT_ARG_INCLUDE_TABLE:	/* Recurse on included sub-tables. */
	    poptSubstituteHelpI18N(arg.opt);	/* XXX side effects */
	    if (opt->arg != NULL)
		invokeCallbacksOPTION(con, opt->arg, myOpt, myData, shorty);
	    break;
	case POPT_ARG_CALLBACK:		/* Save callback info. */
	    if (CBF_ISSET(opt, SKIPOPTION))
		break;
	    cbopt = opt;
	    cbarg.ptr = opt->arg;
	    break;
	default:		/* Perform callback on matching option. */
	    if (cbopt == NULL || cbarg.cb == NULL)
		break;
	    if ((myOpt->shortName && opt->shortName && shorty &&
			myOpt->shortName == opt->shortName)
	     || (myOpt->longName != NULL && opt->longName != NULL &&
			!strcmp(myOpt->longName, opt->longName)))
	    {	const void *cbData = (cbopt->descrip ? cbopt->descrip : myData);
		cbarg.cb(con, POPT_CALLBACK_REASON_OPTION,
			myOpt, con->os->nextArg, cbData);
		/* Terminate (unless explcitly continuing). */
		if (!CBF_ISSET(cbopt, CONTINUE))
		    return;
	    }
	    break;
	}
    }
}

poptContext poptGetContext(const char * name, int argc, const char ** argv,
			const struct poptOption * options, unsigned int flags)
{
    poptContext con = malloc(sizeof(*con));

    if (con == NULL) return NULL;	/* XXX can't happen */
    memset(con, 0, sizeof(*con));

    con->os = con->optionStack;
    con->os->argc = argc;
    con->os->argv = argv;
    con->os->argb = NULL;

    if (!(flags & POPT_CONTEXT_KEEP_FIRST))
	con->os->next = 1;		/* skip argv[0] */

    con->leftovers = calloc( (size_t)(argc + 1), sizeof(*con->leftovers) );
    con->allocLeftovers = argc + 1;
    con->options = options;
    con->aliases = NULL;
    con->numAliases = 0;
    con->flags = flags;
    con->execs = NULL;
    con->numExecs = 0;
    con->execFail = NULL;
    con->finalArgvAlloced = argc * 2;
    con->finalArgv = calloc( (size_t)con->finalArgvAlloced, sizeof(*con->finalArgv) );
    con->execAbsolute = 1;
    con->arg_strip = NULL;

    if (getenv("POSIXLY_CORRECT") || getenv("POSIX_ME_HARDER"))
	con->flags |= POPT_CONTEXT_POSIXMEHARDER;

    if (name)
	con->appName = xstrdup(name);

    invokeCallbacksPRE(con, con->options);

    return con;
}

static void cleanOSE(struct optionStackEntry *os)
{
    os->nextArg = _free(os->nextArg);
    os->argv = _free(os->argv);
    os->argb = PBM_FREE(os->argb);
}

void poptResetContext(poptContext con)
{
    int i;

    if (con == NULL) return;
    while (con->os > con->optionStack) {
	cleanOSE(con->os--);
    }
    con->os->argb = PBM_FREE(con->os->argb);
    con->os->currAlias = NULL;
    con->os->nextCharArg = NULL;
    con->os->nextArg = _free(con->os->nextArg);
    if (!(con->flags & POPT_CONTEXT_KEEP_FIRST))
	con->os->next = 1;		/* skip argv[0] */
    else
	con->os->next = 0;

    for (i = 0; i < con->numLeftovers; i++) {
        con->leftovers[i] = _free(con->leftovers[i]);
    }
    con->numLeftovers = 0;
    con->nextLeftover = 0;
    con->restLeftover = 0;
    con->doExec = NULL;
    con->execFail = _free(con->execFail);

    if (con->finalArgv != NULL)
    for (i = 0; i < con->finalArgvCount; i++) {
	con->finalArgv[i] = _free(con->finalArgv[i]);
    }

    con->finalArgvCount = 0;
    con->arg_strip = PBM_FREE(con->arg_strip);
    return;
}

/* Only one of longName, shortName should be set, not both. */
static int handleExec(poptContext con,
		const char * longName, char shortName)
{
    poptItem item;
    int i;

    if (con->execs == NULL || con->numExecs <= 0) /* XXX can't happen */
	return 0;

    for (i = con->numExecs - 1; i >= 0; i--) {
	item = con->execs + i;
	if (longName && !(item->option.longName &&
			!strcmp(longName, item->option.longName)))
	    continue;
	else if (shortName != item->option.shortName)
	    continue;
	break;
    }
    if (i < 0) return 0;


    if (con->flags & POPT_CONTEXT_NO_EXEC)
	return 1;

    if (con->doExec == NULL) {
	con->doExec = con->execs + i;
	return 1;
    }

    /* We already have an exec to do; remember this option for next
       time 'round */
    if ((con->finalArgvCount + 1) >= (con->finalArgvAlloced)) {
	con->finalArgvAlloced += 10;
	con->finalArgv = realloc(con->finalArgv,
			sizeof(*con->finalArgv) * con->finalArgvAlloced);
    }

    i = con->finalArgvCount++;
    if (con->finalArgv != NULL)	/* XXX can't happen */
    {	char *s  = malloc((longName ? strlen(longName) : 0) + sizeof("--"));
	if (s != NULL) {	/* XXX can't happen */
	    con->finalArgv[i] = s;
	    *s++ = '-';
	    if (longName)
		s = stpcpy( stpcpy(s, "-"), longName);
	    else
		*s++ = shortName;
	    *s = '\0';
	} else
	    con->finalArgv[i] = NULL;
    }

    return 1;
}

/**
 * Compare long option for equality, adjusting for POPT_ARGFLAG_TOGGLE.
 * @param opt           option
 * @param longName	arg option
 * @param longNameLen	arg option length
 * @return		does long option match?
 */
static int
longOptionStrcmp(const struct poptOption * opt,
		const char * longName, size_t longNameLen)
{
    const char * optLongName = opt->longName;
    int rc;

    if (optLongName == NULL || longName == NULL)	/* XXX can't heppen */
	return 0;

    if (F_ISSET(opt, TOGGLE)) {
	if (optLongName[0] == 'n' && optLongName[1] == 'o') {
	    optLongName += sizeof("no") - 1;
	    if (optLongName[0] == '-')
		optLongName++;
	}
	if (longName[0] == 'n' && longName[1] == 'o') {
	    longName += sizeof("no") - 1;
	    longNameLen -= sizeof("no") - 1;
	    if (longName[0] == '-') {
		longName++;
		longNameLen--;
	    }
	}
    }
    rc = (int)(strlen(optLongName) == longNameLen);
    if (rc)
	rc = (int)(strncmp(optLongName, longName, longNameLen) == 0);
    return rc;
}

/* Only one of longName, shortName may be set at a time */
static int handleAlias(poptContext con,
		const char * longName, size_t longNameLen,
		char shortName,
		const char * nextArg)
{
    poptItem item = con->os->currAlias;
    int rc;
    int i;

    if (item) {
	if (longName && item->option.longName != NULL
	 && longOptionStrcmp(&item->option, longName, longNameLen))
	    return 0;
	else
	if (shortName && shortName == item->option.shortName)
	    return 0;
    }

    if (con->aliases == NULL || con->numAliases <= 0) /* XXX can't happen */
	return 0;

    for (i = con->numAliases - 1; i >= 0; i--) {
	item = con->aliases + i;
	if (longName) {
	    if (item->option.longName == NULL)
		continue;
	    if (!longOptionStrcmp(&item->option, longName, longNameLen))
		continue;
	} else if (shortName != item->option.shortName)
	    continue;
	break;
    }
    if (i < 0) return 0;

    if ((con->os - con->optionStack + 1) == POPT_OPTION_DEPTH)
	return POPT_ERROR_OPTSTOODEEP;

    if (longName == NULL && nextArg != NULL && *nextArg != '\0')
	con->os->nextCharArg = nextArg;

    con->os++;
    con->os->next = 0;
    con->os->stuffed = 0;
    con->os->nextArg = NULL;
    con->os->nextCharArg = NULL;
    con->os->currAlias = con->aliases + i;
    {	const char ** av;
	int ac = con->os->currAlias->argc;
	/* Append --foo=bar arg to alias argv array (if present). */ 
	if (longName && nextArg != NULL && *nextArg != '\0') {
	    av = malloc((ac + 1 + 1) * sizeof(*av));
	    if (av != NULL) {	/* XXX won't happen. */
		for (i = 0; i < ac; i++) {
		    av[i] = con->os->currAlias->argv[i];
		}
		av[ac++] = nextArg;
		av[ac] = NULL;
	    } else	/* XXX revert to old popt behavior if malloc fails. */
		av = con->os->currAlias->argv;
	} else
	    av = con->os->currAlias->argv;
	rc = poptDupArgv(ac, av, &con->os->argc, &con->os->argv);
	if (av != NULL && av != con->os->currAlias->argv)
	    free(av);
    }
    con->os->argb = NULL;

    return (rc ? rc : 1);
}

/**
 * Return absolute path to executable by searching PATH.
 * @param argv0		name of executable
 * @return		(malloc'd) absolute path to executable (or NULL)
 */
static
const char * findProgramPath(const char * argv0)
{
    char *path = NULL, *s = NULL, *se;
    char *t = NULL;

    if (argv0 == NULL) return NULL;	/* XXX can't happen */

    /* If there is a / in argv[0], it has to be an absolute path. */
    /* XXX Hmmm, why not if (argv0[0] == '/') ... instead? */
    if (strchr(argv0, '/'))
	return xstrdup(argv0);

    if ((path = getenv("PATH")) == NULL || (path = xstrdup(path)) == NULL)
	return NULL;

    /* The return buffer in t is big enough for any path. */
    if ((t = malloc(strlen(path) + strlen(argv0) + sizeof("/"))) != NULL)
    for (s = path; s && *s; s = se) {

	/* Snip PATH element into [s,se). */
	if ((se = strchr(s, ':')))
	    *se++ = '\0';

	/* Append argv0 to PATH element. */
	(void) stpcpy(stpcpy(stpcpy(t, s), "/"), argv0);

	/* If file is executable, bingo! */
	if (!access(t, X_OK))
	    break;
    }

    /* If no executable was found in PATH, return NULL. */
    if (!(s && *s) && t != NULL)
	t = _free(t);
    path = _free(path);

    return t;
}

static int execCommand(poptContext con)
{
    poptItem item = con->doExec;
    poptArgv argv = NULL;
    int argc = 0;
    int rc;
    int ec = POPT_ERROR_ERRNO;

    if (item == NULL) /*XXX can't happen*/
	return POPT_ERROR_NOARG;

    if (item->argv == NULL || item->argc < 1 ||
	(!con->execAbsolute && strchr(item->argv[0], '/')))
	    return POPT_ERROR_NOARG;

    argv = malloc(sizeof(*argv) *
			(6 + item->argc + con->numLeftovers + con->finalArgvCount));
    if (argv == NULL) return POPT_ERROR_MALLOC;

    if (!strchr(item->argv[0], '/') && con->execPath != NULL) {
	char *s = malloc(strlen(con->execPath) + strlen(item->argv[0]) + sizeof("/"));
	if (s)
	    (void)stpcpy(stpcpy(stpcpy(s, con->execPath), "/"), item->argv[0]);

	argv[argc] = s;
    } else
	argv[argc] = findProgramPath(item->argv[0]);
    if (argv[argc++] == NULL) {
	ec = POPT_ERROR_NOARG;
	goto exit;
    }

    if (item->argc > 1) {
	memcpy(argv + argc, item->argv + 1, sizeof(*argv) * (item->argc - 1));
	argc += (item->argc - 1);
    }

    if (con->finalArgv != NULL && con->finalArgvCount > 0) {
	memcpy(argv + argc, con->finalArgv,
		sizeof(*argv) * con->finalArgvCount);
	argc += con->finalArgvCount;
    }

    if (con->leftovers != NULL && con->numLeftovers > 0) {
	memcpy(argv + argc, con->leftovers, sizeof(*argv) * con->numLeftovers);
	argc += con->numLeftovers;
    }

    argv[argc] = NULL;

#if defined(hpux) || defined(__hpux)
    rc = setresgid(getgid(), getgid(),-1);
    if (rc) goto exit;
    rc = setresuid(getuid(), getuid(),-1);
    if (rc) goto exit;
#else
/*
 * XXX " ... on BSD systems setuid() should be preferred over setreuid()"
 * XXX 	sez' Timur Bakeyev <mc@bat.ru>
 * XXX	from Norbert Warmuth <nwarmuth@privat.circular.de>
 */
#if defined(HAVE_SETUID)
    rc = setgid(getgid());
    if (rc) goto exit;
    rc = setuid(getuid());
    if (rc) goto exit;
#elif defined (HAVE_SETREUID)
    rc = setregid(getgid(), getgid());
    if (rc) goto exit;
    rc = setreuid(getuid(), getuid());
    if (rc) goto exit;
#else
    /* refuse to exec if we cannot drop suid/sgid privileges */
    if (getuid() != geteuid() || getgid() != getegid()) {
	errno = ENOTSUP;
	goto exit;
    }
#endif
#endif

#ifdef	MYDEBUG
if (_popt_debug)
    {	poptArgv avp;
	fprintf(stderr, "==> execvp(%s) argv[%d]:", argv[0], argc);
	for (avp = argv; *avp; avp++)
	    fprintf(stderr, " '%s'", *avp);
	fprintf(stderr, "\n");
    }
#endif

    rc = execvp(argv[0], (char *const *)argv);

    /* only reached on execvp() failure */
    con->execFail = xstrdup(argv[0]);

exit:
    if (argv) {
        if (argv[0])
            free((void *)argv[0]);
        free(argv);
    }
    return ec;
}

static const struct poptOption *
findOption(const struct poptOption * opt,
		const char * longName, size_t longNameLen,
		char shortName,
		poptCallbackType * callback,
		const void ** callbackData,
		unsigned int argInfo)
{
    const struct poptOption * cb = NULL;
    poptArg cbarg = { .ptr = NULL };

    /* This happens when a single - is given */
    if (LF_ISSET(ONEDASH) && !shortName && (longName && *longName == '\0'))
	shortName = '-';

    for (; opt->longName || opt->shortName || opt->arg; opt++) {
	poptArg arg = { .ptr = opt->arg };

	switch (poptArgType(opt)) {
	case POPT_ARG_INCLUDE_TABLE:	/* Recurse on included sub-tables. */
	{   const struct poptOption * opt2;

	    poptSubstituteHelpI18N(arg.opt);	/* XXX side effects */
	    if (arg.ptr == NULL) continue;	/* XXX program error */
	    opt2 = findOption(arg.opt, longName, longNameLen, shortName, callback,
			      callbackData, argInfo);
	    if (opt2 == NULL) continue;
	    /* Sub-table data will be inheirited if no data yet. */
	    if (callback && *callback
	     && callbackData && *callbackData == NULL)
		*callbackData = opt->descrip;
	    return opt2;
	}   break;
	case POPT_ARG_CALLBACK:
	    cb = opt;
	    cbarg.ptr = opt->arg;
	    continue;
	    break;
	default:
	    break;
	}

	if (longName != NULL && opt->longName != NULL &&
		   (!LF_ISSET(ONEDASH) || F_ISSET(opt, ONEDASH)) &&
		   longOptionStrcmp(opt, longName, longNameLen))
	{
	    break;
	} else if (shortName && shortName == opt->shortName) {
	    break;
	}
    }

    if (opt->longName == NULL && !opt->shortName)
	return NULL;

    if (callback)
	*callback = (cb ? cbarg.cb : NULL);
    if (callbackData)
	*callbackData = (cb && !CBF_ISSET(cb, INC_DATA) ? cb->descrip : NULL);

    return opt;
}

static const char * findNextArg(poptContext con,
		unsigned argx, int delete_arg)
{
    struct optionStackEntry * os = con->os;
    const char * arg;

    do {
	int i;
	arg = NULL;
	while (os->next == os->argc && os > con->optionStack) os--;
	if (os->next == os->argc && os == con->optionStack) break;
	if (os->argv != NULL)
	for (i = os->next; i < os->argc; i++) {
	    if (os->argb && PBM_ISSET(i, os->argb))
		continue;
	    if (*os->argv[i] == '-')
		continue;
	    if (--argx > 0)
		continue;
	    arg = os->argv[i];
	    if (delete_arg) {
		if (os->argb == NULL) os->argb = PBM_ALLOC(os->argc);
		if (os->argb != NULL)	/* XXX can't happen */
		    PBM_SET(i, os->argb);
	    }
	    break;
	}
	if (os > con->optionStack) os--;
    } while (arg == NULL);
    return arg;
}

static const char *
expandNextArg(poptContext con, const char * s)
{
    const char * a = NULL;
    char *t, *t_tmp, *te;
    size_t tn = strlen(s) + 1;
    char c;

    te = t = malloc(tn);
    if (t == NULL) return NULL;		/* XXX can't happen */
    *t = '\0';
    while ((c = *s++) != '\0') {
	switch (c) {
#if 0	/* XXX can't do this */
	case '\\':	/* escape */
	    c = *s++;
	    break;
#endif
	case '!':
	    if (!(s[0] == '#' && s[1] == ':' && s[2] == '+'))
		break;
	    /* XXX Make sure that findNextArg deletes only next arg. */
	    if (a == NULL) {
		if ((a = findNextArg(con, 1U, 1)) == NULL)
		    break;
	    }
	    s += sizeof("#:+") - 1;

	    tn += strlen(a);
	    {   size_t pos = (size_t) (te - t);
		if ((t_tmp = realloc(t, tn)) == NULL) {	/* XXX can't happen */
		    free(t);
		    return NULL;
		}
		t = t_tmp;
		te = stpcpy(t + pos, a);
	    }
	    continue;
	    break;
	default:
	    break;
	}
	*te++ = c;
    }
    *te++ = '\0';
    /* If the new string is longer than needed, shorten. */
    if ((t + tn) > te) {
	if ((te = realloc(t, (size_t)(te - t))) == NULL)
	    free(t);
	t = te;
    }
    return t;
}

static void poptStripArg(poptContext con, int which)
{
    if (con->arg_strip == NULL)
	con->arg_strip = PBM_ALLOC(con->optionStack[0].argc);
    if (con->arg_strip != NULL)		/* XXX can't happen */
    PBM_SET(which, con->arg_strip);
    return;
}

unsigned int _poptBitsN = _POPT_BITS_N;
unsigned int _poptBitsM = _POPT_BITS_M;
unsigned int _poptBitsK = _POPT_BITS_K;

static int _poptBitsNew(poptBits *bitsp)
{
    if (bitsp == NULL)
	return POPT_ERROR_NULLARG;

    /* XXX handle negated initialization. */
    if (*bitsp == NULL) {
	if (_poptBitsN == 0) {
	    _poptBitsN = _POPT_BITS_N;
	    _poptBitsM = _POPT_BITS_M;
	}
	if (_poptBitsM == 0U) _poptBitsM = (3 * _poptBitsN) / 2;
	if (_poptBitsK == 0U || _poptBitsK > 32U) _poptBitsK = _POPT_BITS_K;
	*bitsp = PBM_ALLOC(_poptBitsM-1);
    }
    return 0;
}

int poptBitsAdd(poptBits bits, const char * s)
{
    size_t ns = (s ? strlen(s) : 0);
    uint32_t h0 = 0;
    uint32_t h1 = 0;

    if (bits == NULL || ns == 0)
	return POPT_ERROR_NULLARG;

    poptJlu32lpair(s, ns, &h0, &h1);

    for (ns = 0; ns < (size_t)_poptBitsK; ns++) {
        uint32_t h = h0 + ns * h1;
        uint32_t ix = (h % _poptBitsM);
        PBM_SET(ix, bits);
    }
    return 0;
}

int poptBitsChk(poptBits bits, const char * s)
{
    size_t ns = (s ? strlen(s) : 0);
    uint32_t h0 = 0;
    uint32_t h1 = 0;
    int rc = 1;

    if (bits == NULL || ns == 0)
	return POPT_ERROR_NULLARG;

    poptJlu32lpair(s, ns, &h0, &h1);

    for (ns = 0; ns < (size_t)_poptBitsK; ns++) {
        uint32_t h = h0 + ns * h1;
        uint32_t ix = (h % _poptBitsM);
        if (PBM_ISSET(ix, bits))
            continue;
        rc = 0;
        break;
    }
    return rc;
}

int poptBitsClr(poptBits bits)
{
    static size_t nbw = (__PBM_NBITS/8);
    size_t nw = (__PBM_IX(_poptBitsM-1) + 1);

    if (bits == NULL)
	return POPT_ERROR_NULLARG;
    memset(bits, 0, nw * nbw);
    return 0;
}

int poptBitsDel(poptBits bits, const char * s)
{
    size_t ns = (s ? strlen(s) : 0);
    uint32_t h0 = 0;
    uint32_t h1 = 0;

    if (bits == NULL || ns == 0)
	return POPT_ERROR_NULLARG;

    poptJlu32lpair(s, ns, &h0, &h1);

    for (ns = 0; ns < (size_t)_poptBitsK; ns++) {
        uint32_t h = h0 + ns * h1;
        uint32_t ix = (h % _poptBitsM);
        PBM_CLR(ix, bits);
    }
    return 0;
}

int poptBitsIntersect(poptBits *ap, const poptBits b)
{
    __pbm_bits *abits;
    __pbm_bits *bbits;
    __pbm_bits rc = 0;
    size_t nw = (__PBM_IX(_poptBitsM-1) + 1);
    size_t i;

    if (ap == NULL || b == NULL || _poptBitsNew(ap))
	return POPT_ERROR_NULLARG;
    abits = __PBM_BITS(*ap);
    bbits = __PBM_BITS(b);

    for (i = 0; i < nw; i++) {
        abits[i] &= bbits[i];
	rc |= abits[i];
    }
    return (rc ? 1 : 0);
}

int poptBitsUnion(poptBits *ap, const poptBits b)
{
    __pbm_bits *abits;
    __pbm_bits *bbits;
    __pbm_bits rc = 0;
    size_t nw = (__PBM_IX(_poptBitsM-1) + 1);
    size_t i;

    if (ap == NULL || b == NULL || _poptBitsNew(ap))
	return POPT_ERROR_NULLARG;
    abits = __PBM_BITS(*ap);
    bbits = __PBM_BITS(b);

    for (i = 0; i < nw; i++) {
        abits[i] |= bbits[i];
	rc |= abits[i];
    }
    return (rc ? 1 : 0);
}

int poptBitsArgs(poptContext con, poptBits *ap)
{
    const char ** av;
    int rc = 0;

    if (con == NULL || ap == NULL || _poptBitsNew(ap) ||
	con->leftovers == NULL || con->numLeftovers == con->nextLeftover)
	return POPT_ERROR_NULLARG;

    /* some apps like [like RPM ;-) ] need this NULL terminated */
    con->leftovers[con->numLeftovers] = NULL;

    for (av = con->leftovers + con->nextLeftover; *av != NULL; av++) {
	if ((rc = poptBitsAdd(*ap, *av)) != 0)
	    break;
    }
    return rc;
}

int poptSaveBits(poptBits * bitsp,
		UNUSED(unsigned int argInfo), const char * s)
{
    char *tbuf = NULL;
    char *t, *te;
    int rc = 0;

    if (bitsp == NULL || s == NULL || *s == '\0' || _poptBitsNew(bitsp))
	return POPT_ERROR_NULLARG;

    /* Parse comma separated attributes. */
    te = tbuf = xstrdup(s);
    while ((t = te) != NULL && *t) {
	while (*te != '\0' && *te != ',')
	    te++;
	if (*te != '\0')
	    *te++ = '\0';
	/* XXX Ignore empty strings. */
	if (*t == '\0')
	    continue;
	/* XXX Permit negated attributes. caveat emptor: false negatives. */
	if (*t == '!') {
	    t++;
	    if ((rc = poptBitsChk(*bitsp, t)) > 0)
		rc = poptBitsDel(*bitsp, t);
	} else
	    rc = poptBitsAdd(*bitsp, t);
	if (rc)
	    break;
    }
    tbuf = _free(tbuf);
    return rc;
}

int poptSaveString(const char *** argvp,
		UNUSED(unsigned int argInfo), const char * val)
{
    int argc = 0;

    if (argvp == NULL || val == NULL)
	return POPT_ERROR_NULLARG;

    /* XXX likely needs an upper bound on argc. */
    if (*argvp != NULL)
    while ((*argvp)[argc] != NULL)
	argc++;
 
    if ((*argvp = xrealloc(*argvp, (argc + 1 + 1) * sizeof(**argvp))) != NULL) {
	(*argvp)[argc++] = xstrdup(val);
	(*argvp)[argc  ] = NULL;
    }
    return 0;
}

static long long poptRandomValue(long long limit)
{
#if defined(HAVE_SRANDOM)
    static int seed = 1;

    if (seed) {
	srandom((unsigned)getpid());
	srandom((unsigned)random());
	seed = 0;
    }

    return random() % limit + 1;
#else
    /* XXX avoid adding POPT_ERROR_UNIMPLEMENTED to minimize i18n churn. */
    return POPT_ERROR_BADOPERATION;
#endif
}

int poptSaveLongLong(long long * arg, unsigned int argInfo, long long aLongLong)
{
    /* XXX Check alignment, may fail on funky platforms. */
    if (arg == NULL || (((unsigned long)arg) & (ALIGNOF(*arg)-1)))
	return POPT_ERROR_NULLARG;

    if (aLongLong != 0 && LF_ISSET(RANDOM)) {
	aLongLong = poptRandomValue(aLongLong);
	if (aLongLong < 0)
	    return aLongLong;
    }
    if (LF_ISSET(NOT))
	aLongLong = ~aLongLong;
    switch (LF_ISSET(LOGICALOPS)) {
    case 0:
	*arg = aLongLong;
	break;
    case POPT_ARGFLAG_OR:
	*(unsigned long long *)arg |= (unsigned long long)aLongLong;
	break;
    case POPT_ARGFLAG_AND:
	*(unsigned long long *)arg &= (unsigned long long)aLongLong;
	break;
    case POPT_ARGFLAG_XOR:
	*(unsigned long long *)arg ^= (unsigned long long)aLongLong;
	break;
    default:
	return POPT_ERROR_BADOPERATION;
	break;
    }
    return 0;
}

int poptSaveLong(long * arg, unsigned int argInfo, long aLong)
{
    /* XXX Check alignment, may fail on funky platforms. */
    if (arg == NULL || (((unsigned long)arg) & (ALIGNOF(*arg)-1)))
	return POPT_ERROR_NULLARG;

    if (aLong != 0 && LF_ISSET(RANDOM)) {
	aLong = (long)poptRandomValue(aLong);
	if (aLong < 0)
	    return aLong;
    }
    if (LF_ISSET(NOT))
	aLong = ~aLong;
    switch (LF_ISSET(LOGICALOPS)) {
    case 0:		   *arg = aLong; break;
    case POPT_ARGFLAG_OR:  *(unsigned long *)arg |= (unsigned long)aLong; break;
    case POPT_ARGFLAG_AND: *(unsigned long *)arg &= (unsigned long)aLong; break;
    case POPT_ARGFLAG_XOR: *(unsigned long *)arg ^= (unsigned long)aLong; break;
    default:
	return POPT_ERROR_BADOPERATION;
	break;
    }
    return 0;
}

int poptSaveInt(int * arg, unsigned int argInfo, long aLong)
{
    /* XXX Check alignment, may fail on funky platforms. */
    if (arg == NULL || (((unsigned long)arg) & (ALIGNOF(*arg)-1)))
	return POPT_ERROR_NULLARG;

    if (aLong != 0 && LF_ISSET(RANDOM)) {
	aLong = (int)poptRandomValue(aLong);
	if (aLong < 0)
	    return aLong;
    }
    if (LF_ISSET(NOT))
	aLong = ~aLong;
    switch (LF_ISSET(LOGICALOPS)) {
    case 0:		   *arg = (int) aLong;				break;
    case POPT_ARGFLAG_OR:  *(unsigned int *)arg |= (unsigned int) aLong; break;
    case POPT_ARGFLAG_AND: *(unsigned int *)arg &= (unsigned int) aLong; break;
    case POPT_ARGFLAG_XOR: *(unsigned int *)arg ^= (unsigned int) aLong; break;
    default:
	return POPT_ERROR_BADOPERATION;
	break;
    }
    return 0;
}

int poptSaveShort(short * arg, unsigned int argInfo, long aLong)
{
    /* XXX Check alignment, may fail on funky platforms. */
    if (arg == NULL || (((unsigned long)arg) & (ALIGNOF(*arg)-1)))
	return POPT_ERROR_NULLARG;

    if (aLong != 0 && LF_ISSET(RANDOM)) {
	aLong = (short)poptRandomValue(aLong);
	if (aLong < 0)
	    return aLong;
    }
    if (LF_ISSET(NOT))
	aLong = ~aLong;
    switch (LF_ISSET(LOGICALOPS)) {
    case 0:		   *arg = (short) aLong;
	break;
    case POPT_ARGFLAG_OR:  *(unsigned short *)arg |= (unsigned short) aLong;
	break;
    case POPT_ARGFLAG_AND: *(unsigned short *)arg &= (unsigned short) aLong;
	break;
    case POPT_ARGFLAG_XOR: *(unsigned short *)arg ^= (unsigned short) aLong;
	break;
    default: return POPT_ERROR_BADOPERATION;
	break;
    }
    return 0;
}

/**
 * Return argInfo field, handling POPT_ARGFLAG_TOGGLE overrides.
 * @param con		context
 * @param opt           option
 * @return		argInfo
 */
static unsigned int poptArgInfo(poptContext con, const struct poptOption * opt)
{
    unsigned int argInfo = opt->argInfo;

    if (con->os->argv != NULL && con->os->next > 0 && opt->longName != NULL)
    if (LF_ISSET(TOGGLE)) {
	const char * longName = con->os->argv[con->os->next-1];
	while (*longName == '-') longName++;
	/* XXX almost good enough but consider --[no]nofoo corner cases. */
	if (longName[0] != opt->longName[0] || longName[1] != opt->longName[1])
	{
	    if (!LF_ISSET(XOR)) {	/* XXX dont toggle with XOR */
		/* Toggle POPT_BIT_SET <=> POPT_BIT_CLR. */
		if (LF_ISSET(LOGICALOPS))
		    argInfo ^= (POPT_ARGFLAG_OR|POPT_ARGFLAG_AND);
		argInfo ^= POPT_ARGFLAG_NOT;
	    }
	}
    }
    return argInfo;
}

/**
 * Parse an integer expression.
 * @retval *llp		integer expression value
 * @param argInfo	integer expression type
 * @param val		integer expression string
 * @return		0 on success, otherwise POPT_* error.
 */
static int poptParseInteger(long long * llp,
		UNUSED(unsigned int argInfo),
		const char * val)
{
    if (val) {
	char *end = NULL;
	*llp = strtoll(val, &end, 0);

	/* XXX parse scaling suffixes here. */

	if (!(end && *end == '\0'))
	    return POPT_ERROR_BADNUMBER;
    } else
	*llp = 0;
    return 0;
}

/**
 * Save the option argument through the (*opt->arg) pointer.
 * @param con		context
 * @param opt           option
 * @return		0 on success, otherwise POPT_* error.
 */
static int poptSaveArg(poptContext con, const struct poptOption * opt)
{
    poptArg arg = { .ptr = opt->arg };
    int rc = 0;		/* assume success */

    switch (poptArgType(opt)) {
    case POPT_ARG_BITSET:
	/* XXX memory leak, application is responsible for free. */
	rc = poptSaveBits(arg.ptr, opt->argInfo, con->os->nextArg);
	break;
    case POPT_ARG_ARGV:
	/* XXX memory leak, application is responsible for free. */
	rc = poptSaveString(arg.ptr, opt->argInfo, con->os->nextArg);
	break;
    case POPT_ARG_STRING:
	/* XXX memory leak, application is responsible for free. */
	arg.argv[0] = (con->os->nextArg) ? xstrdup(con->os->nextArg) : NULL;
	break;

    case POPT_ARG_INT:
    case POPT_ARG_SHORT:
    case POPT_ARG_LONG:
    case POPT_ARG_LONGLONG:
    {	unsigned int argInfo = poptArgInfo(con, opt);
	long long aNUM = 0;

	if ((rc = poptParseInteger(&aNUM, argInfo, con->os->nextArg)) != 0)
	    break;

	switch (poptArgType(opt)) {
	case POPT_ARG_LONGLONG:
/* XXX let's not demand C99 compiler flags for <limits.h> quite yet. */
#if !defined(LLONG_MAX)
#   define LLONG_MAX    9223372036854775807LL
#   define LLONG_MIN    (-LLONG_MAX - 1LL)
#endif
	    rc = !(aNUM == LLONG_MIN || aNUM == LLONG_MAX)
		? poptSaveLongLong(arg.longlongp, argInfo, aNUM)
		: POPT_ERROR_OVERFLOW;
	    break;
	case POPT_ARG_LONG:
	    rc = !(aNUM < (long long)LONG_MIN || aNUM > (long long)LONG_MAX)
		? poptSaveLong(arg.longp, argInfo, (long)aNUM)
		: POPT_ERROR_OVERFLOW;
	    break;
	case POPT_ARG_INT:
	    rc = !(aNUM < (long long)INT_MIN || aNUM > (long long)INT_MAX)
		? poptSaveInt(arg.intp, argInfo, (long)aNUM)
		: POPT_ERROR_OVERFLOW;
	    break;
	case POPT_ARG_SHORT:
	    rc = !(aNUM < (long long)SHRT_MIN || aNUM > (long long)SHRT_MAX)
		? poptSaveShort(arg.shortp, argInfo, (long)aNUM)
		: POPT_ERROR_OVERFLOW;
	    break;
	}
    }   break;

    case POPT_ARG_FLOAT:
    case POPT_ARG_DOUBLE:
    {	char *end = NULL;
	double aDouble = 0.0;

	if (con->os->nextArg) {
	    int saveerrno = errno;
	    errno = 0;
	    aDouble = strtod(con->os->nextArg, &end);
	    if (errno == ERANGE) {
		rc = POPT_ERROR_OVERFLOW;
		break;
	    }
	    errno = saveerrno;
	    if (*end != '\0') {
		rc = POPT_ERROR_BADNUMBER;
		break;
	    }
	}

	switch (poptArgType(opt)) {
	case POPT_ARG_DOUBLE:
	    arg.doublep[0] = aDouble;
	    break;
	case POPT_ARG_FLOAT:
#define POPT_ABS(a)	((((a) - 0.0) < DBL_EPSILON) ? -(a) : (a))
	    if ((FLT_MIN - POPT_ABS(aDouble)) > DBL_EPSILON
	     || (POPT_ABS(aDouble) - FLT_MAX) > DBL_EPSILON)
		rc = POPT_ERROR_OVERFLOW;
	    else
		arg.floatp[0] = (float) aDouble;
	    break;
	}
    }   break;
    case POPT_ARG_MAINCALL:
	con->maincall = opt->arg;
	break;
    default:
	fprintf(stdout, POPT_("option type (%u) not implemented in popt\n"),
		poptArgType(opt));
	exit(EXIT_FAILURE);
	break;
    }
    return rc;
}

/* returns 'val' element, -1 on last item, POPT_ERROR_* on error */
int poptGetNextOpt(poptContext con)
{
    const struct poptOption * opt = NULL;
    int done = 0;

    if (con == NULL)
	return -1;
    while (!done) {
	const char * origOptString = NULL;
	poptCallbackType cb = NULL;
	const void * cbData = NULL;
	const char * longArg = NULL;
	int canstrip = 0;
	int shorty = 0;

	while (!con->os->nextCharArg && con->os->next == con->os->argc
		&& con->os > con->optionStack) {
	    cleanOSE(con->os--);
	}
	if (!con->os->nextCharArg && con->os->next == con->os->argc) {
	    invokeCallbacksPOST(con, con->options);

	    if (con->maincall) {
		(void) (*con->maincall) (con->finalArgvCount, con->finalArgv);
		return -1;
	    }

	    if (con->doExec) return execCommand(con);
	    return -1;
	}

	/* Process next long option */
	if (!con->os->nextCharArg) {
	    const char * optString;
            size_t optStringLen;
	    int thisopt;

	    if (con->os->argb && PBM_ISSET(con->os->next, con->os->argb)) {
		con->os->next++;
		continue;
	    }
	    thisopt = con->os->next;
	    if (con->os->argv != NULL)	/* XXX can't happen */
	    origOptString = con->os->argv[con->os->next++];

	    if (origOptString == NULL)	/* XXX can't happen */
		return POPT_ERROR_BADOPT;

	    if (con->restLeftover || *origOptString != '-' ||
		(*origOptString == '-' && origOptString[1] == '\0'))
	    {
		if (con->flags & POPT_CONTEXT_POSIXMEHARDER)
		    con->restLeftover = 1;
		if (con->flags & POPT_CONTEXT_ARG_OPTS) {
		    con->os->nextArg = xstrdup(origOptString);
		    return 0;
		}
		if (con->leftovers != NULL) {	/* XXX can't happen */
		    /* One might think we can never overflow the leftovers
		       array.  Actually, that's true, as long as you don't
		       use poptStuffArgs()... */
		    if ((con->numLeftovers + 1) >= (con->allocLeftovers)) {
			con->allocLeftovers += 10;
			con->leftovers =
			    realloc(con->leftovers,
				    sizeof(*con->leftovers) * con->allocLeftovers);
		    }
		    con->leftovers[con->numLeftovers++]
			= xstrdup(origOptString); /* so a free of a stuffed
						     argv doesn't give us a
						     dangling pointer */
		}
		continue;
	    }

	    /* Make a copy we can hack at */
	    optString = origOptString;

	    if (optString[0] == '\0')
		return POPT_ERROR_BADOPT;

	    if (optString[1] == '-' && !optString[2]) {
		con->restLeftover = 1;
		continue;
	    } else {
		const char *oe;
		unsigned int argInfo = 0;

		optString++;
		if (*optString == '-')
		    optString++;
		else
		    argInfo |= POPT_ARGFLAG_ONEDASH;

		/* Check for "--long=arg" option. */
		for (oe = optString; *oe && *oe != '='; oe++)
		    {};
		optStringLen = (size_t)(oe - optString);
		if (*oe == '=')
		    longArg = oe + 1;

		/* XXX aliases with arg substitution need "--alias=arg" */
		if (handleAlias(con, optString, optStringLen, '\0', longArg)) {
		    longArg = NULL;
		    continue;
		}

		if (handleExec(con, optString, '\0'))
		    continue;

		opt = findOption(con->options, optString, optStringLen, '\0', &cb, &cbData,
				 argInfo);
		if (!opt && !LF_ISSET(ONEDASH))
		    return POPT_ERROR_BADOPT;
	    }

	    if (!opt) {
		con->os->nextCharArg = origOptString + 1;
		longArg = NULL;
	    } else {
		if (con->os == con->optionStack && F_ISSET(opt, STRIP))
		{
		    canstrip = 1;
		    poptStripArg(con, thisopt);
		}
		shorty = 0;
	    }
	}

	/* Process next short option */
	if (con->os->nextCharArg) {
	    const char * nextCharArg = con->os->nextCharArg;

	    con->os->nextCharArg = NULL;

	    if (handleAlias(con, NULL, 0, *nextCharArg, nextCharArg + 1))
		continue;

	    if (handleExec(con, NULL, *nextCharArg)) {
		/* Restore rest of short options for further processing */
		nextCharArg++;
		if (*nextCharArg != '\0')
		    con->os->nextCharArg = nextCharArg;
		continue;
	    }

	    opt = findOption(con->options, NULL, 0, *nextCharArg, &cb,
			     &cbData, 0);
	    if (!opt)
		return POPT_ERROR_BADOPT;
	    shorty = 1;

	    nextCharArg++;
	    if (*nextCharArg != '\0')
		con->os->nextCharArg = nextCharArg;
	}

	if (opt == NULL) return POPT_ERROR_BADOPT;	/* XXX can't happen */
	if (poptArgType(opt) == POPT_ARG_NONE || poptArgType(opt) == POPT_ARG_VAL) {
	    if (longArg || (con->os->nextCharArg && con->os->nextCharArg[0] == '='))
		return POPT_ERROR_UNWANTEDARG;
	    if (opt->arg) {
		long val = poptArgType(opt) == POPT_ARG_VAL ? opt->val : 1;
		unsigned int argInfo = poptArgInfo(con, opt);
		if (poptSaveInt((int *)opt->arg, argInfo, val))
		    return POPT_ERROR_BADOPERATION;
	    }
	} else {
	    int rc;

	    con->os->nextArg = _free(con->os->nextArg);
	    if (longArg) {
		longArg = expandNextArg(con, longArg);
		con->os->nextArg = (char *) longArg;
	    } else if (con->os->nextCharArg) {
		longArg = expandNextArg(con, con->os->nextCharArg + (int)(*con->os->nextCharArg == '='));
		con->os->nextArg = (char *) longArg;
		con->os->nextCharArg = NULL;
	    } else {
		while (con->os->next == con->os->argc &&
			con->os > con->optionStack)
		{
		    cleanOSE(con->os--);
		}
		if (con->os->next == con->os->argc) {
		    if (!F_ISSET(opt, OPTIONAL))
			return POPT_ERROR_NOARG;
		    con->os->nextArg = NULL;
		} else {

		    /*
		     * Make sure this isn't part of a short arg or the
		     * result of an alias expansion.
		     */
		    if (con->os == con->optionStack
		     && F_ISSET(opt, STRIP) && canstrip)
		    {
			poptStripArg(con, con->os->next);
		    }
		
		    if (con->os->argv != NULL) {	/* XXX can't happen */
			if (F_ISSET(opt, OPTIONAL) &&
			    con->os->argv[con->os->next][0] == '-') {
			    con->os->nextArg = NULL;
			} else {
			    /* XXX watchout: subtle side-effects live here. */
			    longArg = con->os->argv[con->os->next++];
			    longArg = expandNextArg(con, longArg);
			    con->os->nextArg = (char *) longArg;
			}
		    }
		}
	    }
	    longArg = NULL;

	   /* Save the option argument through a (*opt->arg) pointer. */
	    if (opt->arg != NULL && (rc = poptSaveArg(con, opt)) != 0)
		return rc;
	}

	if (cb)
	    invokeCallbacksOPTION(con, con->options, opt, cbData, shorty);
	else if (opt->val && (poptArgType(opt) != POPT_ARG_VAL))
	    done = 1;

	if ((con->finalArgvCount + 2) >= (con->finalArgvAlloced)) {
	    con->finalArgvAlloced += 10;
	    con->finalArgv = realloc(con->finalArgv,
			    sizeof(*con->finalArgv) * con->finalArgvAlloced);
	}

	if (con->finalArgv != NULL)
	{   char *s = malloc((opt->longName ? strlen(opt->longName) : 0) + sizeof("--"));
	    if (s != NULL) {	/* XXX can't happen */
		con->finalArgv[con->finalArgvCount++] = s;
		*s++ = '-';
		if (opt->longName) {
		    if (!F_ISSET(opt, ONEDASH))
			*s++ = '-';
		    s = stpcpy(s, opt->longName);
		} else {
		    *s++ = opt->shortName;
		    *s = '\0';
		}
	    } else
		con->finalArgv[con->finalArgvCount++] = NULL;
	}

	if (opt->arg && poptArgType(opt) == POPT_ARG_NONE)
	    ;
	else if (poptArgType(opt) == POPT_ARG_VAL)
	    ;
	else if (poptArgType(opt) != POPT_ARG_NONE) {
	    if (con->finalArgv != NULL && con->os->nextArg != NULL)
	        con->finalArgv[con->finalArgvCount++] =
			xstrdup(con->os->nextArg);
	}
    }

    return (opt ? opt->val : -1);	/* XXX can't happen */
}

char * poptGetOptArg(poptContext con)
{
    char * ret = NULL;
    if (con) {
	ret = con->os->nextArg;
	con->os->nextArg = NULL;
    }
    return ret;
}

const char * poptGetArg(poptContext con)
{
    const char * ret = NULL;
    if (con && con->leftovers != NULL && con->nextLeftover < con->numLeftovers)
	ret = con->leftovers[con->nextLeftover++];
    return ret;
}

const char * poptPeekArg(poptContext con)
{
    const char * ret = NULL;
    if (con && con->leftovers != NULL && con->nextLeftover < con->numLeftovers)
	ret = con->leftovers[con->nextLeftover];
    return ret;
}

const char ** poptGetArgs(poptContext con)
{
    if (con == NULL ||
	con->leftovers == NULL || con->numLeftovers == con->nextLeftover)
	return NULL;

    /* some apps like [like RPM ;-) ] need this NULL terminated */
    con->leftovers[con->numLeftovers] = NULL;

    return (con->leftovers + con->nextLeftover);
}

static
poptItem poptFreeItems(poptItem items, int nitems)
{
    if (items != NULL) {
	poptItem item = items;
	while (--nitems >= 0) {
	    item->option.longName = _free(item->option.longName);
	    item->option.descrip = _free(item->option.descrip);
	    item->option.argDescrip = _free(item->option.argDescrip);
	    item->argv = _free(item->argv);
	    item++;
	}
	_free(items);
    }
    return NULL;
}

poptContext poptFreeContext(poptContext con)
{
    int i;

    if (con == NULL) return con;
    poptResetContext(con);

    con->aliases = poptFreeItems(con->aliases, con->numAliases);
    con->numAliases = 0;

    con->execs = poptFreeItems(con->execs, con->numExecs);
    con->numExecs = 0;

    for (i = 0; i < con->numLeftovers; i++) {
        con->leftovers[i] = _free(con->leftovers[i]);
    }
    con->leftovers = _free(con->leftovers);

    con->finalArgv = _free(con->finalArgv);
    con->appName = _free(con->appName);
    con->otherHelp = _free(con->otherHelp);
    con->execPath = _free(con->execPath);
    con->arg_strip = PBM_FREE(con->arg_strip);
    
    con = _free(con);
    return con;
}

int poptAddAlias(poptContext con, struct poptAlias alias,
		UNUSED(int flags))
{
    struct poptItem_s item_buf;
    poptItem item = &item_buf;
    memset(item, 0, sizeof(*item));
    item->option.longName = alias.longName;
    item->option.shortName = alias.shortName;
    item->option.argInfo = POPT_ARGFLAG_DOC_HIDDEN;
    item->option.arg = 0;
    item->option.val = 0;
    item->option.descrip = NULL;
    item->option.argDescrip = NULL;
    item->argc = alias.argc;
    item->argv = alias.argv;
    return poptAddItem(con, item, 0);
}

int poptAddItem(poptContext con, poptItem newItem, int flags)
{
    poptItem * items, item_tmp, item;
    int * nitems;

    switch (flags) {
    case 1:
	items = &con->execs;
	nitems = &con->numExecs;
	break;
    case 0:
	items = &con->aliases;
	nitems = &con->numAliases;
	break;
    default:
	return 1;
	break;
    }

    item_tmp = realloc((*items), ((*nitems) + 1) * sizeof(**items));
    if (item_tmp == NULL)
	return 1;
    *items = item_tmp;

    item = (*items) + (*nitems);

    item->option.longName =
	(newItem->option.longName ? xstrdup(newItem->option.longName) : NULL);
    item->option.shortName = newItem->option.shortName;
    item->option.argInfo = newItem->option.argInfo;
    item->option.arg = newItem->option.arg;
    item->option.val = newItem->option.val;
    item->option.descrip =
	(newItem->option.descrip ? xstrdup(newItem->option.descrip) : NULL);
    item->option.argDescrip =
       (newItem->option.argDescrip ? xstrdup(newItem->option.argDescrip) : NULL);
    item->argc = newItem->argc;
    item->argv = newItem->argv;

    (*nitems)++;

    return 0;
}

const char * poptBadOption(poptContext con, unsigned int flags)
{
    struct optionStackEntry * os = NULL;
    const char *badOpt = NULL;

    if (con != NULL) {
       /* Stupid hack to return something semi-meaningful from exec failure */
       if (con->execFail) {
           badOpt = con->execFail;
       } else {
           os = (flags & POPT_BADOPTION_NOALIAS) ? con->optionStack : con->os;
           badOpt = os->argv[os->next - 1];
       }
    }

    return badOpt;
}

const char * poptStrerror(const int error)
{
    switch (error) {
      case POPT_ERROR_NOARG:
	return POPT_("missing argument");
      case POPT_ERROR_UNWANTEDARG:
	return POPT_("option does not take an argument");
      case POPT_ERROR_BADOPT:
	return POPT_("unknown option");
      case POPT_ERROR_BADOPERATION:
	return POPT_("mutually exclusive logical operations requested");
      case POPT_ERROR_NULLARG:
	return POPT_("opt->arg should not be NULL");
      case POPT_ERROR_OPTSTOODEEP:
	return POPT_("aliases nested too deeply");
      case POPT_ERROR_BADQUOTE:
	return POPT_("error in parameter quoting");
      case POPT_ERROR_BADNUMBER:
	return POPT_("invalid numeric value");
      case POPT_ERROR_OVERFLOW:
	return POPT_("number too large or too small");
      case POPT_ERROR_MALLOC:
	return POPT_("memory allocation failed");
      case POPT_ERROR_BADCONFIG:
	return POPT_("config file failed sanity test");
      case POPT_ERROR_ERRNO:
	return strerror(errno);
      default:
	return POPT_("unknown error");
    }
}

int poptStuffArgs(poptContext con, const char ** argv)
{
    int argc;
    int rc;

    if ((con->os - con->optionStack) == POPT_OPTION_DEPTH)
	return POPT_ERROR_OPTSTOODEEP;

    for (argc = 0; argv[argc]; argc++)
	{};

    con->os++;
    con->os->next = 0;
    con->os->nextArg = NULL;
    con->os->nextCharArg = NULL;
    con->os->currAlias = NULL;
    rc = poptDupArgv(argc, argv, &con->os->argc, &con->os->argv);
    con->os->argb = NULL;
    con->os->stuffed = 1;

    return rc;
}

const char * poptGetInvocationName(poptContext con)
{
    return (con->os->argv ? con->os->argv[0] : "");
}

int poptStrippedArgv(poptContext con, int argc, char ** argv)
{
    int numargs = argc;
    int j = 1;
    int i;
    
    if (con->arg_strip)
    for (i = 1; i < argc; i++) {
	if (PBM_ISSET(i, con->arg_strip))
	    numargs--;
    }
    
    for (i = 1; i < argc; i++) {
	if (con->arg_strip && PBM_ISSET(i, con->arg_strip))
	    continue;
	argv[j] = (j < numargs) ? argv[i] : NULL;
	j++;
    }
    
    return numargs;
}
