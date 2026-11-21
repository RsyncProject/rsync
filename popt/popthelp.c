/* -*- Mode: C; tab-width: 8; indent-tabs-mode: t; c-basic-offset: 4 -*- */

/** \ingroup popt
 * @file
 */

/* (C) 1998-2002 Red Hat, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.rpm.org/pub/rpm/dist. */

#include "system.h"

#define        POPT_USE_TIOCGWINSZ
#ifdef POPT_USE_TIOCGWINSZ
#include <sys/ioctl.h>
#endif

#ifdef HAVE_MBSRTOWCS
#include <wchar.h>			/* for mbsrtowcs */
#endif
#include "poptint.h"


/**
 * Display arguments.
 * @param con		context
 * @param foo		(unused)
 * @param key		option(s)
 * @param arg		(unused)
 * @param data		(unused)
 */
NORETURN
static void displayArgs(poptContext con,
		UNUSED(enum poptCallbackReason foo),
		struct poptOption * key, 
		UNUSED(const char * arg),
		UNUSED(void * data))
{
    if (key->shortName == '?')
	poptPrintHelp(con, stdout, 0);
    else
	poptPrintUsage(con, stdout, 0);

    poptFreeContext(con);
    exit(0);
}

#ifdef	NOTYET
static int show_option_defaults = 0;
#endif

/**
 * Empty table marker to enable displaying popt alias/exec options.
 */
struct poptOption poptAliasOptions[] = {
    POPT_TABLEEND
};

/**
 * Auto help table options.
 */
struct poptOption poptHelpOptions[] = {
  { NULL, '\0', POPT_ARG_CALLBACK, (void *)displayArgs, 0, NULL, NULL },
  { "help", '?', 0, NULL, (int)'?', N_("Show this help message"), NULL },
  { "usage", '\0', 0, NULL, (int)'u', N_("Display brief usage message"), NULL },
    POPT_TABLEEND
} ;

static struct poptOption poptHelpOptions2[] = {
  { NULL, '\0', POPT_ARG_INTL_DOMAIN, (void *)PACKAGE, 0, NULL, NULL},
  { NULL, '\0', POPT_ARG_CALLBACK, (void *)displayArgs, 0, NULL, NULL },
  { "help", '?', 0, NULL, (int)'?', N_("Show this help message"), NULL },
  { "usage", '\0', 0, NULL, (int)'u', N_("Display brief usage message"), NULL },
#ifdef	NOTYET
  { "defaults", '\0', POPT_ARG_NONE, &show_option_defaults, 0,
	N_("Display option defaults in message"), NULL },
#endif
  { NULL, '\0',	0, NULL, 0, N_("Terminate options"), NULL },
    POPT_TABLEEND
} ;

struct poptOption * poptHelpOptionsI18N = poptHelpOptions2;

#define        _POPTHELP_MAXLINE       ((size_t)79)

typedef struct columns_s {
    size_t cur;
    size_t max;
} * columns_t;

/** 
 * Return no. of columns in output window.
 * @param fp           FILE
 * @return             no. of columns 
 */ 
static size_t maxColumnWidth(FILE *fp)
{   
    size_t maxcols = _POPTHELP_MAXLINE;
#if defined(TIOCGWINSZ)
    struct winsize ws;
    int fdno = fileno(fp ? fp : stdout);

    memset(&ws, 0, sizeof(ws));
    if (fdno >= 0 && !ioctl(fdno, (unsigned long)TIOCGWINSZ, &ws)) {
	size_t ws_col = (size_t)ws.ws_col;
	if (ws_col > maxcols && ws_col < (size_t)256)
	    maxcols = ws_col - 1;
    }
#endif
    return maxcols;
}   

/**
 * Determine number of display characters in a string.
 * @param s		string
 * @return		no. of display characters.
 */
static inline size_t stringDisplayWidth(const char *s)
{
    size_t n = strlen(s);
#ifdef HAVE_MBSRTOWCS
    mbstate_t t;

    memset ((void *)&t, 0, sizeof (t));	/* In initial state.  */
    /* Determine number of display characters.  */
    n = mbsrtowcs (NULL, &s, n, &t);
#else
    n = 0;
    for (; *s; s = POPT_next_char(s))
	n++;
#endif

    return n;
}

/**
 * @param opt		option(s)
 */
static const char *
getTableTranslationDomain(const struct poptOption *opt)
{
    if (opt != NULL)
    for (; opt->longName || opt->shortName || opt->arg; opt++) {
	if (opt->argInfo == POPT_ARG_INTL_DOMAIN)
	    return opt->arg;
    }
    return NULL;
}

/**
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static const char *
getArgDescrip(const struct poptOption * opt,
		/* FIX: i18n macros disabled with lclint */
		const char * translation_domain)
{
    if (!poptArgType(opt)) return NULL;

    if (poptArgType(opt) == POPT_ARG_MAINCALL)
	return opt->argDescrip;
    if (poptArgType(opt) == POPT_ARG_ARGV)
	return opt->argDescrip;

    if (opt->argDescrip) {
	/* Some strings need popt library, not application, i18n domain. */
	if (opt == (poptHelpOptions + 1)
	 || opt == (poptHelpOptions + 2)
	 || !strcmp(opt->argDescrip, N_("Help options:"))
	 || !strcmp(opt->argDescrip, N_("Options implemented via popt alias/exec:")))
	    return POPT_(opt->argDescrip);

	/* Use the application i18n domain. */
	return D_(translation_domain, opt->argDescrip);
    }

    switch (poptArgType(opt)) {
    case POPT_ARG_NONE:		return POPT_("NONE");
#ifdef	DYING
    case POPT_ARG_VAL:		return POPT_("VAL");
#else
    case POPT_ARG_VAL:		return NULL;
#endif
    case POPT_ARG_INT:		return POPT_("INT");
    case POPT_ARG_SHORT:	return POPT_("SHORT");
    case POPT_ARG_LONG:		return POPT_("LONG");
    case POPT_ARG_LONGLONG:	return POPT_("LONGLONG");
    case POPT_ARG_STRING:	return POPT_("STRING");
    case POPT_ARG_FLOAT:	return POPT_("FLOAT");
    case POPT_ARG_DOUBLE:	return POPT_("DOUBLE");
    case POPT_ARG_MAINCALL:	return NULL;
    case POPT_ARG_ARGV:		return NULL;
    default:			return POPT_("ARG");
    }
}

/**
 * Display default value for an option.
 * @param lineLength	display positions remaining
 * @param opt		option(s)
 * @param translation_domain	translation domain
 * @return
 */
static char *
singleOptionDefaultValue(size_t lineLength,
		const struct poptOption * opt,
		/* FIX: i18n macros disabled with lclint */
		const char * translation_domain)
{
    const char * defstr = D_(translation_domain, "default");
    char * le = malloc(4*lineLength + 1);
    char * l = le;

    if (le == NULL) return NULL;	/* XXX can't happen */
    *le = '\0';
    *le++ = '(';
    le = stpcpy(le, defstr);
    *le++ = ':';
    *le++ = ' ';
  if (opt->arg) {	/* XXX programmer error */
    poptArg arg = { .ptr = opt->arg };
    switch (poptArgType(opt)) {
    case POPT_ARG_VAL:
    case POPT_ARG_INT:
	le += sprintf(le, "%d", arg.intp[0]);
	break;
    case POPT_ARG_SHORT:
	le += sprintf(le, "%hd", arg.shortp[0]);
	break;
    case POPT_ARG_LONG:
	le += sprintf(le, "%ld", arg.longp[0]);
	break;
    case POPT_ARG_LONGLONG:
	le += sprintf(le, "%lld", arg.longlongp[0]);
	break;
    case POPT_ARG_FLOAT:
    {	double aDouble = (double) arg.floatp[0];
	le += sprintf(le, "%g", aDouble);
    }	break;
    case POPT_ARG_DOUBLE:
	le += sprintf(le, "%g", arg.doublep[0]);
	break;
    case POPT_ARG_MAINCALL:
	le += sprintf(le, "%p", opt->arg);
	break;
    case POPT_ARG_ARGV:
	le += sprintf(le, "%p", opt->arg);
	break;
    case POPT_ARG_STRING:
    {	const char * s = arg.argv[0];
	if (s == NULL)
	    le = stpcpy(le, "null");
	else {
	    size_t limit = 4*lineLength - (le - l) - sizeof("\"\")");
	    size_t slen;
	    *le++ = '"';
	    strncpy(le, s, limit); le[limit] = '\0'; le += (slen = strlen(le));
	    if (slen == limit && s[limit])
		le[-1] = le[-2] = le[-3] = '.';
	    *le++ = '"';
	}
    }	break;
    case POPT_ARG_NONE:
    default:
	l = _free(l);
	return NULL;
	break;
    }
  }
    *le++ = ')';
    *le = '\0';

    return l;
}

/**
 * Display help text for an option.
 * @param fp		output file handle
 * @param columns	output display width control
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static void singleOptionHelp(FILE * fp, columns_t columns,
		const struct poptOption * opt,
		const char * translation_domain)
{
    size_t maxLeftCol = columns->cur;
    size_t indentLength = maxLeftCol + 5;
    size_t lineLength = columns->max - indentLength;
    const char * help = D_(translation_domain, opt->descrip);
    const char * argDescrip = getArgDescrip(opt, translation_domain);
    /* Display shortName iff printable non-space. */
    int prtshort = (int)(isprint((int)opt->shortName) && opt->shortName != ' ');
    size_t helpLength;
    char * defs = NULL;
    char * left;
    size_t nb = maxLeftCol + 1;
    int displaypad = 0;

    /* Make sure there's more than enough room in target buffer. */
    if (opt->longName)	nb += strlen(opt->longName);
    if (F_ISSET(opt, TOGGLE)) nb += sizeof("[no]") - 1;
    if (argDescrip)	nb += strlen(argDescrip);

    left = malloc(nb);
    if (left == NULL) return;	/* XXX can't happen */
    left[0] = '\0';
    left[maxLeftCol] = '\0';

#define	prtlong	(opt->longName != NULL)	/* XXX splint needs a clue */
    if (!(prtshort || prtlong))
	goto out;
    if (prtshort && prtlong) {
	const char *dash = F_ISSET(opt, ONEDASH) ? "-" : "--";
	left[0] = '-';
	left[1] = opt->shortName;
	(void) stpcpy(stpcpy(stpcpy(left+2, ", "), dash), opt->longName);
    } else if (prtshort) {
	left[0] = '-';
	left[1] = opt->shortName;
	left[2] = '\0';
    } else if (prtlong) {
	/* XXX --long always padded for alignment with/without "-X, ". */
	const char *dash = poptArgType(opt) == POPT_ARG_MAINCALL ? ""
			 : (F_ISSET(opt, ONEDASH) ? "-" : "--");
	const char *longName = opt->longName;
	const char *toggle;
	if (F_ISSET(opt, TOGGLE)) {
	    toggle = "[no]";
	    if (longName[0] == 'n' && longName[1] == 'o') {
		longName += sizeof("no") - 1;
		if (longName[0] == '-')
		    longName++;
	    }
	} else
	    toggle = "";
	(void) stpcpy(stpcpy(stpcpy(stpcpy(left, "    "), dash), toggle), longName);
    }
#undef	prtlong

    if (argDescrip) {
	char * le = left + strlen(left);

	if (F_ISSET(opt, OPTIONAL))
	    *le++ = '[';

	/* Choose type of output */
	if (F_ISSET(opt, SHOW_DEFAULT)) {
	    defs = singleOptionDefaultValue(lineLength, opt, translation_domain);
	    if (defs) {
		char * t = malloc((help ? strlen(help) : 0) +
				strlen(defs) + sizeof(" "));
		if (t) {
		    char * te = t;
		    if (help)
			te = stpcpy(te, help);
		    *te++ = ' ';
		    strcpy(te, defs);
		    defs = _free(defs);
		    defs = t;
		}
	    }
	}

	if (opt->argDescrip == NULL) {
	    switch (poptArgType(opt)) {
	    case POPT_ARG_NONE:
		break;
	    case POPT_ARG_VAL:
#ifdef	NOTNOW	/* XXX pug ugly nerdy output */
	    {	long aLong = opt->val;
		int ops = F_ISSET(opt, LOGICALOPS);
		int negate = F_ISSET(opt, NOT);

		/* Don't bother displaying typical values */
		if (!ops && (aLong == 0L || aLong == 1L || aLong == -1L))
		    break;
		*le++ = '[';
		switch (ops) {
		case POPT_ARGFLAG_OR:
		    *le++ = '|';
		    break;
		case POPT_ARGFLAG_AND:
		    *le++ = '&';
		    break;
		case POPT_ARGFLAG_XOR:
		    *le++ = '^';
		    break;
		default:
		    break;
		}
		*le++ = (opt->longName != NULL ? '=' : ' ');
		if (negate) *le++ = '~';
		le += sprintf(le, (ops ? "0x%lx" : "%ld"), aLong);
		*le++ = ']';
	    }
#endif
		break;
	    case POPT_ARG_INT:
	    case POPT_ARG_SHORT:
	    case POPT_ARG_LONG:
	    case POPT_ARG_LONGLONG:
	    case POPT_ARG_FLOAT:
	    case POPT_ARG_DOUBLE:
	    case POPT_ARG_STRING:
		*le++ = (opt->longName != NULL ? '=' : ' ');
		le = stpcpy(le, argDescrip);
		break;
	    default:
		break;
	    }
	} else {
	    char *leo;

	    /* XXX argDescrip[0] determines "--foo=bar" or "--foo bar". */
	    if (!strchr(" =(", argDescrip[0]))
		*le++ = ((poptArgType(opt) == POPT_ARG_MAINCALL) ? ' ' :
			 (poptArgType(opt) == POPT_ARG_ARGV) ? ' ' :
			 opt->longName == NULL ? ' ' : '=');
	    le = stpcpy(leo = le, argDescrip);

	    /* Adjust for (possible) wide characters. */
	    displaypad = (int)((le - leo) - stringDisplayWidth(argDescrip));
	}
	if (F_ISSET(opt, OPTIONAL))
	    *le++ = ']';
	*le = '\0';
    }

    if (help)
	POPT_fprintf(fp,"  %-*s   ", (int)(maxLeftCol+displaypad), left);
    else {
	POPT_fprintf(fp,"  %s\n", left);
	goto out;
    }

    left = _free(left);
    if (defs)
	help = defs;

    helpLength = strlen(help);
    while (helpLength > lineLength) {
	const char * ch;
	char format[16];

	ch = help + lineLength - 1;
	while (ch > help && !_isspaceptr(ch))
	    ch = POPT_prev_char(ch);
	if (ch == help) break;		/* give up */
	while (ch > (help + 1) && _isspaceptr(ch))
	    ch = POPT_prev_char (ch);
	ch = POPT_next_char(ch);

	/*
	 *  XXX strdup is necessary to add NUL terminator so that an unknown
	 *  no. of (possible) multi-byte characters can be displayed.
	 */
	{   char * fmthelp = xstrdup(help);
	    if (fmthelp) {
		fmthelp[ch - help] = '\0';
		sprintf(format, "%%s\n%%%ds", (int) indentLength);
		POPT_fprintf(fp, format, fmthelp, " ");
		free(fmthelp);
	    }
	}

	help = ch;
	while (_isspaceptr(help) && *help)
	    help = POPT_next_char(help);
	helpLength = strlen(help);
    }

    if (helpLength) fprintf(fp, "%s\n", help);
    help = NULL;

out:
    defs = _free(defs);
    left = _free(left);
}

/**
 * Find display width for longest argument string.
 * @param opt		option(s)
 * @param translation_domain	translation domain
 * @return		display width
 */
static size_t maxArgWidth(const struct poptOption * opt,
		       const char * translation_domain)
{
    size_t max = 0;
    size_t len = 0;
    const char * argDescrip;
    
    if (opt != NULL)
    while (opt->longName || opt->shortName || opt->arg) {
	if (poptArgType(opt) == POPT_ARG_INCLUDE_TABLE) {
	    void * arg = opt->arg;
	    /* XXX sick hack to preserve pretense of ABI. */
	    if (arg == poptHelpOptions)
		arg = poptHelpOptionsI18N;
	    if (arg)	/* XXX program error */
		len = maxArgWidth(arg, translation_domain);
	    if (len > max) max = len;
	} else if (!F_ISSET(opt, DOC_HIDDEN)) {
	    len = sizeof("  ")-1;
	    /* XXX --long always padded for alignment with/without "-X, ". */
	    len += sizeof("-X, ")-1;
	    if (opt->longName) {
		len += (F_ISSET(opt, ONEDASH) ? sizeof("-") : sizeof("--")) - 1;
		len += strlen(opt->longName);
	    }

	    argDescrip = getArgDescrip(opt, translation_domain);

	    if (argDescrip) {

		/* XXX argDescrip[0] determines "--foo=bar" or "--foo bar". */
		if (!strchr(" =(", argDescrip[0])) len += sizeof("=")-1;

		/* Adjust for (possible) wide characters. */
		len += stringDisplayWidth(argDescrip);
	    }

	    if (F_ISSET(opt, OPTIONAL)) len += sizeof("[]")-1;
	    if (len > max) max = len;
	}
	opt++;
    }
    
    return max;
}

/**
 * Display popt alias and exec help.
 * @param fp		output file handle
 * @param items		alias/exec array
 * @param nitems	no. of alias/exec entries
 * @param columns	output display width control
 * @param translation_domain	translation domain
 */
static void itemHelp(FILE * fp,
		poptItem items, int nitems,
		columns_t columns,
		const char * translation_domain)
{
    poptItem item;
    int i;

    if (items != NULL)
    for (i = 0, item = items; i < nitems; i++, item++) {
	const struct poptOption * opt;
	opt = &item->option;
	if ((opt->longName || opt->shortName) && !F_ISSET(opt, DOC_HIDDEN))
	    singleOptionHelp(fp, columns, opt, translation_domain);
    }
}

/**
 * Display help text for a table of options.
 * @param con		context
 * @param fp		output file handle
 * @param table		option(s)
 * @param columns	output display width control
 * @param translation_domain	translation domain
 */
static void singleTableHelp(poptContext con, FILE * fp,
		const struct poptOption * table,
		columns_t columns,
		const char * translation_domain)
{
    const struct poptOption * opt;
    const char *sub_transdom;

    if (table == poptAliasOptions) {
	itemHelp(fp, con->aliases, con->numAliases, columns, NULL);
	itemHelp(fp, con->execs, con->numExecs, columns, NULL);
	return;
    }

    if (table != NULL)
    for (opt = table; opt->longName || opt->shortName || opt->arg; opt++) {
	if ((opt->longName || opt->shortName) && !F_ISSET(opt, DOC_HIDDEN))
	    singleOptionHelp(fp, columns, opt, translation_domain);
    }

    if (table != NULL)
    for (opt = table; opt->longName || opt->shortName || opt->arg; opt++) {
	void * arg = opt->arg;
	if (poptArgType(opt) != POPT_ARG_INCLUDE_TABLE)
	    continue;
	/* XXX sick hack to preserve pretense of ABI. */
	if (arg == poptHelpOptions)
	    arg = poptHelpOptionsI18N;
	sub_transdom = getTableTranslationDomain(arg);
	if (sub_transdom == NULL)
	    sub_transdom = translation_domain;
	    
	/* If no popt aliases/execs, skip poptAliasOption processing. */
	if (arg == poptAliasOptions && !(con->numAliases || con->numExecs))
	    continue;
	if (opt->descrip)
	    POPT_fprintf(fp, "\n%s\n", D_(sub_transdom, opt->descrip));

	singleTableHelp(con, fp, arg, columns, sub_transdom);
    }
}

/**
 * @param con		context
 * @param fp		output file handle
 */
static size_t showHelpIntro(poptContext con, FILE * fp)
{
    const char *usage_str = POPT_("Usage:");
    size_t len = strlen(usage_str);
    POPT_fprintf(fp, "%s", usage_str);

    if (!(con->flags & POPT_CONTEXT_KEEP_FIRST)) {
	struct optionStackEntry * os = con->optionStack;
	const char * fn = (os->argv ? os->argv[0] : NULL);
	if (fn == NULL) return len;
	if (strchr(fn, '/')) fn = strrchr(fn, '/') + 1;
	/* XXX POPT_fprintf not needed for argv[0] display. */
	fprintf(fp, " %s", fn);
	len += strlen(fn) + 1;
    }

    return len;
}

void poptPrintHelp(poptContext con, FILE * fp, UNUSED(int flags))
{
    columns_t columns = calloc((size_t)1, sizeof(*columns));

    (void) showHelpIntro(con, fp);
    if (con->otherHelp)
	POPT_fprintf(fp, " %s\n", con->otherHelp);
    else
	POPT_fprintf(fp, " %s\n", POPT_("[OPTION...]"));

    if (columns) {
	columns->cur = maxArgWidth(con->options, NULL);
	columns->max = maxColumnWidth(fp);
	singleTableHelp(con, fp, con->options, columns, NULL);
	free(columns);
    }
}

/**
 * Display usage text for an option.
 * @param fp		output file handle
 * @param columns	output display width control
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static size_t singleOptionUsage(FILE * fp, columns_t columns,
		const struct poptOption * opt,
		const char *translation_domain)
{
    size_t len = sizeof(" []")-1;
    const char * argDescrip = getArgDescrip(opt, translation_domain);
    /* Display shortName iff printable non-space. */
    int prtshort = (int)(isprint((int)opt->shortName) && opt->shortName != ' ');

#define	prtlong	(opt->longName != NULL)	/* XXX splint needs a clue */
    if (!(prtshort || prtlong))
	return columns->cur;

    len = sizeof(" []")-1;
    if (prtshort)
	len += sizeof("-c")-1;
    if (prtlong) {
	if (prtshort) len += sizeof("|")-1;
	len += (F_ISSET(opt, ONEDASH) ? sizeof("-") : sizeof("--")) - 1;
	len += strlen(opt->longName);
    }

    if (argDescrip) {

	/* XXX argDescrip[0] determines "--foo=bar" or "--foo bar". */
	if (!strchr(" =(", argDescrip[0])) len += sizeof("=")-1;

	/* Adjust for (possible) wide characters. */
	len += stringDisplayWidth(argDescrip);
    }

    if ((columns->cur + len) > columns->max) {
	fprintf(fp, "\n       ");
	columns->cur = (size_t)7;
    } 

    fprintf(fp, " [");
    if (prtshort)
	fprintf(fp, "-%c", opt->shortName);
    if (prtlong)
	fprintf(fp, "%s%s%s",
		(prtshort ? "|" : ""),
		(F_ISSET(opt, ONEDASH) ? "-" : "--"),
		opt->longName);
#undef	prtlong

    if (argDescrip) {
	/* XXX argDescrip[0] determines "--foo=bar" or "--foo bar". */
	if (!strchr(" =(", argDescrip[0])) fputc(opt->longName == NULL ? ' ' : '=', fp);
	fprintf(fp, "%s", argDescrip);
    }
    fprintf(fp, "]");

    return columns->cur + len + 1;
}

/**
 * Display popt alias and exec usage.
 * @param fp		output file handle
 * @param columns	output display width control
 * @param item		alias/exec array
 * @param nitems	no. of ara/exec entries
 * @param translation_domain	translation domain
 */
static size_t itemUsage(FILE * fp, columns_t columns,
		poptItem item, int nitems,
		const char * translation_domain)
{
    int i;

    if (item != NULL)
    for (i = 0; i < nitems; i++, item++) {
	const struct poptOption * opt;
	opt = &item->option;
        if (poptArgType(opt) == POPT_ARG_INTL_DOMAIN) {
	    translation_domain = (const char *)opt->arg;
	} else
	if ((opt->longName || opt->shortName) && !F_ISSET(opt, DOC_HIDDEN)) {
	    columns->cur = singleOptionUsage(fp, columns, opt, translation_domain);
	}
    }

    return columns->cur;
}

/**
 * Keep track of option tables already processed.
 */
typedef struct poptDone_s {
    int nopts;
    int maxopts;
    const void ** opts;
} * poptDone;

/**
 * Display usage text for a table of options.
 * @param con		context
 * @param fp		output file handle
 * @param columns	output display width control
 * @param opt		option(s)
 * @param translation_domain	translation domain
 * @param done		tables already processed
 * @return
 */
static size_t singleTableUsage(poptContext con, FILE * fp, columns_t columns,
		const struct poptOption * opt,
		const char * translation_domain,
		poptDone done)
{
    if (opt != NULL)
    for (; (opt->longName || opt->shortName || opt->arg) ; opt++) {
        if (poptArgType(opt) == POPT_ARG_INTL_DOMAIN) {
	    translation_domain = (const char *)opt->arg;
	} else
	if (poptArgType(opt) == POPT_ARG_INCLUDE_TABLE) {
	    void * arg = opt->arg;
	    /* XXX sick hack to preserve pretense of ABI. */
	    if (arg == poptHelpOptions)
		arg = poptHelpOptionsI18N;
	    if (done) {
		int i = 0;
		if (done->opts != NULL)
		for (i = 0; i < done->nopts; i++) {
		    const void * that = done->opts[i];
		    if (that == NULL || that != arg)
			continue;
		    break;
		}
		/* Skip if this table has already been processed. */
		if (arg == NULL || i < done->nopts)
		    continue;
		if (done->opts != NULL && done->nopts < done->maxopts)
		    done->opts[done->nopts++] = (const void *) arg;
	    }
	    columns->cur = singleTableUsage(con, fp, columns, opt->arg,
			translation_domain, done);
	} else
	if ((opt->longName || opt->shortName) && !F_ISSET(opt, DOC_HIDDEN)) {
	    columns->cur = singleOptionUsage(fp, columns, opt, translation_domain);
	}
    }

    return columns->cur;
}

/**
 * Return concatenated short options for display.
 * @todo Sub-tables should be recursed.
 * @param opt		option(s)
 * @param fp		output file handle
 * @retval str		concatenation of short options
 * @return		length of display string
 */
static size_t showShortOptions(const struct poptOption * opt, FILE * fp,
		char * str)
{
    /* bufsize larger then the ascii set, lazy allocation on top level call. */
    size_t nb = (size_t)300;
    char * s = (str != NULL ? str : calloc((size_t)1, nb));
    size_t len = (size_t)0;

    if (s == NULL)
	return 0;

    if (opt != NULL)
    for (; (opt->longName || opt->shortName || opt->arg); opt++) {
	if (!F_ISSET(opt, DOC_HIDDEN) && opt->shortName && !poptArgType(opt))
	{
	    /* Display shortName iff unique printable non-space. */
	    if (!strchr(s, opt->shortName) && isprint((int)opt->shortName)
	     && opt->shortName != ' ')
		s[strlen(s)] = opt->shortName;
	} else if (poptArgType(opt) == POPT_ARG_INCLUDE_TABLE) {
	    void * arg = opt->arg;
	    /* XXX sick hack to preserve pretense of ABI. */
	    if (arg == poptHelpOptions)
		arg = poptHelpOptionsI18N;
	    if (arg)	/* XXX program error */
		len = showShortOptions(arg, fp, s);
	}
    } 

    /* On return to top level, print the short options, return print length. */
    if (s != str && *s != '\0') {
	fprintf(fp, " [-%s]", s);
	len = strlen(s) + sizeof(" [-]")-1;
    }
    if (s != str)
	free(s);
    return len;
}

void poptPrintUsage(poptContext con, FILE * fp, UNUSED(int flags))
{
    columns_t columns = calloc((size_t)1, sizeof(*columns));
    struct poptDone_s done_buf;
    poptDone done = &done_buf;

    memset(done, 0, sizeof(*done));
    done->nopts = 0;
    done->maxopts = 64;
  if (columns) {
    columns->cur = done->maxopts * sizeof(*done->opts);
    columns->max = maxColumnWidth(fp);
    done->opts = calloc((size_t)1, columns->cur);
    if (done->opts != NULL)
	done->opts[done->nopts++] = (const void *) con->options;

    columns->cur = showHelpIntro(con, fp);
    columns->cur += showShortOptions(con->options, fp, NULL);
    columns->cur = singleTableUsage(con, fp, columns, con->options, NULL, done);
    columns->cur = itemUsage(fp, columns, con->aliases, con->numAliases, NULL);
    columns->cur = itemUsage(fp, columns, con->execs, con->numExecs, NULL);

    if (con->otherHelp) {
	columns->cur += strlen(con->otherHelp) + 1;
	if (columns->cur > columns->max) fprintf(fp, "\n       ");
	fprintf(fp, " %s", con->otherHelp);
    }

    fprintf(fp, "\n");
    if (done->opts != NULL)
	free(done->opts);
    free(columns);
  }
}

void poptSetOtherOptionHelp(poptContext con, const char * text)
{
    con->otherHelp = _free(con->otherHelp);
    con->otherHelp = xstrdup(text);
}
