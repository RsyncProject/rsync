/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   Copyright (C) 2002 by Martin Pool <mbp@samba.org>
   
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

extern int dry_run;
extern int verbose;

#if SUPPORT_HARD_LINKS
static int hlink_compare(struct file_struct *f1, struct file_struct *f2)
{
	if (!S_ISREG(f1->mode) && !S_ISREG(f2->mode))
		return 0;
	if (!S_ISREG(f1->mode))
		return -1;
	if (!S_ISREG(f2->mode))
		return 1;

	if (f1->dev != f2->dev)
		return (int) (f1->dev > f2->dev ? 1 : -1);

	if (f1->inode != f2->inode)
		return (int) (f1->inode > f2->inode ? 1 : -1);

	return file_compare(&f1, &f2);
}


static struct file_struct *hlink_list;
static int hlink_count;
#endif

void init_hard_links(struct file_list *flist)
{
#if SUPPORT_HARD_LINKS
	int i;
	if (flist->count < 2)
		return;

	if (hlink_list)
		free(hlink_list);

	if (!(hlink_list = new_array(struct file_struct, flist->count)))
		out_of_memory("init_hard_links");

	for (i = 0; i < flist->count; i++)
		memcpy(&hlink_list[i], flist->files[i],
		       sizeof(hlink_list[0]));

	qsort(hlink_list, flist->count,
	      sizeof(hlink_list[0]), (int (*)()) hlink_compare);

	hlink_count = flist->count;
#endif
}

/* check if a file should be skipped because it is the same as an
   earlier hard link */
int check_hard_link(struct file_struct *file)
{
#if SUPPORT_HARD_LINKS
	int low = 0, high = hlink_count - 1;
	int ret = 0;

	if (!hlink_list || !S_ISREG(file->mode))
		return 0;

	while (low != high) {
		int mid = (low + high) / 2;
		ret = hlink_compare(&hlink_list[mid], file);
		if (ret == 0) {
			low = mid;
			break;
		}
		if (ret > 0)
			high = mid;
		else
			low = mid + 1;
	}

	/* XXX: To me this looks kind of dodgy -- why do we use [low]
	 * here and [low-1] below? -- mbp */
	if (hlink_compare(&hlink_list[low], file) != 0)
		return 0;

	if (low > 0 &&
	    S_ISREG(hlink_list[low - 1].mode) &&
	    file->dev == hlink_list[low - 1].dev &&
	    file->inode == hlink_list[low - 1].inode) {
		if (verbose >= 2) {
			rprintf(FINFO, "check_hard_link: \"%s\" is a hard link to file %d, \"%s\"\n",
				f_name(file), low-1, f_name(&hlink_list[low-1]));
		}
		return 1;
	}
#endif

	return 0;
}


#if SUPPORT_HARD_LINKS
static void hard_link_one(int i)
{
	STRUCT_STAT st1, st2;

	if (link_stat(f_name(&hlink_list[i - 1]), &st1) != 0)
		return;

	if (link_stat(f_name(&hlink_list[i]), &st2) != 0) {
		if (do_link
		    (f_name(&hlink_list[i - 1]),
		     f_name(&hlink_list[i])) != 0) {
			if (verbose > 0)
				rprintf(FINFO, "link %s => %s : %s\n",
					f_name(&hlink_list[i]),
					f_name(&hlink_list[i - 1]),
					strerror(errno));
			return;
		}
	} else {
		if (st2.st_dev == st1.st_dev && st2.st_ino == st1.st_ino)
			return;

		if (robust_unlink(f_name(&hlink_list[i])) != 0 ||
		    do_link(f_name(&hlink_list[i - 1]),
			    f_name(&hlink_list[i])) != 0) {
			if (verbose > 0)
				rprintf(FINFO, "link %s => %s : %s\n",
					f_name(&hlink_list[i]),
					f_name(&hlink_list[i - 1]),
					strerror(errno));
			return;
		}
	}
	if (verbose > 0)
		rprintf(FINFO, "%s => %s\n",
			f_name(&hlink_list[i]),
			f_name(&hlink_list[i - 1]));
}
#endif



/**
 * Create any hard links in the global hlink_list.  They were put
 * there by running init_hard_links on the filelist.
 **/
void do_hard_links(void)
{
#if SUPPORT_HARD_LINKS
	int i;

	if (!hlink_list)
		return;

	for (i = 1; i < hlink_count; i++) {
		if (S_ISREG(hlink_list[i].mode) &&
		    S_ISREG(hlink_list[i - 1].mode) &&
		    hlink_list[i].basename && hlink_list[i - 1].basename &&
		    hlink_list[i].dev == hlink_list[i - 1].dev &&
		    hlink_list[i].inode == hlink_list[i - 1].inode) {
			hard_link_one(i);
		}
	}
#endif
}
