/*
 * Routines to support hard-linking.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2004, 2005, 2006 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#include "rsync.h"

extern int verbose;
extern int do_xfers;
extern int link_dest;
extern int make_backups;
extern int remove_source_files;
extern int stdout_format_has_i;
extern char *basis_dir[];
extern struct file_list *the_file_list;

#ifdef SUPPORT_HARD_LINKS

#define SKIPPED_LINK (-1)
#define FINISHED_LINK (-2)

#define FPTR(i) (the_file_list->files[i])
#define LINKED(p1,p2) (FPTR(p1)->F_DEV == FPTR(p2)->F_DEV \
		    && FPTR(p1)->F_INODE == FPTR(p2)->F_INODE)

static int hlink_compare(int *int1, int *int2)
{
	struct file_struct *f1 = FPTR(*int1);
	struct file_struct *f2 = FPTR(*int2);

	if (f1->F_DEV != f2->F_DEV)
		return (int) (f1->F_DEV > f2->F_DEV ? 1 : -1);

	if (f1->F_INODE != f2->F_INODE)
		return (int) (f1->F_INODE > f2->F_INODE ? 1 : -1);

	return f_name_cmp(f1, f2);
}

static int32 *hlink_list;
static int32 hlink_count;

/* Analyze the data in the hlink_list[], remove items that aren't multiply
 * linked, and replace the dev+inode data with the hlindex+next linked list. */
static void link_idev_data(void)
{
	int32 cur, from, to, start;

	alloc_pool_t hlink_pool;
	alloc_pool_t idev_pool = the_file_list->hlink_pool;

	hlink_pool = pool_create(128 * 1024, sizeof (struct hlink),
	    out_of_memory, POOL_INTERN);

	for (from = to = 0; from < hlink_count; from++) {
		start = from;
		while (1) {
			cur = hlink_list[from];
			if (from == hlink_count-1
			    || !LINKED(cur, hlink_list[from+1]))
				break;
			pool_free(idev_pool, 0, FPTR(cur)->link_u.idev);
			FPTR(cur)->link_u.links = pool_talloc(hlink_pool,
			    struct hlink, 1, "hlink_list");

			FPTR(cur)->F_HLINDEX = to;
			FPTR(cur)->F_NEXT = hlink_list[++from];
			FPTR(cur)->link_u.links->link_dest_used = 0;
		}
		pool_free(idev_pool, 0, FPTR(cur)->link_u.idev);
		if (from > start) {
			int head = hlink_list[start];
			FPTR(cur)->link_u.links = pool_talloc(hlink_pool,
			    struct hlink, 1, "hlink_list");

			FPTR(head)->flags |= FLAG_HLINK_TOL;
			FPTR(cur)->F_HLINDEX = to;
			FPTR(cur)->F_NEXT = head;
			FPTR(cur)->flags |= FLAG_HLINK_EOL;
			FPTR(cur)->link_u.links->link_dest_used = 0;
			hlink_list[to++] = head;
		} else
			FPTR(cur)->link_u.links = NULL;
	}

	if (!to) {
		free(hlink_list);
		hlink_list = NULL;
		pool_destroy(hlink_pool);
		hlink_pool = NULL;
	} else {
		hlink_count = to;
		hlink_list = realloc_array(hlink_list, int32, hlink_count);
		if (!hlink_list)
			out_of_memory("init_hard_links");
	}
	the_file_list->hlink_pool = hlink_pool;
	pool_destroy(idev_pool);
}
#endif

void init_hard_links(void)
{
#ifdef SUPPORT_HARD_LINKS
	int i;

	if (hlink_list)
		free(hlink_list);

	if (!(hlink_list = new_array(int32, the_file_list->count)))
		out_of_memory("init_hard_links");

	hlink_count = 0;
	for (i = 0; i < the_file_list->count; i++) {
		if (FPTR(i)->link_u.idev)
			hlink_list[hlink_count++] = i;
	}

	qsort(hlink_list, hlink_count,
	    sizeof hlink_list[0], (int (*)()) hlink_compare);

	if (!hlink_count) {
		free(hlink_list);
		hlink_list = NULL;
	} else
		link_idev_data();
#endif
}

#ifdef SUPPORT_HARD_LINKS
static int maybe_hard_link(struct file_struct *file, int ndx,
			   char *fname, int statret, STRUCT_STAT *st,
			   char *toname, STRUCT_STAT *to_st,
			   int itemizing, enum logcode code)
{
	if (statret == 0) {
		if (st->st_dev == to_st->st_dev
		 && st->st_ino == to_st->st_ino) {
			if (itemizing) {
				itemize(file, ndx, statret, st,
					ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS,
					0, "");
			}
			return 0;
		}
		if (make_backups) {
			if (!make_backup(fname))
				return -1;
		} else if (robust_unlink(fname)) {
			rsyserr(FERROR, errno, "unlink %s failed",
				full_fname(fname));
			return -1;
		}
	}
	return hard_link_one(file, ndx, fname, statret, st, toname,
			     0, itemizing, code);
}
#endif

int hard_link_check(struct file_struct *file, int ndx, char *fname,
		    int statret, STRUCT_STAT *st, int itemizing,
		    enum logcode code, int skip)
{
#ifdef SUPPORT_HARD_LINKS
	int head;
	if (skip && !(file->flags & FLAG_HLINK_EOL))
		head = hlink_list[file->F_HLINDEX] = file->F_NEXT;
	else
		head = hlink_list[file->F_HLINDEX];
	if (ndx != head) {
		struct file_struct *head_file = FPTR(head);
		if (!stdout_format_has_i && verbose > 1) {
			rprintf(FINFO, "\"%s\" is a hard link\n",
				f_name(file, NULL));
		}
		if (head_file->F_HLINDEX == FINISHED_LINK) {
			STRUCT_STAT st2, st3;
			char toname[MAXPATHLEN];
			int ldu = head_file->link_u.links->link_dest_used;
			if (ldu) {
				pathjoin(toname, MAXPATHLEN, basis_dir[ldu-1],
					 f_name(head_file, NULL));
			} else
				f_name(head_file, toname);
			if (link_stat(toname, &st2, 0) < 0) {
				rsyserr(FERROR, errno, "stat %s failed",
					full_fname(toname));
				return -1;
			}
			if (statret < 0 && basis_dir[0] != NULL) {
				char cmpbuf[MAXPATHLEN];
				int j = 0;
				do {
					pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
					if (link_stat(cmpbuf, &st3, 0) < 0)
						continue;
					if (link_dest) {
						if (st2.st_dev != st3.st_dev
						 || st2.st_ino != st3.st_ino)
							continue;
						statret = 1;
						st = &st3;
						if (verbose < 2 || !stdout_format_has_i) {
							itemizing = 0;
							code = FNONE;
						}
						break;
					}
					if (!unchanged_file(cmpbuf, file, &st3))
						continue;
					statret = 1;
					st = &st3;
					if (unchanged_attrs(file, &st3))
						break;
				} while (basis_dir[++j] != NULL);
			}
			maybe_hard_link(file, ndx, fname, statret, st,
					toname, &st2, itemizing, code);
			if (remove_source_files == 1 && do_xfers) {
				char numbuf[4];
				SIVAL(numbuf, 0, ndx);
				send_msg(MSG_SUCCESS, numbuf, 4);
			}
			file->F_HLINDEX = FINISHED_LINK;
		} else
			file->F_HLINDEX = SKIPPED_LINK;
		return 1;
	}
#endif
	return 0;
}

#ifdef SUPPORT_HARD_LINKS
int hard_link_one(struct file_struct *file, int ndx, char *fname,
		  int statret, STRUCT_STAT *st, char *toname, int terse,
		  int itemizing, enum logcode code)
{
	if (do_link(toname, fname)) {
		if (terse) {
			if (!verbose)
				return -1;
			code = FINFO;
		} else
			code = FERROR;
		rsyserr(code, errno, "link %s => %s failed",
			full_fname(fname), toname);
		return -1;
	}

	if (itemizing) {
		itemize(file, ndx, statret, st,
			ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS, 0,
			terse ? "" : toname);
	}
	if (code != FNONE && verbose && !terse)
		rprintf(code, "%s => %s\n", fname, toname);
	return 0;
}
#endif


void hard_link_cluster(struct file_struct *file, int master, int itemizing,
		       enum logcode code)
{
#ifdef SUPPORT_HARD_LINKS
	char hlink1[MAXPATHLEN];
	char *hlink2;
	STRUCT_STAT st1, st2;
	int statret, ndx = master;

	file->F_HLINDEX = FINISHED_LINK;
	if (link_stat(f_name(file, hlink1), &st1, 0) < 0)
		return;
	if (!(file->flags & FLAG_HLINK_TOL)) {
		while (!(file->flags & FLAG_HLINK_EOL)) {
			ndx = file->F_NEXT;
			file = FPTR(ndx);
		}
	}
	do {
		ndx = file->F_NEXT;
		file = FPTR(ndx);
		if (file->F_HLINDEX != SKIPPED_LINK)
			continue;
		hlink2 = f_name(file, NULL);
		statret = link_stat(hlink2, &st2, 0);
		maybe_hard_link(file, ndx, hlink2, statret, &st2,
				hlink1, &st1, itemizing, code);
		if (remove_source_files == 1 && do_xfers) {
			char numbuf[4];
			SIVAL(numbuf, 0, ndx);
			send_msg(MSG_SUCCESS, numbuf, 4);
		}
		file->F_HLINDEX = FINISHED_LINK;
	} while (!(file->flags & FLAG_HLINK_EOL));
#endif
}
