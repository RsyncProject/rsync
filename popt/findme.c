/* (C) 1998 Red Hat Software, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.redhat.com/pub/code/popt */

#include "system.h"
#include "findme.h"

const char * findProgramPath(const char * argv0) {
    char * path = getenv("PATH");
    char * pathbuf;
    char * start, * chptr;
    char * buf, *local = NULL;

    /* If there is a / in the argv[0], it has to be an absolute
       path */
    if (strchr(argv0, '/'))
	return xstrdup(argv0);

    if (!path) return NULL;

    local = start = pathbuf = malloc(strlen(path) + 1);
    buf = malloc(strlen(path) + strlen(argv0) + 2);
    strcpy(pathbuf, path);

    chptr = NULL;
    do {
	if ((chptr = strchr(start, ':')))
	    *chptr = '\0';
	sprintf(buf, "%s/%s", start, argv0);

	if (!access(buf, X_OK)) {
		if (local) free(local);
		return buf;
	}

	if (chptr) 
	    start = chptr + 1;
	else
	    start = NULL;
    } while (start && *start);

    free(buf);
    if (local) free(local);

    return NULL;
}
