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

extern int verbose;

/****************************************************************************
Set a fd into nonblocking mode. Uses POSIX O_NONBLOCK if available,
else
if SYSV use O_NDELAY
if BSD use FNDELAY
****************************************************************************/
int set_nonblocking(int fd)
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
	val |= FLAG_TO_SET;
	return fcntl( fd, F_SETFL, val);
#undef FLAG_TO_SET
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
    exit_cleanup(RERR_IPC);
  }


  pid = do_fork();
  if (pid < 0) {
    rprintf(FERROR,"fork: %s\n",strerror(errno));
    exit_cleanup(RERR_IPC);
  }

  if (pid == 0)
    {
      extern int orig_umask;
      if (dup2(to_child_pipe[0], STDIN_FILENO) < 0 ||
	  close(to_child_pipe[1]) < 0 ||
	  close(from_child_pipe[0]) < 0 ||
	  dup2(from_child_pipe[1], STDOUT_FILENO) < 0) {
	rprintf(FERROR,"Failed to dup/close : %s\n",strerror(errno));
	exit_cleanup(RERR_IPC);
      }
      if (to_child_pipe[0] != STDIN_FILENO) close(to_child_pipe[0]);
      if (from_child_pipe[1] != STDOUT_FILENO) close(from_child_pipe[1]);
      umask(orig_umask);
      execvp(command[0], command);
      rprintf(FERROR,"Failed to exec %s : %s\n",
	      command[0],strerror(errno));
      exit_cleanup(RERR_IPC);
    }

  if (close(from_child_pipe[1]) < 0 ||
      close(to_child_pipe[0]) < 0) {
    rprintf(FERROR,"Failed to close : %s\n",strerror(errno));   
    exit_cleanup(RERR_IPC);
  }

  *f_in = from_child_pipe[0];
  *f_out = to_child_pipe[1];

  set_nonblocking(*f_in);
  set_nonblocking(*f_out);
  
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
		exit_cleanup(RERR_IPC);
	}


	pid = do_fork();
	if (pid < 0) {
		rprintf(FERROR,"fork: %s\n",strerror(errno));
		exit_cleanup(RERR_IPC);
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
			exit_cleanup(RERR_IPC);
		}
		if (to_child_pipe[0] != STDIN_FILENO) close(to_child_pipe[0]);
		if (from_child_pipe[1] != STDOUT_FILENO) close(from_child_pipe[1]);
		start_server(STDIN_FILENO, STDOUT_FILENO, argc, argv);
	}

	if (close(from_child_pipe[1]) < 0 ||
	    close(to_child_pipe[0]) < 0) {
		rprintf(FERROR,"Failed to close : %s\n",strerror(errno));   
		exit_cleanup(RERR_IPC);
	}

	*f_in = from_child_pipe[0];
	*f_out = to_child_pipe[1];
  
	return pid;
}



void out_of_memory(char *str)
{
  rprintf(FERROR,"ERROR: out of memory in %s\n",str);
  exit_cleanup(RERR_MALLOC);
}

void overflow(char *str)
{
  rprintf(FERROR,"ERROR: buffer overflow in %s\n",str);
  exit_cleanup(RERR_MALLOC);
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
static int full_write(int desc, char *ptr, int len)
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
static int safe_read(int desc, char *ptr, int len)
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

	if (robust_unlink(dest) && errno != ENOENT) {
		rprintf(FERROR,"unlink %s: %s\n",
			dest,strerror(errno));
		return -1;
	}

	ofd = do_open(dest, O_WRONLY | O_CREAT | O_TRUNC | O_EXCL, mode);
	if (ofd == -1) {
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

/*
  Robust unlink: some OS'es (HPUX) refuse to unlink busy files, so
  rename to <path>/.rsyncNNN instead. Note that successive rsync runs
  will shuffle the filenames around a bit as long as the file is still
  busy; this is because this function does not know if the unlink call
  is due to a new file coming in, or --delete trying to remove old
  .rsyncNNN files, hence it renames it each time.
*/
/* MAX_RENAMES should be 10**MAX_RENAMES_DIGITS */
#define MAX_RENAMES_DIGITS 3
#define MAX_RENAMES 1000

int robust_unlink(char *fname)
{
#ifndef ETXTBSY
	return do_unlink(fname);
#else
	static int counter = 1;
	int rc, pos, start;
	char path[MAXPATHLEN];

	rc = do_unlink(fname);
	if ((rc == 0) || (errno != ETXTBSY))
		return rc;

	strlcpy(path, fname, MAXPATHLEN);

	pos = strlen(path);
	while((path[--pos] != '/') && (pos >= 0))
		;
	++pos;
	strlcpy(&path[pos], ".rsync", MAXPATHLEN-pos);
	pos += sizeof(".rsync")-1;

	if (pos > (MAXPATHLEN-MAX_RENAMES_DIGITS-1)) {
		errno = ETXTBSY;
		return -1;
	}

	/* start where the last one left off to reduce chance of clashes */
	start = counter;
	do {
		sprintf(&path[pos], "%03d", counter);
		if (++counter >= MAX_RENAMES)
			counter = 1;
	} while (((rc = access(path, 0)) == 0) && (counter != start));

	if (verbose > 0)
		rprintf(FINFO,"renaming %s to %s because of text busy\n",
					    fname, path);

	/* maybe we should return rename()'s exit status? Nah. */
	if (do_rename(fname, path) != 0) {
		errno = ETXTBSY;
		return -1;
	}
	return 0;
#endif
}

int robust_rename(char *from, char *to)
{
#ifndef ETXTBSY
	return do_rename(from, to);
#else
	int rc = do_rename(from, to);
	if ((rc == 0) || (errno != ETXTBSY))
		return rc;
	if (robust_unlink(to) != 0)
		return -1;
	return do_rename(from, to);
#endif
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


static void glob_expand_one(char *s, char **argv, int *argc, int maxargs, int sanitize_paths)
{
#if !(defined(HAVE_GLOB) && defined(HAVE_GLOB_H))
	if (!*s) s = ".";
	argv[*argc] = strdup(s);
	(*argc)++;
	return;
#else
	glob_t globbuf;
	int i;

	if (!*s) s = ".";

	s = strdup(s);
	sanitize_path(s);
	argv[*argc] = s;

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

void glob_expand(char *base1, char **argv, int *argc, int maxargs, int sanitize_paths)
{
	char *s = argv[*argc];
	char *p, *q;
	char *base = base1;

	if (!s || !*s) return;

	if (strncmp(s, base, strlen(base)) == 0) {
		s += strlen(base);
	}

	s = strdup(s);
	if (!s) out_of_memory("glob_expand");

	base = (char *)malloc(strlen(base1)+3);
	if (!base) out_of_memory("glob_expand");

	sprintf(base," %s/", base1);

	q = s;
	while ((p = strstr(q,base)) && ((*argc) < maxargs)) {
		/* split it at this point */
		*p = 0;
		glob_expand_one(q, argv, argc, maxargs, sanitize_paths);
		q = p+strlen(base);
	}

	if (*q && (*argc < maxargs)) glob_expand_one(q, argv, argc, maxargs, sanitize_paths);

	free(s);
	free(base);
}

/*******************************************************************
  convert a string to lower case
********************************************************************/
void strlower(char *s)
{
	while (*s) {
		if (isupper(*s)) *s = tolower(*s);
		s++;
	}
}

/* this is like vsnprintf but it always null terminates, so you
   can fit at most n-1 chars in */
int vslprintf(char *str, int n, const char *format, va_list ap)
{
	int ret = vsnprintf(str, n, format, ap);
	if (ret >= n || ret < 0) {
		str[n-1] = 0;
		return -1;
	}
	str[ret] = 0;
	return ret;
}


/* like snprintf but always null terminates */
int slprintf(char *str, int n, char *format, ...)
{
	va_list ap;  
	int ret;

	va_start(ap, format);
	ret = vslprintf(str,n,format,ap);
	va_end(ap);
	return ret;
}


void *Realloc(void *p, int size)
{
	if (!p) return (void *)malloc(size);
	return (void *)realloc(p, size);
}


void clean_fname(char *name)
{
	char *p;
	int l;
	int modified = 1;

	if (!name) return;

	while (modified) {
		modified = 0;

		if ((p=strstr(name,"/./"))) {
			modified = 1;
			while (*p) {
				p[0] = p[2];
				p++;
			}
		}

		if ((p=strstr(name,"//"))) {
			modified = 1;
			while (*p) {
				p[0] = p[1];
				p++;
			}
		}

		if (strncmp(p=name,"./",2) == 0) {      
			modified = 1;
			do {
				p[0] = p[2];
			} while (*p++);
		}

		l = strlen(p=name);
		if (l > 1 && p[l-1] == '/') {
			modified = 1;
			p[l-1] = 0;
		}
	}
}

/*
 * Make path appear as if a chroot had occurred:
 *    1. remove leading "/" (or replace with "." if at end)
 *    2. remove leading ".." components
 *    3. delete any other "<dir>/.." (recursively)
 * While we're at it, remove double slashes and "." components like
 *   clean_fname does(), but DON'T remove a trailing slash because that
 *   is sometimes significant on command line arguments.
 * Can only shrink paths, so sanitizes in place.
 * Contributed by Dave Dykstra <dwd@bell-labs.com>
 */

void sanitize_path(char *p)
{
	char *start, *sanp;

	start = p;
	sanp = p;
	while (*p == '/') {
		/* remove leading slashes */
		p++;
	}
	while (*p != '\0') {
		/* this loop iterates once per filename component in p.
		 * both p (and sanp if the original had a slash) should
		 * always be left pointing after a slash
		 */
		if ((*p == '.') && ((*(p+1) == '/') || (*(p+1) == '\0'))) {
			/* skip "." component */
			while (*++p == '/') {
				/* skip following slashes */
				;
			}
		} else if ((*p == '.') && (*(p+1) == '.') &&
			    ((*(p+2) == '/') || (*(p+2) == '\0'))) {
			/* skip ".." component followed by slash or end */
			p += 2;
			if (*p == '/')
				p++;
			if (sanp != start) {
				/* back up sanp one level */
				--sanp; /* now pointing at slash */
				while ((sanp > start) && (*(sanp - 1) != '/')) {
					/* skip back up to slash */
					sanp--;
				}
			}
		} else {
			while (1) {
				/* copy one component through next slash */
				*sanp++ = *p++;
				if ((*p == '\0') || (*(p-1) == '/')) {
					while (*p == '/') {
						/* skip multiple slashes */
						p++;
					}
					break;
				}
			}
		}
	}
	if (sanp == start) {
		/* ended up with nothing, so put in "." component */
		*sanp++ = '.';
	}
	*sanp = '\0';
}


static char curr_dir[MAXPATHLEN];

/* like chdir() but can be reversed with pop_dir() if save is set. It
   is also much faster as it remembers where we have been */
char *push_dir(char *dir, int save)
{
	char *ret = curr_dir;
	static int initialised;

	if (!initialised) {
		initialised = 1;
		getcwd(curr_dir, sizeof(curr_dir)-1);
	}

	if (!dir) return NULL; /* this call was probably just to initialize */

	if (chdir(dir)) return NULL;

	if (save) {
		ret = strdup(curr_dir);
	}

	if (*dir == '/') {
		strlcpy(curr_dir, dir, sizeof(curr_dir));
	} else {
		strlcat(curr_dir,"/", sizeof(curr_dir));
		strlcat(curr_dir,dir, sizeof(curr_dir));
	}

	clean_fname(curr_dir);

	return ret;
}

/* reverse a push_dir call */
int pop_dir(char *dir)
{
	int ret;

	ret = chdir(dir);
	if (ret) {
		free(dir);
		return ret;
	}

	strlcpy(curr_dir, dir, sizeof(curr_dir));

	free(dir);

	return 0;
}

/* we need to supply our own strcmp function for file list comparisons
   to ensure that signed/unsigned usage is consistent between machines. */
int u_strcmp(const char *cs1, const char *cs2)
{
	const uchar *s1 = (const uchar *)cs1;
	const uchar *s2 = (const uchar *)cs2;

	while (*s1 && *s2 && (*s1 == *s2)) {
		s1++; s2++;
	}
	
	return (int)*s1 - (int)*s2;
}

static OFF_T last_ofs;

void end_progress(void)
{
	extern int do_progress, am_server;

	if (do_progress && !am_server) {
		rprintf(FINFO,"\n");
	}
	last_ofs = 0;
}

void show_progress(OFF_T ofs, OFF_T size)
{
	extern int do_progress, am_server;

	if (do_progress && !am_server) {
		if (ofs > last_ofs + 1000) {
			int pct = (int)((100.0*ofs)/size);
			rprintf(FINFO,"%.0f (%d%%)\r", (double)ofs, pct);
			last_ofs = ofs;
		}
	}
}

/* determine if a symlink points outside the current directory tree */
int unsafe_symlink(char *dest, char *src)
{
	char *tok;
	int depth = 0;

	/* all absolute and null symlinks are unsafe */
	if (!dest || !(*dest) || (*dest == '/')) return 1;

	src = strdup(src);
	if (!src) out_of_memory("unsafe_symlink");

	/* find out what our safety margin is */
	for (tok=strtok(src,"/"); tok; tok=strtok(NULL,"/")) {
		if (strcmp(tok,"..") == 0) {
			depth=0;
		} else if (strcmp(tok,".") == 0) {
			/* nothing */
		} else {
			depth++;
		}
	}
	free(src);

	/* drop by one to account for the filename portion */
	depth--;

	dest = strdup(dest);
	if (!dest) out_of_memory("unsafe_symlink");

	for (tok=strtok(dest,"/"); tok; tok=strtok(NULL,"/")) {
		if (strcmp(tok,"..") == 0) {
			depth--;
		} else if (strcmp(tok,".") == 0) {
			/* nothing */
		} else {
			depth++;
		}
		/* if at any point we go outside the current directory then
		   stop - it is unsafe */
		if (depth < 0) break;
	}

	free(dest);
	return (depth < 0);
}


/****************************************************************************
  return the date and time as a string
****************************************************************************/
char *timestring(time_t t)
{
	static char TimeBuf[200];
	struct tm *tm = localtime(&t);

#ifdef HAVE_STRFTIME
	strftime(TimeBuf,sizeof(TimeBuf)-1,"%Y/%m/%d %T",tm);
#else
	strlcpy(TimeBuf, asctime(tm), sizeof(TimeBuf));
#endif

	if (TimeBuf[strlen(TimeBuf)-1] == '\n') {
		TimeBuf[strlen(TimeBuf)-1] = 0;
	}

	return(TimeBuf);
}

