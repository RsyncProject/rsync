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

/* compatability routines for older rsync protocol versions */

#include "rsync.h"

extern int csum_length;

extern int preserve_links;
extern int preserve_perms;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_times;
extern int always_checksum;


extern int remote_version;

 void (*send_file_entry)(struct file_struct *file,int f) = NULL;
 void (*receive_file_entry)(struct file_struct *file,
			    unsigned char flags,int f) = NULL;


void send_file_entry_v10(struct file_struct *file,int f)
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
    write_buf(f,file->sum,csum_length);
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



void receive_file_entry_v10(struct file_struct *file,
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
    read_buf(f,file->sum,csum_length);
  
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




void setup_protocol(void)
{
  if (remote_version == 10) {
    send_file_entry = send_file_entry_v10;
    receive_file_entry = receive_file_entry_v10;
  } else {
    send_file_entry = send_file_entry_v11;
    receive_file_entry = receive_file_entry_v11;
  }
}

