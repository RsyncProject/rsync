/** \ingroup popt
 * @file
 */

/* (C) 1998-2002 Red Hat, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.rpm.org/pub/rpm/dist. */

#include "system.h"

#define POPT_ARGV_ARRAY_GROW_DELTA 5

int poptDupArgv(int argc, const char **argv,
		int * argcPtr, const char *** argvPtr)
{
    size_t nb = (argc + 1) * sizeof(*argv);
    const char ** argv2;
    char * dst;
    int i;

    if (argc <= 0 || argv == NULL)	/* XXX can't happen */
	return POPT_ERROR_NOARG;
    for (i = 0; i < argc; i++) {
	if (argv[i] == NULL)
	    return POPT_ERROR_NOARG;
	nb += strlen(argv[i]) + 1;
    }
	
    dst = malloc(nb);
    if (dst == NULL)			/* XXX can't happen */
	return POPT_ERROR_MALLOC;
    argv2 = (void *) dst;
    dst += (argc + 1) * sizeof(*argv);
    *dst = '\0';

    for (i = 0; i < argc; i++) {
	argv2[i] = dst;
	dst = stpcpy(dst, argv[i]);
	dst++;	/* trailing NUL */
    }
    argv2[argc] = NULL;

    if (argvPtr) {
	*argvPtr = argv2;
    } else {
	free(argv2);
	argv2 = NULL;
    }
    if (argcPtr)
	*argcPtr = argc;
    return 0;
}

int poptParseArgvString(const char * s, int * argcPtr, const char *** argvPtr)
{
    const char * src;
    char quote = '\0';
    int argvAlloced = POPT_ARGV_ARRAY_GROW_DELTA;
    const char ** argv = malloc(sizeof(*argv) * argvAlloced);
    const char ** argv_tmp;
    int argc = 0;
    size_t buflen = strlen(s) + 1;
    char * buf, * bufOrig = NULL;
    int rc = POPT_ERROR_MALLOC;

    if (argv == NULL) return rc;
    buf = bufOrig = calloc((size_t)1, buflen);
    if (buf == NULL) {
	free(argv);
	return rc;
    }
    argv[argc] = buf;

    for (src = s; *src != '\0'; src++) {
	if (quote == *src) {
	    quote = '\0';
	} else if (quote != '\0') {
	    if (*src == '\\') {
		src++;
		if (!*src) {
		    rc = POPT_ERROR_BADQUOTE;
		    goto exit;
		}
		if (*src != quote) *buf++ = '\\';
	    }
	    *buf++ = *src;
	} else if (_isspaceptr(src)) {
	    if (*argv[argc] != '\0') {
		buf++, argc++;
		if (argc == argvAlloced) {
		    argvAlloced += POPT_ARGV_ARRAY_GROW_DELTA;
		    argv_tmp = realloc(argv, sizeof(*argv) * argvAlloced);
		    if (argv_tmp == NULL) goto exit;
		    argv = argv_tmp;
		}
		argv[argc] = buf;
	    }
	} else switch (*src) {
	  case '"':
	  case '\'':
	    quote = *src;
	    break;
	  case '\\':
	    src++;
	    if (!*src) {
		rc = POPT_ERROR_BADQUOTE;
		goto exit;
	    }
	    /* fallthrough */
	  default:
	    *buf++ = *src;
	    break;
	}
    }

    if (strlen(argv[argc])) {
	argc++, buf++;
    }

    rc = poptDupArgv(argc, argv, argcPtr, argvPtr);

exit:
    if (bufOrig) free(bufOrig);
    if (argv) free(argv);
    return rc;
}

/* still in the dev stage.
 * return values, perhaps 1== file error
 * 2== line to long
 * 3== umm.... more?
 */
int poptConfigFileToString(FILE *fp, char ** argstrp,
		UNUSED(int flags))
{
    char line[999];
    char * argstr;
    char * argstr_tmp;
    char * p;
    char * q;
    char * x;
    size_t t;
    size_t argvlen = 0;
    size_t maxlinelen = sizeof(line);
    size_t linelen;
    size_t maxargvlen = (size_t)480;

    *argstrp = NULL;

    /*   |   this_is   =   our_line
     *	     p             q      x
     */

    if (fp == NULL)
	return POPT_ERROR_NULLARG;

    argstr = calloc(maxargvlen, sizeof(*argstr));
    if (argstr == NULL) return POPT_ERROR_MALLOC;

    while (fgets(line, (int)maxlinelen, fp) != NULL) {
	p = line;

	/* loop until first non-space char or EOL */
	while( *p != '\0' && _isspaceptr(p) )
	    p++;

	linelen = strlen(p);
	if (linelen >= maxlinelen-1) {
	    free(argstr);
	    return POPT_ERROR_OVERFLOW;	/* XXX line too long */
	}

	if (*p == '\0' || *p == '\n') continue;	/* line is empty */
	if (*p == '#') continue;		/* comment line */

	q = p;

	while (*q != '\0' && (!_isspaceptr(q)) && *q != '=')
	    q++;

	if (_isspaceptr(q)) {
	    /* a space after the name, find next non space */
	    *q++='\0';
	    while( *q != '\0' && _isspaceptr(q) ) q++;
	}
	if (*q == '\0') {
	    /* single command line option (ie, no name=val, just name) */
	    q[-1] = '\0';		/* kill off newline from fgets() call */
	    argvlen += (t = (size_t)(q - p)) + (sizeof(" --")-1);
	    if (argvlen >= maxargvlen) {
		maxargvlen = (t > maxargvlen) ? t*2 : maxargvlen*2;
		argstr_tmp = realloc(argstr, maxargvlen);
		if (argstr_tmp == NULL) {
		    free(argstr);
		    return POPT_ERROR_MALLOC;
		}
		argstr = argstr_tmp;
	    }
	    strcat(argstr, " --");
	    strcat(argstr, p);
	    continue;
	}
	if (*q != '=')
	    continue;	/* XXX for now, silently ignore bogus line */
		
	/* *q is an equal sign. */
	*q++ = '\0';

	/* find next non-space letter of value */
	while (*q != '\0' && _isspaceptr(q))
	    q++;
	if (*q == '\0')
	    continue;	/* XXX silently ignore missing value */

	/* now, loop and strip all ending whitespace */
	x = p + linelen;
	while (_isspaceptr(--x))
	    *x = '\0';	/* null out last char if space (including fgets() NL) */

	/* rest of line accept */
	t = (size_t)(x - p);
	argvlen += t + (sizeof("' --='")-1);
	if (argvlen >= maxargvlen) {
	    maxargvlen = (t > maxargvlen) ? t*2 : maxargvlen*2;
	    argstr_tmp = realloc(argstr, maxargvlen);
	    if (argstr_tmp == NULL) {
		free(argstr);
		return POPT_ERROR_MALLOC;
	    }
	    argstr = argstr_tmp;
	}
	strcat(argstr, " --");
	strcat(argstr, p);
	strcat(argstr, "=\"");
	strcat(argstr, q);
	strcat(argstr, "\"");
    }

    *argstrp = argstr;
    return 0;
}
