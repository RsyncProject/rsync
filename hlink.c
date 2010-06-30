/*
 * Routines to support hard-linking.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2004-2009 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"

extern int verbose;
extern int dry_run;
extern int list_only;
extern int am_sender;
extern int inc_recurse;
extern int do_xfers;
extern int link_dest;
extern int preserve_acls;
extern int preserve_xattrs;
extern int make_backups;
extern int protocol_version;
extern int remove_source_files;
extern int stdout_format_has_i;
extern int maybe_ATTRS_REPORT;
extern int unsort_ndx;
extern char *basis_dir[MAX_BASIS_DIRS+1];
extern struct file_list *cur_flist;

#ifdef SUPPORT_HARD_LINKS

/* Starting with protocol 30, we use a simple hashtable on the sending side
 * for hashing the st_dev and st_ino info.  The receiving side gets told
 * (via flags and a "group index") which items are hard-linked together, so
 * we can avoid the pool of dev+inode data.  For incremental recursion mode,
 * the receiver will use a ndx hash to remember old pathnames. */

static struct hashtable *dev_tbl;

static struct hashtable *prior_hlinks;

static struct file_list *hlink_flist;

void init_hard_links(void)
{
	if (am_sender || protocol_version < 30)
		dev_tbl = hashtable_create(16, 1);
	else if (inc_recurse)
		prior_hlinks = hashtable_create(1024, 0);
}

struct ht_int64_node *idev_find(int64 dev, int64 ino)
{
	static struct ht_int64_node *dev_node = NULL;
	struct hashtable *tbl;

	/* Note that some OSes have a dev == 0, so increment to avoid storing a 0. */
	if (!dev_node || dev_node->key != dev+1) {
		/* We keep a separate hash table of inodes for every device. */
		dev_node = hashtable_find(dev_tbl, dev+1, 1);
		if (!(tbl = dev_node->data))
			tbl = dev_node->data = hashtable_create(512, 1);
	} else
		tbl = dev_node->data;

	return hashtable_find(tbl, ino, 1);
}

void idev_destroy(void)
{
	int i;

	for (i = 0; i < dev_tbl->size; i++) {
		struct ht_int32_node *node = HT_NODE(dev_tbl, dev_tbl->nodes, i);
		if (node->data)
			hashtable_destroy(node->data);
	}

	hashtable_destroy(dev_tbl);
}

static int hlink_compare_gnum(int *int1, int *int2)
{
	struct file_struct *f1 = hlink_flist->sorted[*int1];
	struct file_struct *f2 = hlink_flist->sorted[*int2];
	int32 gnum1 = F_HL_GNUM(f1);
	int32 gnum2 = F_HL_GNUM(f2);

	if (gnum1 != gnum2)
		return gnum1 > gnum2 ? 1 : -1;

	return *int1 > *int2 ? 1 : -1;
}

static void match_gnums(int32 *ndx_list, int ndx_count)
{
	int32 from, prev;
	struct file_struct *file, *file_next;
	struct ht_int32_node *node = NULL;
	int32 gnum, gnum_next;

	qsort(ndx_list, ndx_count, sizeof ndx_list[0],
	     (int (*)()) hlink_compare_gnum);

	for (from = 0; from < ndx_count; from++) {
		file = hlink_flist->sorted[ndx_list[from]];
		gnum = F_HL_GNUM(file);
		if (inc_recurse) {
			node = hashtable_find(prior_hlinks, gnum, 1);
			if (!node->data) {
				if (!(node->data = new_array0(char, 5)))
					out_of_memory("match_gnums");
				assert(gnum >= hlink_flist->ndx_start);
				file->flags |= FLAG_HLINK_FIRST;
				prev = -1;
			} else if (CVAL(node->data, 0) == 0) {
				struct file_list *flist;
				prev = IVAL(node->data, 1);
				flist = flist_for_ndx(prev, NULL);
				if (flist)
					flist->files[prev - flist->ndx_start]->flags &= ~FLAG_HLINK_LAST;
				else {
					/* We skipped all prior files in this
					 * group, so mark this as a "first". */
					file->flags |= FLAG_HLINK_FIRST;
					prev = -1;
				}
			} else
				prev = -1;
		} else {
			file->flags |= FLAG_HLINK_FIRST;
			prev = -1;
		}
		for ( ; from < ndx_count-1; file = file_next, gnum = gnum_next, from++) { /*SHARED ITERATOR*/
			file_next = hlink_flist->sorted[ndx_list[from+1]];
			gnum_next = F_HL_GNUM(file_next);
			if (gnum != gnum_next)
				break;
			F_HL_PREV(file) = prev;
			/* The linked list uses over-the-wire ndx values. */
			if (unsort_ndx)
				prev = F_NDX(file);
			else
				prev = ndx_list[from] + hlink_flist->ndx_start;
		}
		if (prev < 0 && !inc_recurse) {
			/* Disable hard-link bit and set DONE so that
			 * HLINK_BUMP()-dependent values are unaffected. */
			file->flags &= ~(FLAG_HLINKED | FLAG_HLINK_FIRST);
			file->flags |= FLAG_HLINK_DONE;
			continue;
		}

		file->flags |= FLAG_HLINK_LAST;
		F_HL_PREV(file) = prev;
		if (inc_recurse && CVAL(node->data, 0) == 0) {
			if (unsort_ndx)
				prev = F_NDX(file);
			else
				prev = ndx_list[from] + hlink_flist->ndx_start;
			SIVAL(node->data, 1, prev);
		}
	}
}

/* Analyze the hard-links in the file-list by creating a list of all the
 * items that have hlink data, sorting them, and matching up identical
 * values into clusters.  These will be a single linked list from last
 * to first when we're done. */
void match_hard_links(struct file_list *flist)
{
	if (!list_only && flist->used) {
		int i, ndx_count = 0;
		int32 *ndx_list;

		if (!(ndx_list = new_array(int32, flist->used)))
			out_of_memory("match_hard_links");

		for (i = 0; i < flist->used; i++) {
			if (F_IS_HLINKED(flist->sorted[i]))
				ndx_list[ndx_count++] = i;
		}

		hlink_flist = flist;

		if (ndx_count)
			match_gnums(ndx_list, ndx_count);

		free(ndx_list);
	}
	if (protocol_version < 30)
		idev_destroy();
}

static int maybe_hard_link(struct file_struct *file, int ndx,
			   const char *fname, int statret, stat_x *sxp,
			   const char *oldname, STRUCT_STAT *old_stp,
			   const char *realname, int itemizing, enum logcode code)
{
	if (statret == 0) {
		if (sxp->st.st_dev == old_stp->st_dev
		 && sxp->st.st_ino == old_stp->st_ino) {
			if (itemizing) {
				itemize(fname, file, ndx, statret, sxp,
					ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS,
					0, "");
			}
			if (verbose > 1 && maybe_ATTRS_REPORT)
				rprintf(FCLIENT, "%s is uptodate\n", fname);
			file->flags |= FLAG_HLINK_DONE;
			return 0;
		}
		if (make_backups > 0) {
			if (!make_backup(fname))
				return -1;
		} else if (robust_unlink(fname)) {
			rsyserr(FERROR_XFER, errno, "unlink %s failed",
				full_fname(fname));
			return -1;
		}
	}

	if (hard_link_one(file, fname, oldname, 0)) {
		if (itemizing) {
			itemize(fname, file, ndx, statret, sxp,
				ITEM_LOCAL_CHANGE | ITEM_XNAME_FOLLOWS, 0,
				realname);
		}
		if (code != FNONE && verbose)
			rprintf(code, "%s => %s\n", fname, realname);
		return 0;
	}
	return -1;
}

/* Figure out if a prior entry is still there or if we just have a
 * cached name for it. */
static char *check_prior(struct file_struct *file, int gnum,
			 int *prev_ndx_p, struct file_list **flist_p)
{
	struct file_struct *fp;
	struct ht_int32_node *node;
	int prev_ndx = F_HL_PREV(file);

	while (1) {
		struct file_list *flist;
		if (prev_ndx < 0
		 || (flist = flist_for_ndx(prev_ndx, NULL)) == NULL)
			break;
		fp = flist->files[prev_ndx - flist->ndx_start];
		if (!(fp->flags & FLAG_SKIP_HLINK)) {
			*prev_ndx_p = prev_ndx;
			*flist_p = flist;
			return NULL;
		}
		F_HL_PREV(file) = prev_ndx = F_HL_PREV(fp);
	}

	if (inc_recurse
	 && (node = hashtable_find(prior_hlinks, gnum, 0)) != NULL) {
		assert(node->data != NULL);
		if (CVAL(node->data, 0) != 0) {
			*prev_ndx_p = -1;
			*flist_p = NULL;
			return node->data;
		}
		/* The prior file must have been skipped. */
		F_HL_PREV(file) = -1;
	}

	*prev_ndx_p = -1;
	*flist_p = NULL;
	return NULL;
}

/* Only called if FLAG_HLINKED is set and FLAG_HLINK_FIRST is not.  Returns:
 * 0 = process the file, 1 = skip the file, -1 = error occurred. */
int hard_link_check(struct file_struct *file, int ndx, const char *fname,
		    int statret, stat_x *sxp, int itemizing,
		    enum logcode code)
{
	STRUCT_STAT prev_st;
	char namebuf[MAXPATHLEN], altbuf[MAXPATHLEN];
	char *realname, *prev_name;
	struct file_list *flist;
	int gnum = inc_recurse ? F_HL_GNUM(file) : -1;
	int prev_ndx;

	prev_name = realname = check_prior(file, gnum, &prev_ndx, &flist);

	if (!prev_name) {
		struct file_struct *prev_file;

		if (!flist) {
			/* The previous file was skipped, so this one is
			 * treated as if it were the first in its group. */
			return 0;
		}

		prev_file = flist->files[prev_ndx - flist->ndx_start];

		/* Is the previous link not complete yet? */
		if (!(prev_file->flags & FLAG_HLINK_DONE)) {
			/* Is the previous link being transferred? */
			if (prev_file->flags & FLAG_FILE_SENT) {
				/* Add ourselves to the list of files that will
				 * be updated when the transfer completes, and
				 * mark ourself as waiting for the transfer. */
				F_HL_PREV(file) = F_HL_PREV(prev_file);
				F_HL_PREV(prev_file) = ndx;
				file->flags |= FLAG_FILE_SENT;
				cur_flist->in_progress++;
				return 1;
			}
			return 0;
		}

		/* There is a finished file to link with! */
		if (!(prev_file->flags & FLAG_HLINK_FIRST)) {
			/* The previous previous is FIRST when prev is not. */
			prev_name = realname = check_prior(prev_file, gnum, &prev_ndx, &flist);
			assert(prev_name != NULL || flist != NULL);
			/* Update our previous pointer to point to the FIRST. */
			F_HL_PREV(file) = prev_ndx;
		}

		if (!prev_name) {
			int alt_dest;

			prev_file = flist->files[prev_ndx - flist->ndx_start];
			/* F_HL_PREV() is alt_dest value when DONE && FIRST. */
			alt_dest = F_HL_PREV(prev_file);

			if (alt_dest >= 0 && dry_run) {
				pathjoin(namebuf, MAXPATHLEN, basis_dir[alt_dest],
					 f_name(prev_file, NULL));
				prev_name = namebuf;
				realname = f_name(prev_file, altbuf);
			} else {
				prev_name = f_name(prev_file, namebuf);
				realname = prev_name;
			}
		}
	}

	if (link_stat(prev_name, &prev_st, 0) < 0) {
		if (!dry_run || errno != ENOENT) {
			rsyserr(FERROR_XFER, errno, "stat %s failed", full_fname(prev_name));
			return -1;
		}
		/* A new hard-link will get a new dev & inode, so approximate
		 * those values in dry-run mode by zeroing them. */
		memset(&prev_st, 0, sizeof prev_st);
	}

	if (statret < 0 && basis_dir[0] != NULL) {
		/* If we match an alt-dest item, we don't output this as a change. */
		char cmpbuf[MAXPATHLEN];
		stat_x alt_sx;
		int j = 0;
#ifdef SUPPORT_ACLS
		alt_sx.acc_acl = alt_sx.def_acl = NULL;
#endif
#ifdef SUPPORT_XATTRS
		alt_sx.xattr = NULL;
#endif
		do {
			pathjoin(cmpbuf, MAXPATHLEN, basis_dir[j], fname);
			if (link_stat(cmpbuf, &alt_sx.st, 0) < 0)
				continue;
			if (link_dest) {
				if (prev_st.st_dev != alt_sx.st.st_dev
				 || prev_st.st_ino != alt_sx.st.st_ino)
					continue;
				statret = 1;
				if (stdout_format_has_i == 0
				 || (verbose < 2 && stdout_format_has_i < 2)) {
					itemizing = 0;
					code = FNONE;
					if (verbose > 1 && maybe_ATTRS_REPORT)
						rprintf(FCLIENT, "%s is uptodate\n", fname);
				}
				break;
			}
			if (!unchanged_file(cmpbuf, file, &alt_sx.st))
				continue;
			statret = 1;
			if (unchanged_attrs(cmpbuf, file, &alt_sx))
				break;
		} while (basis_dir[++j] != NULL);
		if (statret == 1) {
			sxp->st = alt_sx.st;
#ifdef SUPPORT_ACLS
			if (preserve_acls && !S_ISLNK(file->mode)) {
				free_acl(sxp);
				if (!ACL_READY(alt_sx))
					get_acl(cmpbuf, sxp);
				else {
					sxp->acc_acl = alt_sx.acc_acl;
					sxp->def_acl = alt_sx.def_acl;
					alt_sx.acc_acl = alt_sx.def_acl = NULL;
				}
			}
#endif
#ifdef SUPPORT_XATTRS
			if (preserve_xattrs) {
				free_xattr(sxp);
				if (!XATTR_READY(alt_sx))
					get_xattr(cmpbuf, sxp);
				else {
					sxp->xattr = alt_sx.xattr;
					alt_sx.xattr = NULL;
				}
			}
#endif
		} else {
#ifdef SUPPORT_ACLS
			if (preserve_acls)
				free_acl(&alt_sx);
#endif
#ifdef SUPPORT_XATTRS
			if (preserve_xattrs)
				free_xattr(&alt_sx);
#endif
		}
	}

	if (maybe_hard_link(file, ndx, fname, statret, sxp, prev_name, &prev_st,
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
				return 0;
			code = FINFO;
		} else
			code = FERROR_XFER;
		rsyserr(code, errno, "link %s => %s failed",
			full_fname(fname), oldname);
		return 0;
	}

	file->flags |= FLAG_HLINK_DONE;

	return 1;
}

void finish_hard_link(struct file_struct *file, const char *fname, int fin_ndx,
		      STRUCT_STAT *stp, int itemizing, enum logcode code,
		      int alt_dest)
{
	stat_x prev_sx;
	STRUCT_STAT st;
	char prev_name[MAXPATHLEN], alt_name[MAXPATHLEN];
	const char *our_name;
	struct file_list *flist;
	int prev_statret, ndx, prev_ndx = F_HL_PREV(file);

	if (stp == NULL && prev_ndx >= 0) {
		if (link_stat(fname, &st, 0) < 0) {
			rsyserr(FERROR_XFER, errno, "stat %s failed",
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

#ifdef SUPPORT_ACLS
	prev_sx.acc_acl = prev_sx.def_acl = NULL;
#endif
#ifdef SUPPORT_XATTRS
	prev_sx.xattr = NULL;
#endif

	while ((ndx = prev_ndx) >= 0) {
		int val;
		flist = flist_for_ndx(ndx, "finish_hard_link");
		file = flist->files[ndx - flist->ndx_start];
		file->flags = (file->flags & ~FLAG_HLINK_FIRST) | FLAG_HLINK_DONE;
		prev_ndx = F_HL_PREV(file);
		F_HL_PREV(file) = fin_ndx;
		prev_statret = link_stat(f_name(file, prev_name), &prev_sx.st, 0);
		val = maybe_hard_link(file, ndx, prev_name, prev_statret, &prev_sx,
				      our_name, stp, fname, itemizing, code);
		flist->in_progress--;
#ifdef SUPPORT_ACLS
		if (preserve_acls)
			free_acl(&prev_sx);
#endif
#ifdef SUPPORT_XATTRS
		if (preserve_xattrs)
			free_xattr(&prev_sx);
#endif
		if (val < 0)
			continue;
		if (remove_source_files == 1 && do_xfers)
			send_msg_int(MSG_SUCCESS, ndx);
	}

	if (inc_recurse) {
		int gnum = F_HL_GNUM(file);
		struct ht_int32_node *node = hashtable_find(prior_hlinks, gnum, 0);
		if (node == NULL) {
			rprintf(FERROR, "Unable to find a hlink node for %d (%s)\n", gnum, f_name(file, prev_name));
			exit_cleanup(RERR_MESSAGEIO);
		}
		if (node->data == NULL) {
			rprintf(FERROR, "Hlink node data for %d is NULL (%s)\n", gnum, f_name(file, prev_name));
			exit_cleanup(RERR_MESSAGEIO);
		}
		if (CVAL(node->data, 0) != 0) {
			rprintf(FERROR, "Hlink node data for %d already has path=%s (%s)\n",
				gnum, (char*)node->data, f_name(file, prev_name));
			exit_cleanup(RERR_MESSAGEIO);
		}
		free(node->data);
		if (!(node->data = strdup(our_name)))
			out_of_memory("finish_hard_link");
	}
}

int skip_hard_link(struct file_struct *file, struct file_list **flist_p)
{
	struct file_list *flist;
	int prev_ndx;

	file->flags |= FLAG_SKIP_HLINK;
	if (!(file->flags & FLAG_HLINK_LAST))
		return -1;

	check_prior(file, F_HL_GNUM(file), &prev_ndx, &flist);
	if (prev_ndx >= 0) {
		file = flist->files[prev_ndx - flist->ndx_start];
		if (file->flags & (FLAG_HLINK_DONE|FLAG_FILE_SENT))
			return -1;
		file->flags |= FLAG_HLINK_LAST;
		*flist_p = flist;
	}

	return prev_ndx;
}
#endif
