/* (C) 1998 Red Hat Software, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.redhat.com/pub/code/popt */

#include "system.h"

#define POPT_ARGV_ARRAY_GROW_DELTA 5

int poptDupArgv(int argc, const char **argv,
		int * argcPtr, const char *** argvPtr)
{
    size_t nb = (argc + 1) * sizeof(*argv);
    const char ** argv2;
    char * dst;
    int i;

    for (i = 0; i < argc; i++) {
	if (argv[i] == NULL)
	    return POPT_ERROR_NOARG;
	nb += strlen(argv[i]) + 1;
    }
	
    dst = malloc(nb);
    argv2 = (void *) dst;
    dst += (argc + 1) * sizeof(*argv);

    for (i = 0; i < argc; i++) {
	argv2[i] = dst;
	dst += strlen(strcpy(dst, argv[i])) + 1;
    }
    argv2[argc] = NULL;

    *argvPtr = argv2;
    *argcPtr = argc;
    return 0;
}

int poptParseArgvString(const char * s, int * argcPtr, const char *** argvPtr)
{
    const char * src;
    char quote = '\0';
    int argvAlloced = POPT_ARGV_ARRAY_GROW_DELTA;
    const char ** argv = malloc(sizeof(*argv) * argvAlloced);
    int argc = 0;
    int buflen = strlen(s) + 1;
    char *buf0 = calloc(buflen, 1);
    char *buf = buf0;

    argv[argc] = buf;

    for (src = s; *src; src++) {
	if (quote == *src) {
	    quote = '\0';
	} else if (quote) {
	    if (*src == '\\') {
		src++;
		if (!*src) {
		    free(argv);
		    free(buf0);
		    return POPT_ERROR_BADQUOTE;
		}
		if (*src != quote) *buf++ = '\\';
	    }
	    *buf++ = *src;
	} else if (isspace(*src)) {
	    if (*argv[argc]) {
		buf++, argc++;
		if (argc == argvAlloced) {
		    argvAlloced += POPT_ARGV_ARRAY_GROW_DELTA;
		    argv = realloc(argv, sizeof(*argv) * argvAlloced);
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
		free(argv);
		free(buf0);
		return POPT_ERROR_BADQUOTE;
	    }
	    /*@fallthrough@*/
	  default:
	    *buf++ = *src;
	    break;
	}
    }

    if (strlen(argv[argc])) {
	argc++, buf++;
    }

    (void) poptDupArgv(argc, argv, argcPtr, argvPtr);

    free(argv);
    free(buf0);
    return 0;
}
