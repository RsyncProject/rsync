/* (C) 1998 Red Hat Software, Inc. -- Licensing details are in the COPYING
   file accompanying popt source distributions, available from 
   ftp://ftp.redhat.com/pub/code/popt */

#include "system.h"
#include "poptint.h"

static void configLine(poptContext con, char * line) {
    int nameLength = strlen(con->appName);
    char * opt;
    struct poptAlias alias;
    char * entryType;
    char * longName = NULL;
    char shortName = '\0';
    
    if (strncmp(line, con->appName, nameLength)) return;
    line += nameLength;
    if (!*line || !isspace(*line)) return;
    while (*line && isspace(*line)) line++;
    entryType = line;

    while (!*line || !isspace(*line)) line++;
    *line++ = '\0';
    while (*line && isspace(*line)) line++;
    if (!*line) return;
    opt = line;

    while (!*line || !isspace(*line)) line++;
    *line++ = '\0';
    while (*line && isspace(*line)) line++;
    if (!*line) return;

    if (opt[0] == '-' && opt[1] == '-')
	longName = opt + 2;
    else if (opt[0] == '-' && !opt[2])
	shortName = opt[1];

    if (!strcmp(entryType, "alias")) {
	if (poptParseArgvString(line, &alias.argc, &alias.argv)) return;
	alias.longName = longName, alias.shortName = shortName;
	poptAddAlias(con, alias, 0);
    } else if (!strcmp(entryType, "exec")) {
	con->execs = realloc(con->execs,
				sizeof(*con->execs) * (con->numExecs + 1));
	if (longName)
	    con->execs[con->numExecs].longName = xstrdup(longName);
	else
	    con->execs[con->numExecs].longName = NULL;

	con->execs[con->numExecs].shortName = shortName;
	con->execs[con->numExecs].script = xstrdup(line);
	
	con->numExecs++;
    }
}

int poptReadConfigFile(poptContext con, const char * fn) {
    char * file=NULL, * chptr, * end;
    char * buf=NULL, * dst;
    int fd, rc;
    int fileLength;

    fd = open(fn, O_RDONLY);
    if (fd < 0) {
	if (errno == ENOENT)
	    return 0;
	else 
	    return POPT_ERROR_ERRNO;
    }

    fileLength = lseek(fd, 0, SEEK_END);
    (void) lseek(fd, 0, 0);

    file = malloc(fileLength + 1);
    if (read(fd, file, fileLength) != fileLength) {
	rc = errno;
	close(fd);
	errno = rc;
	if (file) free(file);
	return POPT_ERROR_ERRNO;
    }
    close(fd);

    dst = buf = malloc(fileLength + 1);

    chptr = file;
    end = (file + fileLength);
    while (chptr < end) {
	switch (*chptr) {
	  case '\n':
	    *dst = '\0';
	    dst = buf;
	    while (*dst && isspace(*dst)) dst++;
	    if (*dst && *dst != '#') {
		configLine(con, dst);
	    }
	    chptr++;
	    break;
	  case '\\':
	    *dst++ = *chptr++;
	    if (chptr < end) {
		if (*chptr == '\n') 
		    dst--, chptr++;	
		    /* \ at the end of a line does not insert a \n */
		else
		    *dst++ = *chptr++;
	    }
	    break;
	  default:
	    *dst++ = *chptr++;
	    break;
	}
    }

    free(file);
    free(buf);

    return 0;
}

int poptReadDefaultConfig(poptContext con, /*@unused@*/ int useEnv) {
    char * fn, * home;
    int rc;

    if (!con->appName) return 0;

    rc = poptReadConfigFile(con, "/etc/popt");
    if (rc) return rc;
    if (getuid() != geteuid()) return 0;

    if ((home = getenv("HOME"))) {
	fn = malloc(strlen(home) + 20);
	strcpy(fn, home);
	strcat(fn, "/.popt");
	rc = poptReadConfigFile(con, fn);
	free(fn);
	if (rc) return rc;
    }

    return 0;
}

