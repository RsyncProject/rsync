/* 
   Copyright (C) Andrew Tridgell 1998
   
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

/* support the max connections option */
#include "rsync.h"

int yield_connection(char *fname, int max_connections)
{
	int fd, i;
	pid_t mypid=getpid(), pid=0;

	if (max_connections <= 0)
		return 1;

	fd = open(fname,O_RDWR);
	if (fd == -1) {
		rprintf(FERROR,"Couldn't open lock file %s (%s)\n",fname,strerror(errno));
		return 0;
	}

	if (!lock_file(fd)) {
		rprintf(FERROR,"failed to lock %s\n", fname);
		close(fd);
		return 0;
	}

	/* find the right spot */
	for (i=0;i<max_connections;i++) {
		if (read(fd, &pid, sizeof(pid)) != sizeof(pid)) {
			unlock_file(fd);
			close(fd);
			return 0;
		}
		if (pid == mypid) break;
	}

	if (i == max_connections) {
		rprintf(FERROR,"Entry not found in lock file %s\n",fname);
		unlock_file(fd);
		close(fd);
		return 0;
	}

	pid = 0;
  
	/* remove our mark */
	if (lseek(fd,i*sizeof(pid),SEEK_SET) != i*sizeof(pid) ||
	    write(fd, &pid,sizeof(pid)) != sizeof(pid)) {
		rprintf(FERROR,"Couldn't update lock file %s (%s)\n",fname,strerror(errno));
		unlock_file(fd);
		close(fd);
		return 0;
	}

	unlock_file(fd);
	close(fd);
	return 1;
}


/****************************************************************************
simple routine to do connection counting
****************************************************************************/
int claim_connection(char *fname,int max_connections)
{
	int fd, i;
	pid_t pid;

	if (max_connections <= 0)
		return 1;
	
	fd = open(fname,O_RDWR|O_CREAT, 0600);

	if (fd == -1) {
		return 0;
	}

	if (!lock_file(fd)) {
		rprintf(FERROR,"failed to lock %s\n", fname);
		close(fd);
		return 0;
	}

	/* find a free spot */
	for (i=0;i<max_connections;i++) {
		if (read(fd,&pid,sizeof(pid)) != sizeof(pid)) break;
		if (pid == 0 || !process_exists(pid)) break;
	}		

	if (i == max_connections) {
		unlock_file(fd);
		close(fd);
		return 0;
	}

	pid = getpid();

	if (lseek(fd,i*sizeof(pid),SEEK_SET) != i*sizeof(pid) ||
	    write(fd, &pid,sizeof(pid)) != sizeof(pid)) {
		unlock_file(fd);
		close(fd);
		return 0;
	}

	unlock_file(fd);
	close(fd);
	return 1;
}
