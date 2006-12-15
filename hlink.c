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
extern int dry_run;
extern int do_xfers;
extern int link_dest;
extern int make_backups;
extern int protocol_version;
extern int remove_source_files;
extern int stdout_format_has_i;
extern int maybe_ATTRS_REPORT;
extern char *basis_dir[];
extern struct file_list *the_file_list;

#ifdef SUPPORT_HARD_LINKS

alloc_pool_t hlink_pool;

#define HASH_LOAD_LIMIT(size) ((size)*3/4)
#define FPTR(i) (the_file_list->files[i])

struct ihash_table {
	int32 size;
	int32 entries;
	struct idev_node *buckets;
} *dev_tbl;

static struct idev_node *ihash_node(struct ihash_table *tbl, int64 key);

/* Starting with protocol 30, we use a simple hashtable on the sending side
 * for hashing the st_dev and st_ino info.  The receiving side gets told
 * (via flags and a "group index") which items are hard-linked together, so
 * we can avoid the pool of dev+inode data. */

static struct ihash_table *ihash_create(int size)
{
	struct ihash_table *tbl;

	/* Pick a power of 2 that can hold the requested size. */
	if (size & (size-1)) {
		int req = size;
		size = 32;
		while (size < req)
			size *= 2;
	}

	if (!(tbl = new(struct ihash_table))
	 || !(tbl->buckets = new_array(struct idev_node, size)))
		out_of_memory("ihash_create");
	memset(tbl->buckets, 0, size * sizeof tbl->buckets[0]);
	tbl->size = size;
	tbl->entries = 0;

	return tbl;
}

static void ihash_destroy(struct ihash_table *tbl)
{
	free(tbl->buckets);
	free(tbl);
}

void init_hard_links(void)
{
	if (protocol_version >= 30) {
		dev_tbl = ihash_create(16);
		return;
	}

	if (!(hlink_pool = pool_create(HLINK_EXTENT, sizeof (struct idev),
				       out_of_memory, POOL_INTERN)))
		out_of_memory("init_hard_links");
}

static void expand_ihash(struct ihash_table *tbl)
{
	struct idev_node *old_buckets;
	int size = tbl->size * 2;
	int i;

	old_buckets = tbl->buckets;
	if (!(tbl->buckets = new_array(struct idev_node, size)))
		out_of_memory("ihash_create");
	memset(tbl->buckets, 0, size * sizeof tbl->buckets[0]);
	tbl->size = size;
	tbl->entries = 0;

	for (i = size / 2; i-- > 0; ) {
		int64 key = old_buckets[i].key;
		if (key == 0)
			continue;
		ihash_node(tbl, key)->data = old_buckets[i].data;
	}

	free(old_buckets);
}

/* This returns the node for the indicated key, either newly created,
 * or already existing. */
static struct idev_node *ihash_node(struct ihash_table *tbl, int64 key)
{
	uint32 bkt;

	if (tbl->entries > HASH_LOAD_LIMIT(tbl->size))
		expand_ihash(tbl);

#if SIZEOF_INT64 < 8
	/* Based on Jenkins One-at-a-time hash. */
	{
		uchar *keyp = (uchar*)&key;
		int i;

		for (bkt = 0, i = 0; i < SIZEOF_INT64; i++) {
			bkt += keyp[i];
			bkt += (bkt << 10);
			bkt ^= (bkt >> 6);
		}
		bkt += (bkt << 3);
		bkt ^= (bkt >> 11);
		bkt += (bkt << 15);
	}
#else
#define rot(x,k) (((x)<<(k)) ^ ((x)>>(32-(k))))
	/* Based on Jenkins hashword() from lookup3.c. */
	{
		uint32 a, b, c;

		/* Set up the internal state */
		a = b = c = 0xdeadbeef + (8 << 2);

		b += (uint32)(key >> 32);
		a += (uint32)key;
		c ^= b; c -= rot(b, 14);
		a ^= c; a -= rot(c, 11);
		b ^= a; b -= rot(a, 25);
		c ^= b; c -= rot(b, 16);
		a ^= c; a -= rot(c, 4);
		b ^= a; b -= rot(a, 14);
		c ^= b; c -= rot(b, 24);
		bkt = c;
	}
#endif

	/* If it already exists, return it. */
	while (1) {
		bkt &= tbl->size - 1;
		if (tbl->buckets[bkt].key == key)
			return &tbl->buckets[bkt];
		if (tbl->buckets[bkt].key == 0)
			break;
		bkt++;
	}

	/* Otherwise, take over this empty spot and then return it. */
	tbl->buckets[bkt].key = key;
	tbl->entries++;
	return &tbl->buckets[bkt];
}

struct idev_node *idev_node(int64 dev, int64 ino)
{
	static struct idev_node *dev_node = NULL;
	struct ihash_table *tbl;

	if (!dev_node || dev_node->key != dev) {
		/* We keep a separate hash table of inodes for every device. */
		dev_node = ihash_node(dev_tbl, dev);
		if (!(tbl = dev_node->data))
			tbl = dev_node->data = ihash_create(512);
	} else
		tbl = dev_node->data;

	return ihash_node(tbl, ino);
}

void idev_destroy(void)
{
	int i;

	for (i = 0; i < dev_tbl->size; i++) {
		if (dev_tbl->buckets[i].data)
			ihash_destroy(dev_tbl->buckets[i].data);
	}

	ihash_destroy(dev_tbl);
}

/* This routine compares dev+inode info for protocols < 30. */
static int hlink_compare_idev(int *int1, int *int2)
{
	struct file_struct *f1 = FPTR(*int1);
	struct file_struct *f2 = FPTR(*int2);
	struct idev *i1 = F_HL_IDEV(f1);
	struct idev *i2 = F_HL_IDEV(f2);

	if (i1->dev != i2->dev)
		return i1->dev > i2->dev ? 1 : -1;

	if (i1->ino != i2->ino)
		return i1->ino > i2->ino ? 1 : -1;

	return *int1 > *int2 ? 1 : -1;
}

/* This routine compares group numbers for protocols >= 30. */
static int hlink_compare_gnum(int *int1, int *int2)
{
	struct file_struct *f1 = FPTR(*int1);
	struct file_struct *f2 = FPTR(*int2);
	int gnum1 = F_HL_GNUM(f1);
	int gnum2 = F_HL_GNUM(f2);

	if (gnum1 != gnum2)
		return gnum1 > gnum2 ? 1 : -1;

	return *int1 > *int2 ? 1 : -1;
}

/* The match routine for protocols < 30. */
static void match_idevs(int32 *ndx_list, int ndx_count)
{
	int32 from, prev;
	struct file_struct *file, *file_next;
	struct idev *idev, *idev_next;

	qsort(ndx_list, ndx_count, sizeof ndx_list[0],
	     (int (*)()) hlink_compare_idev);

	for (from = 0; from < ndx_count; from++) {
		for (file = FPTR(ndx_list[from]), idev = F_HL_IDEV(file), prev = -1;
		     from < ndx_count-1;
		     file = file_next, idev = idev_next, prev = ndx_list[from++])
		{
			file_next = FPTR(ndx_list[from+1]);
			idev_next = F_HL_IDEV(file_next);
			if (idev->dev != idev_next->dev || idev->ino != idev_next->ino)
				break;
			pool_free(hlink_pool, 0, idev);
			if (prev < 0)
				file->flags |= FLAG_HLINK_FIRST;
			F_HL_PREV(file) = prev;
		}
		pool_free(hlink_pool, 0, idev);
		if (prev < 0)
			file->flags &= ~FLAG_HLINKED;
		else {
			file->flags |= FLAG_HLINK_LAST;
			F_HL_PREV(file) = prev;
		}
	}
}

/* The match routine for protocols >= 30. */
static void match_gnums(int32 *ndx_list, int ndx_count)
{
	int32 from, prev;
	struct file_struct *file, *file_next;
	int gnum, gnum_next;

	qsort(ndx_list, ndx_count, sizeof ndx_list[0],
	     (int (*)()) hlink_compare_gnum);

	for (from = 0; from < ndx_count; from++) {
		for (file = FPTR(ndx_list[from]), gnum = F_HL_GNUM(file), prev = -1;
		     from < ndx_count-1;
		     file = file_next, gnum = gnum_next, prev = ndx_list[from++])
		{
			file_next = FPTR(ndx_list[from+1]);
			gnum_next = F_HL_GNUM(file_next);
			if (gnum != gnum_next)
				break;
			if (prev < 0)
				file->flags |= FLAG_HLINK_FIRST;
			F_HL_PREV(file) = prev;
		}
		if (prev < 0)
			file->flags &= ~FLAG_HLINKED;
		else {
			file->flags |= FLAG_HLINK_LAST;
			F_HL_PREV(file) = prev;
		}
	}
}

/* Analyze the hard-links in the file-list by creating a list of all the
 * items that have hlink data, sorting them, and matching up identical
 * values into clusters.  These will be a single linked list from last
 * to first when we're done. */
void match_hard_links(void)
{
	int i, ndx_count = 0;
	int32 *ndx_list;

	if (!(ndx_list = new_array(int32, the_file_list->count)))
		out_of_memory("match_hard_links");

	for (i = 0; i < the_file_list->count; i++) {
		if (F_IS_HLINKED(FPTR(i)))
			ndx_list[ndx_count++] = i;
	}

	if (ndx_count) {
		if (protocol_version >= 30)
			match_gnums(ndx_list, ndx_count);
		else {
			match_idevs(ndx_list, ndx_count);
			pool_destroy(hlink_pool);
		}
	}
	free(ndx_list);
}

static int maybe_hard_link(struct file_struct *file, int ndx,
			   const char *fname, int statret, STRUCT_STAT *stp,
			   const char *oldname, STRUCT_STAT *old_stp,
			   const char *realname, int itemizing, enum logcode code)
{
	if (statret == 0) {
		if (stp->st_dev == old_stp->st_dev
		 && stp->st_ino == old_stp->st_ino) {
			if (itemizing) {
				itemize(file, ndx, statret, stp,
					ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS,
					0, "");
			}
			if (verbose > 1 && maybe_ATTRS_REPORT)
				rprintf(FCLIENT, "%s is uptodate\n", fname);
			file->flags |= FLAG_HLINK_DONE;
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

	if (hard_link_one(file, fname, oldname, 0)) {
		if (itemizing) {
			itemize(file, ndx, statret, stp,
				ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS, 0,
				realname);
		}
		if (code != FNONE && verbose)
			rprintf(code, "%s => %s\n", fname, realname);
		return 0;
	}
	return -1;
}

/* Only called if FLAG_HLINKED is set and FLAG_HLINK_FIRST is not.  Returns:
 * 0 = process the file, 1 = skip the file, -1 = error occurred. */
int hard_link_check(struct file_struct *file, int ndx, const char *fname,
		    int statret, STRUCT_STAT *stp, int itemizing,
		    enum logcode code)
{
	STRUCT_STAT prev_st;
	char prev_name[MAXPATHLEN], altbuf[MAXPATHLEN], *realname;
	int alt_dest, prev_ndx = F_HL_PREV(file);
	struct file_struct *prev_file = FPTR(prev_ndx);

	/* Is the previous link is not complete yet? */
	if (!(prev_file->flags & FLAG_HLINK_DONE)) {
		/* Is the previous link being transferred? */
		if (prev_file->flags & FLAG_SENT) {
			/* Add ourselves to the list of files that will be
			 * updated when the transfer completes, and mark
			 * ourself as waiting for the transfer. */
			F_HL_PREV(file) = F_HL_PREV(prev_file);
			F_HL_PREV(prev_file) = ndx;
			file->flags |= FLAG_SENT;
			return 1;
		}
		return 0;
	}

	/* There is a finished file to link with! */
	if (!(prev_file->flags & FLAG_HLINK_FIRST)) {
		/* The previous previous will be marked with FIRST. */
		prev_ndx = F_HL_PREV(prev_file);
		prev_file = FPTR(prev_ndx);
		/* Update our previous pointer to point to the first. */
		F_HL_PREV(file) = prev_ndx;
	}
	alt_dest = F_HL_PREV(prev_file); /* alternate value when DONE && FIRST */
	if (alt_dest >= 0 && dry_run) {
		pathjoin(prev_name, MAXPATHLEN, basis_dir[alt_dest],
			 f_name(prev_file, NULL));
		f_name(prev_file, altbuf);
		realname = altbuf;
	} else {
		f_name(prev_file, prev_name);
		realname = prev_name;
	}

	if (link_stat(prev_name, &prev_st, 0) < 0) {
		rsyserr(FERROR, errno, "stat %s failed",
			full_fname(prev_name));
		return -1;
	}

	if (statret < 0 && basis_dir[0] != NULL) {
		/* If we match an alt-dest item, we don't output this as a change. */
		char cmpbuf[MAXPATHLEN];
		STRUCT_STAT alt_st;
		int j = 0;
		do {
			pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
			if (link_stat(cmpbuf, &alt_st, 0) < 0)
				continue;
			if (link_dest) {
				if (prev_st.st_dev != alt_st.st_dev
				 || prev_st.st_ino != alt_st.st_ino)
					continue;
				statret = 1;
				*stp = alt_st;
				if (verbose < 2 || !stdout_format_has_i) {
					itemizing = 0;
					code = FNONE;
					if (verbose > 1 && maybe_ATTRS_REPORT)
						rprintf(FCLIENT, "%s is uptodate\n", fname);
				}
				break;
			}
			if (!unchanged_file(cmpbuf, file, &alt_st))
				continue;
			statret = 1;
			*stp = alt_st;
			if (unchanged_attrs(file, &alt_st))
				break;
		} while (basis_dir[++j] != NULL);
	}

	if (maybe_hard_link(file, ndx, fname, statret, stp, prev_name, &prev_st,
			    realname, itemizing, code) < 0)
		return -1;

	if (remove_source_files == 1 && do_xfers)
		send_msg_int(MSG_SUCCESS, ndx);

	return 1;
}

int hard_link_one(struct file_struct *file, const char *fname,
		  const char *oldname, int terse)
{
	if (do_link(oldname, fname) < 0) {
		enum logcode code;
		if (terse) {
			if (!verbose)
				return -1;
			code = FINFO;
		} else
			code = FERROR;
		rsyserr(code, errno, "link %s => %s failed",
			full_fname(fname), oldname);
		return 0;
	}

	file->flags |= FLAG_HLINK_DONE;

	return 1;
}

void finish_hard_link(struct file_struct *file, const char *fname,
		      STRUCT_STAT *stp, int itemizing, enum logcode code,
		      int alt_dest)
{
	STRUCT_STAT st, prev_st;
	char alt_name[MAXPATHLEN], *prev_name;
	const char *our_name;
	int prev_statret, ndx, prev_ndx = F_HL_PREV(file);

	if (stp == NULL && prev_ndx >= 0) {
		if (link_stat(fname, &st, 0) < 0) {
			rsyserr(FERROR, errno, "stat %s failed",
				full_fname(fname));
			return;
		}
		stp = &st;
	}

	/* FIRST combined with DONE means we were the first to get done. */
	file->flags |= FLAG_HLINK_FIRST | FLAG_HLINK_DONE;
	F_HL_PREV(file) = alt_dest;
	if (alt_dest >= 0 && dry_run) {
		pathjoin(alt_name, MAXPATHLEN, basis_dir[alt_dest],
			 f_name(file, NULL));
		our_name = alt_name;
	} else
		our_name = fname;

	while ((ndx = prev_ndx) >= 0) {
		file = FPTR(ndx);
		file->flags = (file->flags & ~FLAG_HLINK_FIRST) | FLAG_HLINK_DONE;
		prev_ndx = F_HL_PREV(file);
		prev_name = f_name(file, NULL);
		prev_statret = link_stat(prev_name, &prev_st, 0);
		if (maybe_hard_link(file, ndx, prev_name, prev_statret, &prev_st,
				    our_name, stp, fname, itemizing, code) < 0)
			continue;
		if (remove_source_files == 1 && do_xfers)
			send_msg_int(MSG_SUCCESS, ndx);
	}
}
#endif
