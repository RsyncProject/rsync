/* 
   Copyright (C) Andrew Tridgell 1999
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/* backup handling code */

#include "rsync.h"

extern int verbose;
extern char *backup_suffix;


int make_backup(char *fname)
{
	char fnamebak[MAXPATHLEN];
	if (strlen(fname) + strlen(backup_suffix) > (MAXPATHLEN-1)) {
		rprintf(FERROR,"backup filename too long\n");
		return 0;
	}

	slprintf(fnamebak,sizeof(fnamebak),"%s%s",fname,backup_suffix);
	if (do_rename(fname,fnamebak) != 0) {
		/* cygwin (at least version b19) reports EINVAL */
		if (errno != ENOENT && errno != EINVAL) {
			rprintf(FERROR,"rename %s %s : %s\n",fname,fnamebak,strerror(errno));
			return 0;
		}
	} else if (verbose > 1) {
		rprintf(FINFO,"backed up %s to %s\n",fname,fnamebak);
	}
	return 1;
}
