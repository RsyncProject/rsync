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
extern int make_backups;

#if SUPPORT_HARD_LINKS
static int hlink_compare(struct file_struct **file1, struct file_struct **file2)
{
	struct file_struct *f1 = *file1;
	struct file_struct *f2 = *file2;

	if (f1->F_DEV != f2->F_DEV)
		return (int) (f1->F_DEV > f2->F_DEV ? 1 : -1);

	if (f1->F_INODE != f2->F_INODE)
		return (int) (f1->F_INODE > f2->F_INODE ? 1 : -1);

	return file_compare(file1, file2);
}

static struct file_struct **hlink_list;
static int hlink_count;

#define LINKED(p1,p2) ((p1)->F_DEV == (p2)->F_DEV \
		    && (p1)->F_INODE == (p2)->F_INODE)

/* Analyze the data in the hlink_list[], remove items that aren't multiply
 * linked, and replace the dev+inode data with the hlindex+next linked list. */
static void link_idev_data(struct file_list *flist)
{
	struct file_struct *head;
	int from, to, start;

	alloc_pool_t hlink_pool;
	alloc_pool_t idev_pool = flist->hlink_pool;

	hlink_pool = pool_create(128 * 1024, sizeof (struct hlink),
	    out_of_memory, POOL_INTERN);

	for (from = to = 0; from < hlink_count; from++) {
		start = from;
		head = hlink_list[start];
		while (from < hlink_count-1
		    && LINKED(hlink_list[from], hlink_list[from+1])) {
			pool_free(idev_pool, 0, hlink_list[from]->link_u.idev);
			hlink_list[from]->link_u.links = pool_talloc(hlink_pool,
			    struct hlink, 1, "hlink_list");

			hlink_list[from]->F_HLINDEX = to;
			hlink_list[from]->F_NEXT = hlink_list[from+1];
			from++;
		}
		if (from > start) {
			pool_free(idev_pool, 0, hlink_list[from]->link_u.idev);
			hlink_list[from]->link_u.links = pool_talloc(hlink_pool,
			    struct hlink, 1, "hlink_list");

			hlink_list[from]->F_HLINDEX = to;
			hlink_list[from]->F_NEXT = head;
			hlink_list[from]->flags |= FLAG_HLINK_EOL;
			hlink_list[to++] = head;
		} else {
			pool_free(idev_pool, 0, head->link_u.idev);
			head->link_u.idev = NULL;
		}
	}

	if (!to) {
		free(hlink_list);
		hlink_list = NULL;
		pool_destroy(hlink_pool);
		hlink_pool = NULL;
	} else {
		hlink_count = to;
		if (!(hlink_list = realloc_array(hlink_list,
		    struct file_struct *, hlink_count)))
			out_of_memory("init_hard_links");
	}
	flist->hlink_pool = hlink_pool;
	pool_destroy(idev_pool);
}
#endif

void init_hard_links(struct file_list *flist)
{
#if SUPPORT_HARD_LINKS
	int i;

	if (flist->count < 2)
		return;

	if (hlink_list)
		free(hlink_list);

	if (!(hlink_list = new_array(struct file_struct *, flist->count)))
		out_of_memory("init_hard_links");

	hlink_count = 0;
	for (i = 0; i < flist->count; i++) {
		if (flist->files[i]->link_u.idev)
			hlink_list[hlink_count++] = flist->files[i];
	}

	qsort(hlink_list, hlink_count,
	    sizeof hlink_list[0], (int (*)()) hlink_compare);

	if (!hlink_count) {
		free(hlink_list);
		hlink_list = NULL;
	} else
		link_idev_data(flist);
#endif
}

int hard_link_check(struct file_struct *file, int skip)
{
	if (!hlink_list || !file->link_u.links)
		return 0;
	if (skip && !(file->flags & FLAG_HLINK_EOL))
		hlink_list[file->F_HLINDEX] = file->F_NEXT;
	if (hlink_list[file->F_HLINDEX] != file) {
		if (verbose > 1) {
			rprintf(FINFO, "\"%s\" is a hard link\n",
			    f_name(file));
		}
		return 1;
	}
	return 0;
}

#if SUPPORT_HARD_LINKS
static void hard_link_one(char *hlink1, char *hlink2)
{
	if (do_link(hlink1, hlink2)) {
		if (verbose) {
			rprintf(FINFO, "link %s => %s failed: %s\n",
			    hlink2, hlink1, strerror(errno));
		}
	}
	else if (verbose)
		rprintf(FINFO, "%s => %s\n", hlink2, hlink1);
}
#endif



/**
 * Create any hard links in the global hlink_list.  They were put
 * there by running init_hard_links on the filelist.
 **/
void do_hard_links(void)
{
#if SUPPORT_HARD_LINKS
	struct file_struct *file, *first;
	char hlink1[MAXPATHLEN];
	char *hlink2;
	STRUCT_STAT st1, st2;
	int i;

	if (!hlink_list)
		return;

	for (i = 0; i < hlink_count; i++) {
		first = file = hlink_list[i];
		if (link_stat(f_name_to(first, hlink1), &st1) != 0)
			continue;
		while ((file = file->F_NEXT) != first) {
			hlink2 = f_name(file);
			if (link_stat(hlink2, &st2) == 0) {
				if (st2.st_dev == st1.st_dev
				    && st2.st_ino == st1.st_ino)
					continue;
				if (make_backups) {
					if (!make_backup(hlink2))
						continue;
				} else if (robust_unlink(hlink2)) {
					if (verbose > 0) {
						rprintf(FINFO,
						    "unlink %s failed: %s\n",
						    full_fname(hlink2), 
						    strerror(errno));
					}
					continue;
				}
			}
			hard_link_one(hlink1, hlink2);
		}
	}
#endif
}
