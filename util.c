/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
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

/*
  Utilities used in rsync 

  tridge, June 1996
  */
#include "rsync.h"

int num_waiting(int fd)
{
  int len=0;
  ioctl(fd,FIONREAD,&len);
  return(len);
}


struct map_struct *map_file(int fd,OFF_T len)
{
  struct map_struct *ret;
  ret = (struct map_struct *)malloc(sizeof(*ret));
  if (!ret) out_of_memory("map_file");

  ret->map = NULL;
  ret->fd = fd;
  ret->size = len;
  ret->p = NULL;
  ret->p_size = 0;
  ret->p_offset = 0;
  ret->p_len = 0;

#ifdef HAVE_MMAP
  if (len < MAX_MAP_SIZE) {
	  ret->map = (char *)mmap(NULL,len,PROT_READ,MAP_SHARED,fd,0);
	  if (ret->map == (char *)-1) {
		  ret->map = NULL;
	  }
  }
#endif
  return ret;
}


char *map_ptr(struct map_struct *map,OFF_T offset,int len)
{
  int nread = -2;

  if (map->map)
    return map->map+offset;

  if (len == 0) 
    return NULL;

  if (len > (map->size-offset))
      len = map->size-offset;

  if (offset >= map->p_offset && 
      offset+len <= map->p_offset+map->p_len) {
    return (map->p + (offset - map->p_offset));
  }

  len = MAX(len,CHUNK_SIZE);
  if (len > (map->size-offset))
      len = map->size-offset;

  if (len > map->p_size) {
    if (map->p) free(map->p);
    map->p = (char *)malloc(len);
    if (!map->p) out_of_memory("map_ptr");
    map->p_size = len;
  }

  if (do_lseek(map->fd,offset,SEEK_SET) != offset ||
      (nread=read(map->fd,map->p,len)) != len) {
	  rprintf(FERROR,"EOF in map_ptr! (offset=%d len=%d nread=%d errno=%d)\n",
		  (int)offset, len, nread, errno);
	  exit_cleanup(1);
  }

  map->p_offset = offset;
  map->p_len = len;

  return map->p; 
}


void unmap_file(struct map_struct *map)
{
#ifdef HAVE_MMAP
  if (map->map)
    munmap(map->map,map->size);
#endif
  if (map->p) free(map->p);
  free(map);
}


/* this is taken from CVS */
int piped_child(char **command,int *f_in,int *f_out)
{
  int pid;
  int to_child_pipe[2];
  int from_child_pipe[2];

  if (pipe(to_child_pipe) < 0 ||
      pipe(from_child_pipe) < 0) {
    rprintf(FERROR,"pipe: %s\n",strerror(errno));
    exit_cleanup(1);
  }


  pid = do_fork();
  if (pid < 0) {
    rprintf(FERROR,"fork: %s\n",strerror(errno));
    exit_cleanup(1);
  }

  if (pid == 0)
    {
      extern int orig_umask;
      if (dup2(to_child_pipe[0], STDIN_FILENO) < 0 ||
	  close(to_child_pipe[1]) < 0 ||
	  close(from_child_pipe[0]) < 0 ||
	  dup2(from_child_pipe[1], STDOUT_FILENO) < 0) {
	rprintf(FERROR,"Failed to dup/close : %s\n",strerror(errno));
	exit_cleanup(1);
      }
      if (to_child_pipe[0] != STDIN_FILENO) close(to_child_pipe[0]);
      if (from_child_pipe[1] != STDOUT_FILENO) close(from_child_pipe[1]);
      umask(orig_umask);
      execvp(command[0], command);
      rprintf(FERROR,"Failed to exec %s : %s\n",
	      command[0],strerror(errno));
      exit_cleanup(1);
    }

  if (close(from_child_pipe[1]) < 0 ||
      close(to_child_pipe[0]) < 0) {
    rprintf(FERROR,"Failed to close : %s\n",strerror(errno));   
    exit_cleanup(1);
  }

  *f_in = from_child_pipe[0];
  *f_out = to_child_pipe[1];
  
  return pid;
}

int local_child(int argc, char **argv,int *f_in,int *f_out)
{
	int pid;
	int to_child_pipe[2];
	int from_child_pipe[2];

	if (pipe(to_child_pipe) < 0 ||
	    pipe(from_child_pipe) < 0) {
		rprintf(FERROR,"pipe: %s\n",strerror(errno));
		exit_cleanup(1);
	}


	pid = do_fork();
	if (pid < 0) {
		rprintf(FERROR,"fork: %s\n",strerror(errno));
		exit_cleanup(1);
	}

	if (pid == 0) {
		extern int am_sender;
		extern int am_server;

		am_sender = !am_sender;
		am_server = 1;		

		if (dup2(to_child_pipe[0], STDIN_FILENO) < 0 ||
		    close(to_child_pipe[1]) < 0 ||
		    close(from_child_pipe[0]) < 0 ||
		    dup2(from_child_pipe[1], STDOUT_FILENO) < 0) {
			rprintf(FERROR,"Failed to dup/close : %s\n",strerror(errno));
			exit_cleanup(1);
		}
		if (to_child_pipe[0] != STDIN_FILENO) close(to_child_pipe[0]);
		if (from_child_pipe[1] != STDOUT_FILENO) close(from_child_pipe[1]);
		start_server(STDIN_FILENO, STDOUT_FILENO, argc, argv);
	}

	if (close(from_child_pipe[1]) < 0 ||
	    close(to_child_pipe[0]) < 0) {
		rprintf(FERROR,"Failed to close : %s\n",strerror(errno));   
		exit_cleanup(1);
	}

	*f_in = from_child_pipe[0];
	*f_out = to_child_pipe[1];
  
	return pid;
}



void out_of_memory(char *str)
{
  rprintf(FERROR,"ERROR: out of memory in %s\n",str);
  exit_cleanup(1);
}

void overflow(char *str)
{
  rprintf(FERROR,"ERROR: buffer overflow in %s\n",str);
  exit_cleanup(1);
}



int set_modtime(char *fname,time_t modtime)
{
	extern int dry_run;
	if (dry_run) return 0;
	{
#ifdef HAVE_UTIMBUF
		struct utimbuf tbuf;  
		tbuf.actime = time(NULL);
		tbuf.modtime = modtime;
		return utime(fname,&tbuf);
#elif defined(HAVE_UTIME)
		time_t t[2];
		t[0] = time(NULL);
		t[1] = modtime;
		return utime(fname,t);
#else
		struct timeval t[2];
		t[0].tv_sec = time(NULL);
		t[0].tv_usec = 0;
		t[1].tv_sec = modtime;
		t[1].tv_usec = 0;
		return utimes(fname,t);
#endif
	}
}



/****************************************************************************
Set a fd into blocking/nonblocking mode. Uses POSIX O_NONBLOCK if available,
else
if SYSV use O_NDELAY
if BSD use FNDELAY
****************************************************************************/
int set_blocking(int fd, int set)
{
  int val;
#ifdef O_NONBLOCK
#define FLAG_TO_SET O_NONBLOCK
#else
#ifdef SYSV
#define FLAG_TO_SET O_NDELAY
#else /* BSD */
#define FLAG_TO_SET FNDELAY
#endif
#endif

  if((val = fcntl(fd, F_GETFL, 0)) == -1)
	return -1;
  if(set) /* Turn blocking on - ie. clear nonblock flag */
	val &= ~FLAG_TO_SET;
  else
    val |= FLAG_TO_SET;
  return fcntl( fd, F_SETFL, val);
#undef FLAG_TO_SET
}

/****************************************************************************
create any necessary directories in fname. Unfortunately we don't know
what perms to give the directory when this is called so we need to rely
on the umask
****************************************************************************/
int create_directory_path(char *fname)
{
	extern int orig_umask;
	char *p;

	while (*fname == '/') fname++;
	while (strncmp(fname,"./",2)==0) fname += 2;

	p = fname;
	while ((p=strchr(p,'/'))) {
		*p = 0;
		do_mkdir(fname,0777 & ~orig_umask); 
		*p = '/';
		p++;
	}
	return 0;
}


/* Write LEN bytes at PTR to descriptor DESC, retrying if interrupted.
   Return LEN upon success, write's (negative) error code otherwise.  

   derived from GNU C's cccp.c.
*/
int full_write(int desc, char *ptr, int len)
{
	int total_written;
	
	total_written = 0;
	while (len > 0) {
		int written = write (desc, ptr, len);
		if (written < 0)  {
#ifdef EINTR
			if (errno == EINTR)
				continue;
#endif
			return written;
		}
		total_written += written;
		ptr += written;
		len -= written;
	}
	return total_written;
}

/* Read LEN bytes at PTR from descriptor DESC, retrying if interrupted.
   Return the actual number of bytes read, zero for EOF, or negative
   for an error.  

   derived from GNU C's cccp.c. */
int safe_read(int desc, char *ptr, int len)
{
	int n_chars;
 
	if (len <= 0)
		return len;
 
#ifdef EINTR
	do {
		n_chars = read(desc, ptr, len);
	} while (n_chars < 0 && errno == EINTR);
#else
	n_chars = read(desc, ptr, len);
#endif
 
	return n_chars;
}


/* copy a file - this is used in conjunction with the --temp-dir option */
int copy_file(char *source, char *dest, mode_t mode)
{
	int ifd;
	int ofd;
	char buf[1024 * 8];
	int len;   /* Number of bytes read into `buf'. */

	ifd = open(source, O_RDONLY);
	if (ifd == -1) {
		rprintf(FERROR,"open %s: %s\n",
			source,strerror(errno));
		return -1;
	}

	if (do_unlink(dest) && errno != ENOENT) {
		rprintf(FERROR,"unlink %s: %s\n",
			dest,strerror(errno));
		return -1;
	}

	ofd = do_open(dest, O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, mode);
	if (ofd < 0) {
		rprintf(FERROR,"open %s: %s\n",
			dest,strerror(errno));
		close(ifd);
		return -1;
	}

	while ((len = safe_read(ifd, buf, sizeof(buf))) > 0) {
		if (full_write(ofd, buf, len) < 0) {
			rprintf(FERROR,"write %s: %s\n",
				dest,strerror(errno));
			close(ifd);
			close(ofd);
			return -1;
		}
	}

	close(ifd);
	close(ofd);

	if (len < 0) {
		rprintf(FERROR,"read %s: %s\n",
			source,strerror(errno));
		return -1;
	}

	return 0;
}

/* sleep for a while via select */
void u_sleep(int usec)
{
	struct timeval tv;

	tv.tv_sec = 0;
	tv.tv_usec = usec;
	select(0, NULL, NULL, NULL, &tv);
}


static pid_t all_pids[10];
static int num_pids;

/* fork and record the pid of the child */
pid_t do_fork(void)
{
	pid_t newpid = fork();
	
	if (newpid) {
		all_pids[num_pids++] = newpid;
	}
	return newpid;
}

/* kill all children */
void kill_all(int sig)
{
	int i;
	for (i=0;i<num_pids;i++) {
		if (all_pids[i] != getpid())
			kill(all_pids[i], sig);
	}
}

/* like strncpy but does not 0 fill the buffer and always null 
   terminates (thus it can use maxlen+1 space in d) */
void strlcpy(char *d, char *s, int maxlen)
{
	int len = strlen(s);
	if (len > maxlen) len = maxlen;
	memcpy(d, s, len);
	d[len] = 0;
}

/* turn a user name into a uid */
int name_to_uid(char *name, uid_t *uid)
{
	struct passwd *pass;
	if (!name || !*name) return 0;
	pass = getpwnam(name);
	if (pass) {
		*uid = pass->pw_uid;
		return 1;
	}
	return 0;
}

/* turn a group name into a gid */
int name_to_gid(char *name, gid_t *gid)
{
	struct group *grp;
	if (!name || !*name) return 0;
	grp = getgrnam(name);
	if (grp) {
		*gid = grp->gr_gid;
		return 1;
	}
	return 0;
}


/****************************************************************************
check if a process exists. 
****************************************************************************/
int process_exists(int pid)
{
	return(kill(pid,0) == 0 || errno != ESRCH);
}

/* lock a byte range in a open file */
int lock_range(int fd, int offset, int len)
{
	struct flock lock;

	lock.l_type = F_WRLCK;
	lock.l_whence = SEEK_SET;
	lock.l_start = offset;
	lock.l_len = len;
	lock.l_pid = 0;
	
	return fcntl(fd,F_SETLK,&lock) == 0;
}


void glob_expand(char **argv, int *argc, int maxargs)
{
#ifndef HAVE_GLOB
	(*argc)++;
	return;
#else
	glob_t globbuf;
	int i;

	memset(&globbuf, 0, sizeof(globbuf));
	glob(argv[*argc], 0, NULL, &globbuf);
	if (globbuf.gl_pathc == 0) {
		(*argc)++;
		globfree(&globbuf);
		return;
	}
	for (i=0; i<(maxargs - (*argc)) && i<globbuf.gl_pathc;i++) {
		if (i == 0) free(argv[*argc]);
		argv[(*argc) + i] = strdup(globbuf.gl_pathv[i]);
		if (!argv[(*argc) + i]) out_of_memory("glob_expand");
	}
	globfree(&globbuf);
	(*argc) += i;
#endif
}
