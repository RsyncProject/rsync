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

#include "rsync.h"

extern int am_server;
extern int dry_run;
extern int verbose;

#if SUPPORT_HARD_LINKS
static int hlink_compare(struct file_struct *f1,struct file_struct *f2)
{
  if (!S_ISREG(f1->mode) && !S_ISREG(f2->mode)) return 0;
  if (!S_ISREG(f1->mode)) return -1;
  if (!S_ISREG(f2->mode)) return 1;

  if (f1->dev != f2->dev) 
    return (f1->dev - f2->dev);

  if (f1->inode != f2->inode) 
    return (f1->inode - f2->inode);

  return file_compare(f1,f2);
}


static struct file_struct *hlink_list = NULL;
static int hlink_count=0;
#endif

void init_hard_links(struct file_list *flist)
{
#if SUPPORT_HARD_LINKS
  if (flist->count < 2) return;

  if (hlink_list) free(hlink_list);
    
  if (!(hlink_list = 
	(struct file_struct *)malloc(sizeof(hlink_list[0])*flist->count)))
    out_of_memory("init_hard_links");

  bcopy((char *)flist->files,
	(char *)hlink_list,
	sizeof(hlink_list[0])*flist->count);

  qsort(hlink_list,flist->count,
	sizeof(hlink_list[0]),
	(int (*)())hlink_compare);

  hlink_count=flist->count;
#endif
}

/* check if a file should be skipped because it is the same as an
   earlier hard link */
int check_hard_link(struct file_struct *file)
{
#if SUPPORT_HARD_LINKS
  int low=0,high=hlink_count-1;
  int mid=0,ret=0;

  if (!hlink_list || !S_ISREG(file->mode)) return 0;

  while (low != high) {
    mid = (low+high)/2;
    ret = hlink_compare(&hlink_list[mid],file);
    if (ret == 0) break;
    if (ret > 0) 
      high=mid;
    else
      low=mid+1;
  }

  if (hlink_compare(&hlink_list[mid],file) != 0) return 0;

  if (mid > 0 &&
      S_ISREG(hlink_list[mid-1].mode) &&
      file->dev == hlink_list[mid-1].dev &&
      file->inode == hlink_list[mid-1].inode)
    return 1;
#endif

  return 0;
}


/* create any hard links in the flist */
void do_hard_links(struct file_list *flist)
{
#if SUPPORT_HARD_LINKS
  int i;
  
  if (!hlink_list) return;

  for (i=1;i<hlink_count;i++) {
    if (S_ISREG(hlink_list[i].mode) &&
	S_ISREG(hlink_list[i-1].mode) &&
	hlink_list[i].name && hlink_list[i-1].name &&
	hlink_list[i].dev == hlink_list[i-1].dev &&
	hlink_list[i].inode == hlink_list[i-1].inode) {
      struct stat st1,st2;

      if (link_stat(hlink_list[i-1].name,&st1) != 0) continue;
      if (link_stat(hlink_list[i].name,&st2) != 0) {
	if (!dry_run && link(hlink_list[i-1].name,hlink_list[i].name) != 0) {
		if (verbose > 0)
			fprintf(FINFO,"link %s => %s : %s\n",
				hlink_list[i].name,
				hlink_list[i-1].name,strerror(errno));
	  continue;
	}
      } else {
	if (st2.st_dev == st1.st_dev && st2.st_ino == st1.st_ino) continue;
	
	if (!dry_run && (unlink(hlink_list[i].name) != 0 ||
			 link(hlink_list[i-1].name,hlink_list[i].name) != 0)) {
		if (verbose > 0)
			fprintf(FINFO,"link %s => %s : %s\n",
				hlink_list[i].name,
				hlink_list[i-1].name,strerror(errno));
	  continue;
	}
      }
      if (verbose > 0)
	      fprintf(FINFO,"%s => %s\n",
		      hlink_list[i].name,hlink_list[i-1].name);
    }	
  }
#endif
}
