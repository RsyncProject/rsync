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

/* generate and receive file lists */

#include "rsync.h"

extern int verbose;
extern int am_server;
extern int always_checksum;
extern off_t total_size;

extern int cvs_exclude;

extern int one_file_system;
extern int make_backups;
extern int preserve_links;
extern int preserve_perms;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_times;

static char **local_exclude_list = NULL;

static void clean_fname(char *name);


/*
  This function is used to check if a file should be included/excluded
  from the list of files based on its name and type etc
 */
static int match_file_name(char *fname,struct stat *st)
{
  if (check_exclude(fname,local_exclude_list)) {
    if (verbose > 2)
      fprintf(stderr,"excluding file %s\n",fname);
    return 0;
  }
  return 1;
}

/* used by the one_file_system code */
static dev_t filesystem_dev;

static void set_filesystem(char *fname)
{
  struct stat st;
  if (lstat(fname,&st) != 0) return;
  filesystem_dev = st.st_dev;
}


static void send_directory(int f,struct file_list *flist,char *dir);

static char *flist_dir = NULL;

#define FILE_VALID 1
#define SAME_MODE (1<<1)
#define SAME_DEV (1<<2)
#define SAME_UID (1<<3)
#define SAME_GID (1<<4)
#define SAME_DIR (1<<5)

static void send_file_entry(struct file_struct *file,int f)
{
  unsigned char flags;
  static mode_t last_mode=0;
  static dev_t last_dev=0;
  static uid_t last_uid=0;
  static gid_t last_gid=0;
  static char lastdir[MAXPATHLEN]="";
  char *p=NULL;

  if (f == -1) return;

  if (!file) {
    write_byte(f,0);
    return;
  }

  flags = FILE_VALID;

  if (file->mode == last_mode) flags |= SAME_MODE;
  if (file->dev == last_dev) flags |= SAME_DEV;
  if (file->uid == last_uid) flags |= SAME_UID;
  if (file->gid == last_gid) flags |= SAME_GID;
    
  if (strncmp(file->name,lastdir,strlen(lastdir)) == 0) {
    flags |= SAME_DIR;
    p = file->name + strlen(lastdir);
  } else {
    p = file->name;
  }

  write_byte(f,flags);
  if (flags & SAME_DIR)
    write_byte(f,strlen(p));
  else
    write_int(f,strlen(p));
  write_buf(f,p,strlen(p));
  write_int(f,(int)file->modtime);
  write_int(f,(int)file->length);
  if (!(flags & SAME_MODE))
    write_int(f,(int)file->mode);
  if (preserve_uid && !(flags & SAME_UID))
    write_int(f,(int)file->uid);
  if (preserve_gid && !(flags & SAME_GID))
    write_int(f,(int)file->gid);
  if (preserve_devices && IS_DEVICE(file->mode) && !(flags & SAME_DEV))
    write_int(f,(int)file->dev);

#if SUPPORT_LINKS
  if (preserve_links && S_ISLNK(file->mode)) {
    write_int(f,strlen(file->link));
    write_buf(f,file->link,strlen(file->link));
  }
#endif

  if (always_checksum) {
    write_buf(f,file->sum,SUM_LENGTH);
  }       

  last_mode = file->mode;
  last_dev = file->dev;
  last_uid = file->uid;
  last_gid = file->gid;
  p = strrchr(file->name,'/');
  if (p) {
    int l = (int)(p - file->name) + 1;
    strncpy(lastdir,file->name,l);
    lastdir[l] = 0;
  } else {
    strcpy(lastdir,"");
  }
}



static void receive_file_entry(struct file_struct *file,
			       unsigned char flags,int f)
{
  static mode_t last_mode=0;
  static dev_t last_dev=0;
  static uid_t last_uid=0;
  static gid_t last_gid=0;
  static char lastdir[MAXPATHLEN]="";
  char *p=NULL;
  int l1,l2;

  if (flags & SAME_DIR) {
    l1 = read_byte(f);
    l2 = strlen(lastdir);
  } else {
    l1 = read_int(f);
    l2 = 0;
  }

  file->name = (char *)malloc(l1+l2+1);
  if (!file->name) out_of_memory("receive_file_entry");

  strncpy(file->name,lastdir,l2);
  read_buf(f,file->name+l2,l1);
  file->name[l1+l2] = 0;

  file->modtime = (time_t)read_int(f);
  file->length = (off_t)read_int(f);
  file->mode = (flags & SAME_MODE) ? last_mode : (mode_t)read_int(f);
  if (preserve_uid)
    file->uid = (flags & SAME_UID) ? last_uid : (uid_t)read_int(f);
  if (preserve_gid)
    file->gid = (flags & SAME_GID) ? last_gid : (gid_t)read_int(f);
  if (preserve_devices && IS_DEVICE(file->mode))
    file->dev = (flags & SAME_DEV) ? last_dev : (dev_t)read_int(f);

#if SUPPORT_LINKS
  if (preserve_links && S_ISLNK(file->mode)) {
    int l = read_int(f);
    file->link = (char *)malloc(l+1);
    if (!file->link) out_of_memory("receive_file_entry");
    read_buf(f,file->link,l);
    file->link[l] = 0;
  }
#endif
  
  if (always_checksum)
    read_buf(f,file->sum,SUM_LENGTH);
  
  last_mode = file->mode;
  last_dev = file->dev;
  last_uid = file->uid;
  last_gid = file->gid;
  p = strrchr(file->name,'/');
  if (p) {
    int l = (int)(p - file->name) + 1;
    strncpy(lastdir,file->name,l);
    lastdir[l] = 0;
  } else {
    strcpy(lastdir,"");
  }
}


static struct file_struct *make_file(int recurse,char *fname)
{
  static struct file_struct file;
  struct stat st;
  char sum[SUM_LENGTH];

  bzero(sum,SUM_LENGTH);

  if (lstat(fname,&st) != 0) {
    fprintf(stderr,"%s: %s\n",
	    fname,strerror(errno));
    return NULL;
  }

  if (S_ISDIR(st.st_mode) && !recurse) {
    fprintf(stderr,"skipping directory %s\n",fname);
    return NULL;
  }

  if (one_file_system && st.st_dev != filesystem_dev)
    return NULL;

  if (!match_file_name(fname,&st))
    return NULL;

  if (verbose > 2)
    fprintf(stderr,"make_file(%s)\n",fname);

  file.name = strdup(fname);
  file.modtime = st.st_mtime;
  file.length = st.st_size;
  file.mode = st.st_mode;
  file.uid = st.st_uid;
  file.gid = st.st_gid;
#ifdef HAVE_ST_RDEV
  file.dev = st.st_rdev;
#endif

#if SUPPORT_LINKS
  if (S_ISLNK(st.st_mode)) {
    int l;
    char lnk[MAXPATHLEN];
    if ((l=readlink(fname,lnk,MAXPATHLEN-1)) == -1) {
      fprintf(stderr,"readlink %s : %s\n",fname,strerror(errno));
      return NULL;
    }
    lnk[l] = 0;
    file.link = strdup(lnk);
  }
#endif

  if (always_checksum && S_ISREG(st.st_mode)) {
    file_checksum(fname,file.sum,st.st_size);
  }       

  if (flist_dir)
    file.dir = strdup(flist_dir);
  else
    file.dir = NULL;

  if (!S_ISDIR(st.st_mode))
    total_size += st.st_size;

  return &file;
}



static void send_file_name(int f,struct file_list *flist,
			   int recurse,char *fname)
{
  struct file_struct *file;

  file = make_file(recurse,fname);

  if (!file) return;  
  
  if (flist->count >= flist->malloced) {
    flist->malloced += 100;
    flist->files = (struct file_struct *)realloc(flist->files,
						 sizeof(flist->files[0])*
						 flist->malloced);
    if (!flist->files)
      out_of_memory("send_file_name");
  }

  if (strcmp(file->name,".") && strcmp(file->name,"/")) {
    flist->files[flist->count++] = *file;    
    send_file_entry(file,f);
  }

  if (S_ISDIR(file->mode) && recurse) {
    char **last_exclude_list = local_exclude_list;
    send_directory(f,flist,file->name);
    local_exclude_list = last_exclude_list;
    return;
  }
}



static void send_directory(int f,struct file_list *flist,char *dir)
{
  DIR *d;
  struct dirent *di;
  char fname[MAXPATHLEN];
  int l;
  char *p;

  d = opendir(dir);
  if (!d) {
    fprintf(stderr,"%s: %s\n",
	    dir,strerror(errno));
    return;
  }

  strcpy(fname,dir);
  l = strlen(fname);
  if (fname[l-1] != '/')
    strcat(fname,"/");
  p = fname + strlen(fname);

  if (cvs_exclude) {
    strcpy(p,".cvsignore");
    local_exclude_list = make_exclude_list(fname,NULL,0);
  }  

  for (di=readdir(d); di; di=readdir(d)) {
    if (strcmp(di->d_name,".")==0 ||
	strcmp(di->d_name,"..")==0)
      continue;
    strcpy(p,di->d_name);
    send_file_name(f,flist,1,fname);
  }

  closedir(d);
}



struct file_list *send_file_list(int f,int recurse,int argc,char *argv[])
{
  int i,l;
  struct stat st;
  char *p,*dir;
  char dbuf[MAXPATHLEN];
  struct file_list *flist;

  if (verbose && recurse) {
    fprintf(am_server?stderr:stdout,"building file list ... ");
    fflush(am_server?stderr:stdout);
  }

  flist = (struct file_list *)malloc(sizeof(flist[0]));
  if (!flist) out_of_memory("send_file_list");

  flist->count=0;
  flist->malloced = 100;
  flist->files = (struct file_struct *)malloc(sizeof(flist->files[0])*
					      flist->malloced);
  if (!flist->files) out_of_memory("send_file_list");

  for (i=0;i<argc;i++) {
    char fname2[MAXPATHLEN];
    char *fname = fname2;

    strcpy(fname,argv[i]);

    l = strlen(fname);
    if (l != 1 && fname[l-1] == '/') {
      strcat(fname,".");
    }

    if (lstat(fname,&st) != 0) {
      fprintf(stderr,"%s : %s\n",fname,strerror(errno));
      continue;
    }

    if (S_ISDIR(st.st_mode) && !recurse) {
      fprintf(stderr,"skipping directory %s\n",fname);
      continue;
    }

    dir = NULL;
    p = strrchr(fname,'/');
    if (p) {
      *p = 0;
      dir = fname;
      fname = p+1;      
    }
    if (!*fname)
      fname = ".";

    if (dir && *dir) {
      if (getcwd(dbuf,MAXPATHLEN-1) == NULL) {
	fprintf(stderr,"getwd : %s\n",strerror(errno));
	exit(1);
      }
      if (chdir(dir) != 0) {
	fprintf(stderr,"chdir %s : %s\n",dir,strerror(errno));
	continue;
      }
      flist_dir = dir;
      if (one_file_system)
	set_filesystem(fname);
      send_file_name(f,flist,recurse,fname);
      flist_dir = NULL;
      if (chdir(dbuf) != 0) {
	fprintf(stderr,"chdir %s : %s\n",dbuf,strerror(errno));
	exit(1);
      }
      continue;
    }

    if (one_file_system)
      set_filesystem(fname);
    send_file_name(f,flist,recurse,fname);
  }

  if (f != -1) {
    send_file_entry(NULL,f);
    write_flush(f);
  }

  clean_flist(flist);

  if (verbose && recurse)
    fprintf(am_server?stderr:stdout,"done\n");

  return flist;
}


struct file_list *recv_file_list(int f)
{
  struct file_list *flist;
  unsigned char flags;

  if (verbose > 2)
    fprintf(stderr,"recv_file_list starting\n");

  flist = (struct file_list *)malloc(sizeof(flist[0]));
  if (!flist)
    goto oom;

  flist->count=0;
  flist->malloced=100;
  flist->files = (struct file_struct *)malloc(sizeof(flist->files[0])*
					      flist->malloced);
  if (!flist->files)
    goto oom;


  for (flags=read_byte(f); flags; flags=read_byte(f)) {
    int i = flist->count;

    if (i >= flist->malloced) {
      flist->malloced += 100;
      flist->files =(struct file_struct *)realloc(flist->files,
						  sizeof(flist->files[0])*
						  flist->malloced);
      if (!flist->files)
	goto oom;
    }

    receive_file_entry(&flist->files[i],flags,f);

    if (S_ISREG(flist->files[i].mode))
      total_size += flist->files[i].length;

    flist->count++;

    if (verbose > 2)
      fprintf(stderr,"recv_file_name(%s)\n",flist->files[i].name);
  }


  if (verbose > 2)
    fprintf(stderr,"received %d names\n",flist->count);

  clean_flist(flist);

  return flist;

oom:
    out_of_memory("recv_file_list");
    return NULL; /* not reached */
}


static int flist_compare(struct file_struct *f1,struct file_struct *f2)
{
  if (!f1->name && !f2->name) return 0;
  if (!f1->name) return -1;
  if (!f2->name) return 1;
  return strcmp(f1->name,f2->name);
}


int flist_find(struct file_list *flist,struct file_struct *f)
{
  int low=0,high=flist->count;

  while (low != high) {
    int mid = (low+high)/2;
    int ret = flist_compare(&flist->files[mid],f);
    if (ret == 0) return mid;
    if (ret > 0) 
      high=mid;
    else
      low=mid+1;
  }
  if (flist_compare(&flist->files[low],f) == 0)
    return low;
  return -1;
}


static void clean_fname(char *name)
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
      while (*p) {
	p[0] = p[2];
	p++;
      }
    }

    l = strlen(p=name);
    if (l > 1 && p[l-1] == '/') {
      modified = 1;
      p[l-1] = 0;
    }
  }
}


/*
 * This routine ensures we don't have any duplicate names in our file list.
 * duplicate names can cause corruption because of the pipelining 
 */
void clean_flist(struct file_list *flist)
{
  int i;

  if (!flist || flist->count == 0) 
    return;
  
  for (i=0;i<flist->count;i++) {
    clean_fname(flist->files[i].name);
  }
      
  qsort(flist->files,flist->count,
	sizeof(flist->files[0]),
	(int (*)())flist_compare);

  for (i=1;i<flist->count;i++) {
    if (flist->files[i].name &&
	strcmp(flist->files[i].name,flist->files[i-1].name) == 0) {
      if (verbose > 1 && !am_server)
	fprintf(stderr,"removing duplicate name %s from file list\n",
		flist->files[i].name);
      free(flist->files[i-1].name);
      flist->files[i-1].name = NULL;
    }
  }
}

