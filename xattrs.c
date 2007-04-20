/*
 * Extended Attribute support for rsync.
 * Written by Jay Fenlason, vaguely based on the ACLs patch.
 *
 * Copyright (C) 2004 Red Hat, Inc.
 * Copyright (C) 2006, 2007 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
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
#include "lib/sysxattrs.h"

#ifdef SUPPORT_XATTRS

extern int dry_run;
extern int am_root;
extern int am_sender;
extern int am_generator;
extern int read_only;
extern int list_only;
extern int checksum_seed;

#define RSYNC_XAL_INITIAL 5
#define RSYNC_XAL_LIST_INITIAL 100

#define MAX_FULL_DATUM 32

#define HAS_PREFIX(str, prfx) (*(str) == *(prfx) \
			    && strncmp(str, prfx, sizeof (prfx) - 1) == 0)

#define XATTR_ABBREV(x) ((size_t)((x).name - (x).datum) < (x).datum_len)

#define XSTATE_ABBREV	0
#define XSTATE_DONE	1
#define XSTATE_TODO	2
#define XSTATE_LOCAL	3

#define USER_PREFIX "user."
#define UPRE_LEN ((int)sizeof USER_PREFIX - 1)
#define SYSTEM_PREFIX "system."
#define SPRE_LEN ((int)sizeof SYSTEM_PREFIX - 1)

#ifdef HAVE_LINUX_XATTRS
#define RPRE_LEN 0
#else
#define RSYNC_PREFIX "rsync."
#define RPRE_LEN ((int)sizeof RSYNC_PREFIX - 1)
#endif

typedef struct {
	char *datum, *name;
	size_t datum_len, name_len;
} rsync_xa;

static size_t namebuf_len = 0;
static char *namebuf = NULL;

static item_list empty_xattr = EMPTY_ITEM_LIST;
static item_list rsync_xal_l = EMPTY_ITEM_LIST;

/* ------------------------------------------------------------------------- */

static void rsync_xal_free(item_list *xalp)
{
	size_t i;
	rsync_xa *rxas = xalp->items;

	for (i = 0; i < xalp->count; i++) {
		free(rxas[i].datum);
		/*free(rxas[i].name);*/
	}
	xalp->count = 0;
}

void free_xattr(statx *sxp)
{
	if (!sxp->xattr)
		return;
	rsync_xal_free(sxp->xattr);
	free(sxp->xattr);
	sxp->xattr = NULL;
}

static int rsync_xal_compare_names(const void *x1, const void *x2)
{
	const rsync_xa *xa1 = x1;
	const rsync_xa *xa2 = x2;
	return strcmp(xa1->name, xa2->name);
}

static ssize_t get_xattr_names(const char *fname)
{
	ssize_t list_len;

	if (!namebuf) {
		namebuf_len = 1024;
		namebuf = new_array(char, namebuf_len);
		if (!namebuf)
			out_of_memory("get_xattr_names");
	}

	/* The length returned includes all the '\0' terminators. */
	list_len = sys_llistxattr(fname, namebuf, namebuf_len);
	if (list_len > (ssize_t)namebuf_len) {
		list_len = -1;
		errno = ERANGE;
	}
	if (list_len >= 0)
		return list_len;
	if (errno == ENOTSUP)
		return 0;
	if (errno == ERANGE) {
		list_len = sys_llistxattr(fname, NULL, 0);
		if (list_len < 0) {
			rsyserr(FERROR, errno,
				"get_xattr_names: llistxattr(\"%s\",0) failed",
				fname);
			return -1;
		}
		if (namebuf_len)
			free(namebuf);
		namebuf_len = list_len + 1024;
		namebuf = new_array(char, namebuf_len);
		if (!namebuf)
			out_of_memory("get_xattr_names");
		list_len = sys_llistxattr(fname, namebuf, namebuf_len);
		if (list_len >= 0)
			return list_len;
	}

	rsyserr(FERROR, errno,
		"get_xattr_names: llistxattr(\"%s\",%ld) failed",
		fname, (long)namebuf_len);
	return -1;
}

/* On entry, the *len_ptr parameter contains the size of the extra space we
 * should allocate when we create a buffer for the data.  On exit, it contains
 * the length of the datum. */
static char *get_xattr_data(const char *fname, const char *name, size_t *len_ptr,
			    int no_missing_error)
{
	size_t datum_len = sys_lgetxattr(fname, name, NULL, 0);
	char *ptr;

	if (datum_len == (size_t)-1) {
		if (errno == ENOTSUP || no_missing_error)
			return NULL;
		rsyserr(FERROR, errno,
			"get_xattr_data: lgetxattr(\"%s\",\"%s\",0) failed",
			fname, name);
		return NULL;
	}

	if (datum_len + *len_ptr < datum_len /* checks for overflow */
	 || !(ptr = new_array(char, datum_len + *len_ptr)))
		out_of_memory("get_xattr_data");

	*len_ptr = datum_len;

	if (datum_len) {
		size_t len = sys_lgetxattr(fname, name, ptr, datum_len);
		if (len != datum_len) {
			if (len == (size_t)-1) {
				rsyserr(FERROR, errno,
				    "get_xattr_data: lgetxattr(\"%s\",\"%s\",%ld)"
				    " failed", fname, name, (long)datum_len);
			} else {
				rprintf(FERROR,
				    "get_xattr_data: lgetxattr(\"%s\",\"%s\",%ld)"
				    " returned %ld\n", fname, name,
				    (long)datum_len, (long)len);
			}
			free(ptr);
			return NULL;
		}
	}

	return ptr;
}

static int rsync_xal_get(const char *fname, item_list *xalp)
{
	ssize_t list_len, name_len;
	size_t datum_len, name_offset;
	char *name, *ptr;
#ifdef HAVE_LINUX_XATTRS
	int user_only = am_sender ? 0 : !am_root;
#endif

	/* This puts the name list into the "namebuf" buffer. */
	if ((list_len = get_xattr_names(fname)) < 0)
		return -1;

	for (name = namebuf; list_len > 0; name += name_len) {
		rsync_xa *rxas;

		name_len = strlen(name) + 1;
		list_len -= name_len;

#ifdef HAVE_LINUX_XATTRS
		/* We always ignore the system namespace, and non-root
		 * ignores everything but the user namespace. */
		if (user_only ? !HAS_PREFIX(name, USER_PREFIX)
			      : HAS_PREFIX(name, SYSTEM_PREFIX))
			continue;
#endif

		datum_len = name_len; /* Pass extra size to get_xattr_data() */
		if (!(ptr = get_xattr_data(fname, name, &datum_len, 0)))
			return -1;

		if (datum_len > MAX_FULL_DATUM) {
			/* For large datums, we store a flag and a checksum. */
			name_offset = 1 + MAX_DIGEST_LEN;
			sum_init(checksum_seed);
			sum_update(ptr, datum_len);
			free(ptr);

			if (!(ptr = new_array(char, name_offset + name_len)))
				out_of_memory("rsync_xal_get");
			*ptr = XSTATE_ABBREV;
			sum_end(ptr + 1);
		} else
			name_offset = datum_len;

		rxas = EXPAND_ITEM_LIST(xalp, rsync_xa, RSYNC_XAL_INITIAL);
		rxas->name = ptr + name_offset;
		memcpy(rxas->name, name, name_len);
		rxas->datum = ptr;
		rxas->name_len = name_len;
		rxas->datum_len = datum_len;
	}
	if (xalp->count > 1)
		qsort(xalp->items, xalp->count, sizeof (rsync_xa), rsync_xal_compare_names);
	return 0;
}

/* Read the xattr(s) for this filename. */
int get_xattr(const char *fname, statx *sxp)
{
	sxp->xattr = new(item_list);
	*sxp->xattr = empty_xattr;
	if (rsync_xal_get(fname, sxp->xattr) < 0) {
		free_xattr(sxp);
		return -1;
	}
	return 0;
}

static int find_matching_xattr(item_list *xalp)
{
	size_t i, j;
	item_list *lst = rsync_xal_l.items;

	for (i = 0; i < rsync_xal_l.count; i++) {
		rsync_xa *rxas1 = lst[i].items;
		rsync_xa *rxas2 = xalp->items;

		/* Wrong number of elements? */
		if (lst[i].count != xalp->count)
			continue;
		/* any elements different? */
		for (j = 0; j < xalp->count; j++) {
			if (rxas1[j].name_len != rxas2[j].name_len
			 || rxas1[j].datum_len != rxas2[j].datum_len
			 || strcmp(rxas1[j].name, rxas2[j].name))
				break;
			if (rxas1[j].datum_len > MAX_FULL_DATUM) {
				if (memcmp(rxas1[j].datum + 1,
					   rxas2[j].datum + 1,
					   MAX_DIGEST_LEN) != 0)
					break;
			} else {
				if (memcmp(rxas1[j].datum, rxas2[j].datum,
					   rxas2[j].datum_len))
					break;
			}
		}
		/* no differences found.  This is The One! */
		if (j == xalp->count)
			return i;
	}

	return -1;
}

/* Store *xalp on the end of rsync_xal_l */
static void rsync_xal_store(item_list *xalp)
{
	item_list *new_lst = EXPAND_ITEM_LIST(&rsync_xal_l, item_list, RSYNC_XAL_LIST_INITIAL);
	/* Since the following call starts a new list, we know it will hold the
	 * entire initial-count, not just enough space for one new item. */
	*new_lst = empty_xattr;
	(void)EXPAND_ITEM_LIST(new_lst, rsync_xa, xalp->count);
	memcpy(new_lst->items, xalp->items, xalp->count * sizeof (rsync_xa));
	new_lst->count = xalp->count;
	xalp->count = 0;
}

/* Send the make_xattr()-generated xattr list for this flist entry. */
int send_xattr(statx *sxp, int f)
{
	int ndx = find_matching_xattr(sxp->xattr);

	/* Send 0 (-1 + 1) to indicate that literal xattr data follows. */
	write_varint(f, ndx + 1);

	if (ndx < 0) {
		rsync_xa *rxa;
		int count = sxp->xattr->count;
		write_varint(f, count);
		for (rxa = sxp->xattr->items; count--; rxa++) {
#ifdef HAVE_LINUX_XATTRS
			write_varint(f, rxa->name_len);
			write_varint(f, rxa->datum_len);
			write_buf(f, rxa->name, rxa->name_len);
#else
			/* We strip the rsync prefix from disguised namespaces
			 * and put everything else in the user namespace. */
			if (HAS_PREFIX(rxa->name, RSYNC_PREFIX)
			 && rxa->name[RPRE_LEN] != '%') {
				write_varint(f, rxa->name_len - RPRE_LEN);
				write_varint(f, rxa->datum_len);
				write_buf(f, rxa->name + RPRE_LEN, rxa->name_len - RPRE_LEN);
			} else {
				write_varint(f, rxa->name_len + UPRE_LEN);
				write_varint(f, rxa->datum_len);
				write_buf(f, USER_PREFIX, UPRE_LEN);
				write_buf(f, rxa->name, rxa->name_len);
			}
#endif
			if (rxa->datum_len > MAX_FULL_DATUM)
				write_buf(f, rxa->datum + 1, MAX_DIGEST_LEN);
			else
				write_buf(f, rxa->datum, rxa->datum_len);
		}
		ndx = rsync_xal_l.count; /* pre-incremented count */
		rsync_xal_store(sxp->xattr); /* adds item to rsync_xal_l */
	}

	return ndx;
}

/* Return a flag indicating if we need to change a file's xattrs.  If
 * "find_all" is specified, also mark any abbreviated xattrs that we
 * need so that send_xattr_request() can tell the sender about them. */
int xattr_diff(struct file_struct *file, statx *sxp, int find_all)
{
	item_list *lst = rsync_xal_l.items;
	rsync_xa *snd_rxa, *rec_rxa;
	int snd_cnt, rec_cnt;
	int cmp, same, xattrs_equal = 1;

	if (sxp && XATTR_READY(*sxp)) {
		rec_rxa = sxp->xattr->items;
		rec_cnt = sxp->xattr->count;
	} else {
		rec_rxa = NULL;
		rec_cnt = 0;
	}

	if (F_XATTR(file) >= 0)
		lst += F_XATTR(file);
	else
		lst = &empty_xattr;

	snd_rxa = lst->items;
	snd_cnt = lst->count;

	/* If the count of the sender's xattrs is different from our
	 * (receiver's) xattrs, the lists are not the same. */
	if (snd_cnt != rec_cnt) {
		if (!find_all)
			return 1;
		xattrs_equal = 0;
	}

	while (snd_cnt) {
		cmp = rec_cnt ? strcmp(snd_rxa->name, rec_rxa->name) : -1;
		if (cmp > 0)
			same = 0;
		else if (snd_rxa->datum_len > MAX_FULL_DATUM) {
			same = cmp == 0 && snd_rxa->datum_len == rec_rxa->datum_len
			    && memcmp(snd_rxa->datum + 1, rec_rxa->datum + 1,
				      MAX_DIGEST_LEN) == 0;
			/* Flag unrequested items that we need. */
			if (!same && find_all && snd_rxa->datum[0] == XSTATE_ABBREV)
				snd_rxa->datum[0] = XSTATE_TODO;
		} else {
			same = cmp == 0 && snd_rxa->datum_len == rec_rxa->datum_len
			    && memcmp(snd_rxa->datum, rec_rxa->datum,
				      snd_rxa->datum_len) == 0;
		}
		if (!same) {
			if (!find_all)
				return 1;
			xattrs_equal = 0;
		}

		if (cmp <= 0) {
			snd_rxa++;
			snd_cnt--;
		}
		if (cmp >= 0) {
			rec_rxa++;
			rec_cnt--;
		}
	}

	if (rec_cnt)
		xattrs_equal = 0;

	return !xattrs_equal;
}

/* When called by the generator with a NULL fname, this tells the sender
 * which abbreviated xattr values we need.  When called by the sender
 * (with a non-NULL fname), we send all the extra xattr data it needs. */
void send_xattr_request(const char *fname, struct file_struct *file, int f_out)
{
	item_list *lst = rsync_xal_l.items;
	int j, cnt, prior_req = -1;
	rsync_xa *rxa;

	lst += F_XATTR(file);
	cnt = lst->count;
	for (rxa = lst->items, j = 0; j < cnt; rxa++, j++) {
		if (rxa->datum_len <= MAX_FULL_DATUM)
			continue;
		switch (rxa->datum[0]) {
		case XSTATE_LOCAL:
			/* Items set locally will get cached by receiver. */
			rxa->datum[0] = XSTATE_DONE;
			continue;
		case XSTATE_TODO:
			break;
		default:
			continue;
		}

		/* Flag that we handled this abbreviated item. */
		rxa->datum[0] = XSTATE_DONE;

		write_varint(f_out, j - prior_req);
		prior_req = j;

		if (fname) {
			size_t len = 0;
			char *ptr;

			/* Re-read the long datum. */
			if (!(ptr = get_xattr_data(fname, rxa->name, &len, 0)))
				continue;

			write_varint(f_out, len); /* length might have changed! */
			write_buf(f_out, ptr, len);
			free(ptr);
		}
	}

	write_byte(f_out, 0); /* end the list */
}

/* Any items set locally by the generator that the receiver doesn't
 * get told about get changed back to XSTATE_ABBREV. */
void xattr_clear_locals(struct file_struct *file)
{
	item_list *lst = rsync_xal_l.items;
	rsync_xa *rxa;
	int cnt;

	if (F_XATTR(file) < 0)
		return;

	lst += F_XATTR(file);
	cnt = lst->count;
	for (rxa = lst->items; cnt--; rxa++) {
		if (rxa->datum_len <= MAX_FULL_DATUM)
			continue;
		if (rxa->datum[0] == XSTATE_LOCAL)
			rxa->datum[0] = XSTATE_ABBREV;
	}
}

/* When called by the sender, read the request from the generator and mark
 * any needed xattrs with a flag that lets us know they need to be sent to
 * the receiver.  When called by the receiver, reads the sent data and
 * stores it in place of its checksum. */
void recv_xattr_request(struct file_struct *file, int f_in)
{
	item_list *lst = rsync_xal_l.items;
	char *old_datum, *name;
	rsync_xa *rxa;
	int rel_pos, cnt;

	if (F_XATTR(file) < 0) {
		rprintf(FERROR, "recv_xattr_request: internal data error!\n");
		exit_cleanup(RERR_STREAMIO);
	}
	lst += F_XATTR(file);

	cnt = lst->count;
	rxa = lst->items;
	rxa -= 1;
	while ((rel_pos = read_varint(f_in)) != 0) {
		rxa += rel_pos;
		cnt -= rel_pos;
		if (cnt < 0 || rxa->datum_len <= MAX_FULL_DATUM
		 || rxa->datum[0] != XSTATE_ABBREV) {
			rprintf(FERROR, "recv_xattr_request: internal abbrev error!\n");
			exit_cleanup(RERR_STREAMIO);
		}

		if (am_sender) {
			rxa->datum[0] = XSTATE_TODO;
			continue;
		}

		old_datum = rxa->datum;
		rxa->datum_len = read_varint(f_in);

		if (rxa->name_len + rxa->datum_len < rxa->name_len)
			out_of_memory("recv_xattr_request"); /* overflow */
		rxa->datum = new_array(char, rxa->datum_len + rxa->name_len);
		if (!rxa->datum)
			out_of_memory("recv_xattr_request");
		name = rxa->datum + rxa->datum_len;
		memcpy(name, rxa->name, rxa->name_len);
		rxa->name = name;
		free(old_datum);
		read_buf(f_in, rxa->datum, rxa->datum_len);
	}
}

/* ------------------------------------------------------------------------- */

/* receive and build the rsync_xattr_lists */
void receive_xattr(struct file_struct *file, int f)
{
	static item_list temp_xattr = EMPTY_ITEM_LIST;
	int count;
	int ndx = read_varint(f);

	if (ndx < 0 || (size_t)ndx > rsync_xal_l.count) {
		rprintf(FERROR, "receive_xattr: xa index %d out of"
			" range for %s\n", ndx, f_name(file, NULL));
		exit_cleanup(RERR_STREAMIO);
	}

	if (ndx != 0) {
		F_XATTR(file) = ndx - 1;
		return;
	}
	
	if ((count = read_varint(f)) != 0) {
		(void)EXPAND_ITEM_LIST(&temp_xattr, rsync_xa, count);
		temp_xattr.count = 0;
	}

	while (count--) {
		char *ptr, *name;
		rsync_xa *rxa;
		size_t name_len = read_varint(f);
		size_t datum_len = read_varint(f);
		size_t dget_len = datum_len > MAX_FULL_DATUM ? 1 + MAX_DIGEST_LEN : datum_len;
#ifdef HAVE_LINUX_XATTRS
		size_t extra_len = 0;
#else
		size_t extra_len = am_root ? RPRE_LEN : 0;
		if (dget_len + extra_len < dget_len)
			out_of_memory("receive_xattr"); /* overflow */
#endif
		if (dget_len + extra_len + name_len < dget_len)
			out_of_memory("receive_xattr"); /* overflow */
		ptr = new_array(char, dget_len + extra_len + name_len);
		if (!ptr)
			out_of_memory("receive_xattr");
		name = ptr + dget_len + extra_len;
		read_buf(f, name, name_len);
		if (dget_len == datum_len)
			read_buf(f, ptr, dget_len);
		else {
			*ptr = XSTATE_ABBREV;
			read_buf(f, ptr + 1, MAX_DIGEST_LEN);
		}
#ifdef HAVE_LINUX_XATTRS
		/* Non-root can only save the user namespace. */
		if (!am_root && !HAS_PREFIX(name, USER_PREFIX)) {
			free(ptr);
			continue;
		}
#else
		/* This OS only has a user namespace, so we either
		 * strip the user prefix, or we put a non-user
		 * namespace inside our rsync hierarchy. */
		if (HAS_PREFIX(name, USER_PREFIX)) {
			name += UPRE_LEN;
			name_len -= UPRE_LEN;
		} else if (am_root) {
			name -= RPRE_LEN;
			name_len += RPRE_LEN;
			memcpy(name, RSYNC_PREFIX, RPRE_LEN);
		} else {
			free(ptr);
			continue;
		}
#endif
		rxa = EXPAND_ITEM_LIST(&temp_xattr, rsync_xa, 1);
		rxa->name = name;
		rxa->datum = ptr;
		rxa->name_len = name_len;
		rxa->datum_len = datum_len;
	}

	ndx = rsync_xal_l.count; /* pre-incremented count */
	rsync_xal_store(&temp_xattr); /* adds item to rsync_xal_l */

	F_XATTR(file) = ndx;
}

/* Turn the xattr data in statx into cached xattr data, setting the index
 * values in the file struct. */
void cache_xattr(struct file_struct *file, statx *sxp)
{
	int ndx;

	if (!sxp->xattr)
		return;

	ndx = find_matching_xattr(sxp->xattr);
	if (ndx < 0)
		rsync_xal_store(sxp->xattr); /* adds item to rsync_xal_l */

	F_XATTR(file) = ndx;
}

static int rsync_xal_set(const char *fname, item_list *xalp,
			 const char *fnamecmp, statx *sxp)
{
	rsync_xa *rxas = xalp->items;
	ssize_t list_len;
	size_t i, len;
	char *name, *ptr, sum[MAX_DIGEST_LEN];
	int name_len, ret = 0;

	/* This puts the current name list into the "namebuf" buffer. */
	if ((list_len = get_xattr_names(fname)) < 0)
		return -1;

	for (i = 0; i < xalp->count; i++) {
		name = rxas[i].name;

		if (XATTR_ABBREV(rxas[i])) {
			/* See if the fnamecmp version is identical. */
			len = name_len = rxas[i].name_len;
			if ((ptr = get_xattr_data(fnamecmp, name, &len, 1)) == NULL) {
			  still_abbrev:
				if (am_generator)
					continue;
				rprintf(FERROR, "Missing abbreviated xattr value, %s, for %s\n",
					rxas[i].name, full_fname(fname));
				ret = -1;
				continue;
			}
			if (len != rxas[i].datum_len) {
				free(ptr);
				goto still_abbrev;
			}

			sum_init(checksum_seed);
			sum_update(ptr, len);
			sum_end(sum);
			if (memcmp(sum, rxas[i].datum + 1, MAX_DIGEST_LEN) != 0) {
				free(ptr);
				goto still_abbrev;
			}

			if (fname == fnamecmp)
				; /* Value is already set when identical */
			else if (sys_lsetxattr(fname, name, ptr, len) < 0) {
				rsyserr(FERROR, errno,
					"rsync_xal_set: lsetxattr(\"%s\",\"%s\") failed",
					fname, name);
				ret = -1;
			} else /* make sure caller sets mtime */
				sxp->st.st_mtime = (time_t)-1;

			if (am_generator) { /* generator items stay abbreviated */
				if (rxas[i].datum[0] == XSTATE_ABBREV)
					rxas[i].datum[0] = XSTATE_LOCAL;
				free(ptr);
				continue;
			}

			memcpy(ptr + len, name, name_len);
			free(rxas[i].datum);

			rxas[i].name = name = ptr + len;
			rxas[i].datum = ptr;
			continue;
		}

		if (sys_lsetxattr(fname, name, rxas[i].datum, rxas[i].datum_len) < 0) {
			rsyserr(FERROR, errno,
				"rsync_xal_set: lsetxattr(\"%s\",\"%s\") failed",
				fname, name);
			ret = -1;
		} else /* make sure caller sets mtime */
			sxp->st.st_mtime = (time_t)-1;
	}

	/* Remove any extraneous names. */
	for (name = namebuf; list_len > 0; name += name_len) {
		name_len = strlen(name) + 1;
		list_len -= name_len;

#ifdef HAVE_LINUX_XATTRS
		/* We always ignore the system namespace, and non-root
		 * ignores everything but the user namespace. */
		if (am_root ? HAS_PREFIX(name, SYSTEM_PREFIX)
			    : !HAS_PREFIX(name, USER_PREFIX))
			continue;
#endif

		for (i = 0; i < xalp->count; i++) {
			if (strcmp(name, rxas[i].name) == 0)
				break;
		}
		if (i == xalp->count) {
			if (sys_lremovexattr(fname, name) < 0) {
				rsyserr(FERROR, errno,
					"rsync_xal_clear: lremovexattr(\"%s\",\"%s\") failed",
					fname, name);
				ret = -1;
			} else /* make sure caller sets mtime */
				sxp->st.st_mtime = (time_t)-1;
		}
	}

	return ret;
}

/* Set extended attributes on indicated filename. */
int set_xattr(const char *fname, const struct file_struct *file,
	      const char *fnamecmp, statx *sxp)
{
	int ndx;
	item_list *lst = rsync_xal_l.items;

	if (dry_run)
		return 1; /* FIXME: --dry-run needs to compute this value */

	if (read_only || list_only) {
		errno = EROFS;
		return -1;
	}

	ndx = F_XATTR(file);
	return rsync_xal_set(fname, lst + ndx, fnamecmp, sxp);
}

#endif /* SUPPORT_XATTRS */
