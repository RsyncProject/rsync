/* -*- Mode: C; tab-width: 8; indent-tabs-mode: t; c-basic-offset: 4 -*- */

/*@-type@*/
/** \ingroup popt
 * \file popt/popthelp.c
 */

/* (C) 1998-2000 Red Hat, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.rpm.org/pub/rpm/dist. */

#include "system.h"
#include "poptint.h"

/**
 * @param con		context
 * @param key		option(s)
 */
static void displayArgs(poptContext con,
		/*@unused@*/ enum poptCallbackReason foo,
		struct poptOption * key, 
		/*@unused@*/ const char * arg, /*@unused@*/ void * data)
	/*@globals fileSystem@*/
	/*@modifies fileSystem@*/
{
    if (key->shortName == '?')
	poptPrintHelp(con, stdout, 0);
    else
	poptPrintUsage(con, stdout, 0);
    exit(0);
}

#ifdef	NOTYET
/*@unchecked@*/
static int show_option_defaults = 0;
#endif

/**
 * Empty table marker to enable displaying popt alias/exec options.
 */
/*@observer@*/ /*@unchecked@*/
struct poptOption poptAliasOptions[] = {
    POPT_TABLEEND
};

/**
 * Auto help table options.
 */
/*@-castfcnptr@*/
/*@observer@*/ /*@unchecked@*/
struct poptOption poptHelpOptions[] = {
  { NULL, '\0', POPT_ARG_CALLBACK, (void *)&displayArgs, '\0', NULL, NULL },
  { "help", '?', 0, NULL, '?', N_("Show this help message"), NULL },
  { "usage", '\0', 0, NULL, 'u', N_("Display brief usage message"), NULL },
#ifdef	NOTYET
  { "defaults", '\0', POPT_ARG_NONE, &show_option_defaults, 0,
	N_("Display option defaults in message"), NULL },
#endif
    POPT_TABLEEND
} ;
/*@=castfcnptr@*/

/**
 * @param table		option(s)
 */
/*@observer@*/ /*@null@*/ static const char *const
getTableTranslationDomain(/*@null@*/ const struct poptOption *table)
	/*@*/
{
    const struct poptOption *opt;

    if (table != NULL)
    for (opt = table; opt->longName || opt->shortName || opt->arg; opt++) {
	if (opt->argInfo == POPT_ARG_INTL_DOMAIN)
	    return opt->arg;
    }
    return NULL;
}

/**
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
/*@observer@*/ /*@null@*/ static const char *const
getArgDescrip(const struct poptOption * opt,
		/*@-paramuse@*/		/* FIX: wazzup? */
		/*@null@*/ const char * translation_domain)
		/*@=paramuse@*/
	/*@*/
{
    if (!(opt->argInfo & POPT_ARG_MASK)) return NULL;

    if (opt == (poptHelpOptions + 1) || opt == (poptHelpOptions + 2))
	if (opt->argDescrip) return POPT_(opt->argDescrip);

    if (opt->argDescrip) return D_(translation_domain, opt->argDescrip);

    switch (opt->argInfo & POPT_ARG_MASK) {
    case POPT_ARG_NONE:		return POPT_("NONE");
    case POPT_ARG_VAL:		return POPT_("VAL");
    case POPT_ARG_INT:		return POPT_("INT");
    case POPT_ARG_LONG:		return POPT_("LONG");
    case POPT_ARG_STRING:	return POPT_("STRING");
    case POPT_ARG_FLOAT:	return POPT_("FLOAT");
    case POPT_ARG_DOUBLE:	return POPT_("DOUBLE");
    default:			return POPT_("ARG");
    }
}

/**
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static /*@only@*/ /*@null@*/ char *
singleOptionDefaultValue(int lineLength,
		const struct poptOption * opt,
		/*@-paramuse@*/	/* FIX: i18n macros disable with lclint */
		/*@null@*/ const char * translation_domain)
		/*@=paramuse@*/
	/*@*/
{
    const char * defstr = D_(translation_domain, "default");
    char * le = malloc(4*lineLength + 1);
    char * l = le;

    if (le == NULL) return NULL;	/* XXX can't happen */
    *le = '\0';
    *le++ = '(';
    strcpy(le, defstr);	le += strlen(le);
    *le++ = ':';
    *le++ = ' ';
    if (opt->arg)	/* XXX programmer error */
    switch (opt->argInfo & POPT_ARG_MASK) {
    case POPT_ARG_VAL:
    case POPT_ARG_INT:
    {	long aLong = *((int *)opt->arg);
	le += sprintf(le, "%ld", aLong);
    }	break;
    case POPT_ARG_LONG:
    {	long aLong = *((long *)opt->arg);
	le += sprintf(le, "%ld", aLong);
    }	break;
    case POPT_ARG_FLOAT:
    {	double aDouble = *((float *)opt->arg);
	le += sprintf(le, "%g", aDouble);
    }	break;
    case POPT_ARG_DOUBLE:
    {	double aDouble = *((double *)opt->arg);
	le += sprintf(le, "%g", aDouble);
    }	break;
    case POPT_ARG_STRING:
    {	const char * s = *(const char **)opt->arg;
	if (s == NULL) {
	    strcpy(le, "null");	le += strlen(le);
	} else {
	    size_t slen = 4*lineLength - (le - l) - sizeof("\"...\")");
	    *le++ = '"';
	    strncpy(le, s, slen); le[slen] = '\0'; le += strlen(le);	
	    if (slen < strlen(s)) {
		strcpy(le, "...");	le += strlen(le);
	    }
	    *le++ = '"';
	}
    }	break;
    case POPT_ARG_NONE:
    default:
	l = _free(l);
	return NULL;
	/*@notreached@*/ break;
    }
    *le++ = ')';
    *le = '\0';

    return l;
}

/**
 * @param fp		output file handle
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static void singleOptionHelp(FILE * fp, int maxLeftCol, 
		const struct poptOption * opt,
		/*@null@*/ const char * translation_domain)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    int indentLength = maxLeftCol + 5;
    int lineLength = 79 - indentLength;
    const char * help = D_(translation_domain, opt->descrip);
    const char * argDescrip = getArgDescrip(opt, translation_domain);
    int helpLength;
    char * defs = NULL;
    char * left;
    int nb = maxLeftCol + 1;

    /* Make sure there's more than enough room in target buffer. */
    if (opt->longName)	nb += strlen(opt->longName);
    if (argDescrip)	nb += strlen(argDescrip);

    left = malloc(nb);
    if (left == NULL) return;	/* XXX can't happen */
    left[0] = '\0';
    left[maxLeftCol] = '\0';

    if (opt->longName && opt->shortName)
	sprintf(left, "-%c, %s%s", opt->shortName,
		((opt->argInfo & POPT_ARGFLAG_ONEDASH) ? "-" : "--"),
		opt->longName);
    else if (opt->shortName != '\0') 
	sprintf(left, "-%c", opt->shortName);
    else if (opt->longName)
	sprintf(left, "%s%s",
		((opt->argInfo & POPT_ARGFLAG_ONEDASH) ? "-" : "--"),
		opt->longName);
    if (!*left) goto out;
    if (argDescrip) {
	char * le = left + strlen(left);

	if (opt->argInfo & POPT_ARGFLAG_OPTIONAL)
	    *le++ = '[';

	/* Choose type of output */
	/*@-branchstate@*/
	if (opt->argInfo & POPT_ARGFLAG_SHOW_DEFAULT) {
	    defs = singleOptionDefaultValue(lineLength, opt, translation_domain);
	    if (defs) {
		char * t = malloc((help ? strlen(help) : 0) +
				strlen(defs) + sizeof(" "));
		if (t) {
		    char * te = t;
		    *te = '\0';
		    if (help) {
			strcpy(te, help);	te += strlen(te);
		    }
		    *te++ = ' ';
		    strcpy(te, defs);
		    defs = _free(defs);
		}
		defs = t;
	    }
	}
	/*@=branchstate@*/

	if (opt->argDescrip == NULL) {
	    switch (opt->argInfo & POPT_ARG_MASK) {
	    case POPT_ARG_NONE:
		break;
	    case POPT_ARG_VAL:
	    {	long aLong = opt->val;
		int ops = (opt->argInfo & POPT_ARGFLAG_LOGICALOPS);
		int negate = (opt->argInfo & POPT_ARGFLAG_NOT);

		/* Don't bother displaying typical values */
		if (!ops && (aLong == 0L || aLong == 1L || aLong == -1L))
		    break;
		*le++ = '[';
		switch (ops) {
		case POPT_ARGFLAG_OR:
		    *le++ = '|';
		    /*@innerbreak@*/ break;
		case POPT_ARGFLAG_AND:
		    *le++ = '&';
		    /*@innerbreak@*/ break;
		case POPT_ARGFLAG_XOR:
		    *le++ = '^';
		    /*@innerbreak@*/ break;
		default:
		    /*@innerbreak@*/ break;
		}
		*le++ = '=';
		if (negate) *le++ = '~';
		/*@-formatconst@*/
		le += sprintf(le, (ops ? "0x%lx" : "%ld"), aLong);
		/*@=formatconst@*/
		*le++ = ']';
	    }	break;
	    case POPT_ARG_INT:
	    case POPT_ARG_LONG:
	    case POPT_ARG_FLOAT:
	    case POPT_ARG_DOUBLE:
	    case POPT_ARG_STRING:
		*le++ = '=';
		strcpy(le, argDescrip);		le += strlen(le);
		break;
	    default:
		break;
	    }
	} else {
	    *le++ = '=';
	    strcpy(le, argDescrip);		le += strlen(le);
	}
	if (opt->argInfo & POPT_ARGFLAG_OPTIONAL)
	    *le++ = ']';
	*le = '\0';
    }

    if (help)
	fprintf(fp,"  %-*s   ", maxLeftCol, left);
    else {
	fprintf(fp,"  %s\n", left); 
	goto out;
    }

    left = _free(left);
    if (defs) {
	help = defs; defs = NULL;
    }

    helpLength = strlen(help);
    while (helpLength > lineLength) {
	const char * ch;
	char format[10];

	ch = help + lineLength - 1;
	while (ch > help && !isspace(*ch)) ch--;
	if (ch == help) break;		/* give up */
	while (ch > (help + 1) && isspace(*ch)) ch--;
	ch++;

	sprintf(format, "%%.%ds\n%%%ds", (int) (ch - help), indentLength);
	/*@-formatconst@*/
	fprintf(fp, format, help, " ");
	/*@=formatconst@*/
	help = ch;
	while (isspace(*help) && *help) help++;
	helpLength = strlen(help);
    }

    if (helpLength) fprintf(fp, "%s\n", help);

out:
    /*@-dependenttrans@*/
    defs = _free(defs);
    /*@=dependenttrans@*/
    left = _free(left);
}

/**
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static int maxArgWidth(const struct poptOption * opt,
		       /*@null@*/ const char * translation_domain)
	/*@*/
{
    int max = 0;
    int len = 0;
    const char * s;
    
    if (opt != NULL)
    while (opt->longName || opt->shortName || opt->arg) {
	if ((opt->argInfo & POPT_ARG_MASK) == POPT_ARG_INCLUDE_TABLE) {
	    if (opt->arg)	/* XXX program error */
	    len = maxArgWidth(opt->arg, translation_domain);
	    if (len > max) max = len;
	} else if (!(opt->argInfo & POPT_ARGFLAG_DOC_HIDDEN)) {
	    len = sizeof("  ")-1;
	    if (opt->shortName != '\0') len += sizeof("-X")-1;
	    if (opt->shortName != '\0' && opt->longName) len += sizeof(", ")-1;
	    if (opt->longName) {
		len += ((opt->argInfo & POPT_ARGFLAG_ONEDASH)
			? sizeof("-")-1 : sizeof("--")-1);
		len += strlen(opt->longName);
	    }

	    s = getArgDescrip(opt, translation_domain);
	    if (s)
		len += sizeof("=")-1 + strlen(s);
	    if (opt->argInfo & POPT_ARGFLAG_OPTIONAL) len += sizeof("[]")-1;
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
 * @param translation_domain	translation domain
 */
static void itemHelp(FILE * fp,
		/*@null@*/ poptItem items, int nitems, int left,
		/*@null@*/ const char * translation_domain)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    poptItem item;
    int i;

    if (items != NULL)
    for (i = 0, item = items; i < nitems; i++, item++) {
	const struct poptOption * opt;
	opt = &item->option;
	if ((opt->longName || opt->shortName) && 
	    !(opt->argInfo & POPT_ARGFLAG_DOC_HIDDEN))
	    singleOptionHelp(fp, left, opt, translation_domain);
    }
}

/**
 * @param fp		output file handle
 * @param table		option(s)
 * @param translation_domain	translation domain
 */
static void singleTableHelp(poptContext con, FILE * fp,
		/*@null@*/ const struct poptOption * table, int left,
		/*@null@*/ const char * translation_domain)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    const struct poptOption * opt;
    const char *sub_transdom;

    if (table == poptAliasOptions) {
	itemHelp(fp, con->aliases, con->numAliases, left, NULL);
	itemHelp(fp, con->execs, con->numExecs, left, NULL);
	return;
    }

    if (table != NULL)
    for (opt = table; (opt->longName || opt->shortName || opt->arg); opt++) {
	if ((opt->longName || opt->shortName) && 
	    !(opt->argInfo & POPT_ARGFLAG_DOC_HIDDEN))
	    singleOptionHelp(fp, left, opt, translation_domain);
    }

    if (table != NULL)
    for (opt = table; (opt->longName || opt->shortName || opt->arg); opt++) {
	if ((opt->argInfo & POPT_ARG_MASK) != POPT_ARG_INCLUDE_TABLE)
	    continue;
	sub_transdom = getTableTranslationDomain(opt->arg);
	if (sub_transdom == NULL)
	    sub_transdom = translation_domain;
	    
	if (opt->descrip)
	    fprintf(fp, "\n%s\n", D_(sub_transdom, opt->descrip));

	singleTableHelp(con, fp, opt->arg, left, sub_transdom);
    }
}

/**
 * @param con		context
 * @param fp		output file handle
 */
static int showHelpIntro(poptContext con, FILE * fp)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    int len = 6;
    const char * fn;

    fprintf(fp, POPT_("Usage:"));
    if (!(con->flags & POPT_CONTEXT_KEEP_FIRST)) {
	/*@-nullderef@*/	/* LCL: wazzup? */
	fn = con->optionStack->argv[0];
	/*@=nullderef@*/
	if (fn == NULL) return len;
	if (strchr(fn, '/')) fn = strrchr(fn, '/') + 1;
	fprintf(fp, " %s", fn);
	len += strlen(fn) + 1;
    }

    return len;
}

void poptPrintHelp(poptContext con, FILE * fp, /*@unused@*/ int flags)
{
    int leftColWidth;

    (void) showHelpIntro(con, fp);
    if (con->otherHelp)
	fprintf(fp, " %s\n", con->otherHelp);
    else
	fprintf(fp, " %s\n", POPT_("[OPTION...]"));

    leftColWidth = maxArgWidth(con->options, NULL);
    singleTableHelp(con, fp, con->options, leftColWidth, NULL);
}

/**
 * @param fp		output file handle
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static int singleOptionUsage(FILE * fp, int cursor, 
		const struct poptOption * opt,
		/*@null@*/ const char *translation_domain)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    int len = 3;
    char shortStr[2] = { '\0', '\0' };
    const char * item = shortStr;
    const char * argDescrip = getArgDescrip(opt, translation_domain);

    if (opt->shortName!= '\0' ) {
	if (!(opt->argInfo & POPT_ARG_MASK)) 
	    return cursor;	/* we did these already */
	len++;
	shortStr[0] = opt->shortName;
	shortStr[1] = '\0';
    } else if (opt->longName) {
	len += 1 + strlen(opt->longName);
	item = opt->longName;
    }

    if (len == 3) return cursor;

    if (argDescrip) 
	len += strlen(argDescrip) + 1;

    if ((cursor + len) > 79) {
	fprintf(fp, "\n       ");
	cursor = 7;
    } 

    fprintf(fp, " [-%s%s%s%s]",
	((opt->shortName || (opt->argInfo & POPT_ARGFLAG_ONEDASH)) ? "" : "-"),
	item,
	(argDescrip ? (opt->shortName != '\0' ? " " : "=") : ""),
	(argDescrip ? argDescrip : ""));

    return cursor + len + 1;
}

/**
 * Display popt alias and exec usage.
 * @param fp		output file handle
 * @param item		alias/exec array
 * @param nitems	no. of ara/exec entries
 * @param translation_domain	translation domain
 */
static int itemUsage(FILE * fp, int cursor, poptItem item, int nitems,
		/*@null@*/ const char * translation_domain)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    int i;

    /*@-branchstate@*/		/* FIX: W2DO? */
    if (item != NULL)
    for (i = 0; i < nitems; i++, item++) {
	const struct poptOption * opt;
	opt = &item->option;
        if ((opt->argInfo & POPT_ARG_MASK) == POPT_ARG_INTL_DOMAIN) {
	    translation_domain = (const char *)opt->arg;
	} else if ((opt->longName || opt->shortName) &&
		 !(opt->argInfo & POPT_ARGFLAG_DOC_HIDDEN)) {
	    cursor = singleOptionUsage(fp, cursor, opt, translation_domain);
	}
    }
    /*@=branchstate@*/

    return cursor;
}

/**
 * @param fp		output file handle
 * @param opt		option(s)
 * @param translation_domain	translation domain
 */
static int singleTableUsage(poptContext con, FILE * fp,
		int cursor, const struct poptOption * opt,
		/*@null@*/ const char * translation_domain)
	/*@globals fileSystem @*/
	/*@modifies *fp, fileSystem @*/
{
    /*@-branchstate@*/		/* FIX: W2DO? */
    if (opt != NULL)
    for (; (opt->longName || opt->shortName || opt->arg) ; opt++) {
        if ((opt->argInfo & POPT_ARG_MASK) == POPT_ARG_INTL_DOMAIN) {
	    translation_domain = (const char *)opt->arg;
	} else if ((opt->argInfo & POPT_ARG_MASK) == POPT_ARG_INCLUDE_TABLE) {
	    if (opt->arg)	/* XXX program error */
	    cursor = singleTableUsage(con, fp, cursor, opt->arg,
			translation_domain);
	} else if ((opt->longName || opt->shortName) &&
		 !(opt->argInfo & POPT_ARGFLAG_DOC_HIDDEN)) {
	    cursor = singleOptionUsage(fp, cursor, opt, translation_domain);
	}
    }
    /*@=branchstate@*/

    return cursor;
}

/**
 * Return concatenated short options for display.
 * @param opt		option(s)
 * @param fp		output file handle
 * @retval str		concatenation of short options
 * @return		length of display string
 */
static int showShortOptions(const struct poptOption * opt, FILE * fp,
		/*@null@*/ char * str)
	/*@globals fileSystem @*/
	/*@modifies *str, *fp, fileSystem @*/
{
    char * s = alloca(300);	/* larger then the ascii set */

    s[0] = '\0';
    /*@-branchstate@*/		/* FIX: W2DO? */
    if (str == NULL) {
	memset(s, 0, sizeof(s));
	str = s;
    }
    /*@=branchstate@*/

    if (opt != NULL)
    for (; (opt->longName || opt->shortName || opt->arg); opt++) {
	if (opt->shortName && !(opt->argInfo & POPT_ARG_MASK))
	    str[strlen(str)] = opt->shortName;
	else if ((opt->argInfo & POPT_ARG_MASK) == POPT_ARG_INCLUDE_TABLE)
	    if (opt->arg)	/* XXX program error */
		(void) showShortOptions(opt->arg, fp, str);
    } 

    if (s != str || *s != '\0')
	return 0;

    fprintf(fp, " [-%s]", s);
    return strlen(s) + 4;
}

void poptPrintUsage(poptContext con, FILE * fp, /*@unused@*/ int flags)
{
    int cursor;

    cursor = showHelpIntro(con, fp);
    cursor += showShortOptions(con->options, fp, NULL);
    (void) singleTableUsage(con, fp, cursor, con->options, NULL);
    (void) itemUsage(fp, cursor, con->aliases, con->numAliases, NULL);
    (void) itemUsage(fp, cursor, con->execs, con->numExecs, NULL);

    if (con->otherHelp) {
	cursor += strlen(con->otherHelp) + 1;
	if (cursor > 79) fprintf(fp, "\n       ");
	fprintf(fp, " %s", con->otherHelp);
    }

    fprintf(fp, "\n");
}

void poptSetOtherOptionHelp(poptContext con, const char * text)
{
    con->otherHelp = _free(con->otherHelp);
    con->otherHelp = xstrdup(text);
}
/*@=type@*/
