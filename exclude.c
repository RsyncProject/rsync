/*
 * The filter include/exclude routines.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003-2015 Wayne Davison
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

extern int am_server;
extern int am_sender;
extern int eol_nulls;
extern int io_error;
extern int local_server;
extern int prune_empty_dirs;
extern int ignore_perishable;
extern int delete_mode;
extern int delete_excluded;
extern int cvs_exclude;
extern int sanitize_paths;
extern int protocol_version;
extern int module_id;

extern char curr_dir[MAXPATHLEN];
extern unsigned int curr_dir_len;
extern unsigned int module_dirlen;

filter_rule_list filter_list = { .debug_type = "" };
filter_rule_list cvs_filter_list = { .debug_type = " [global CVS]" };
filter_rule_list daemon_filter_list = { .debug_type = " [daemon]" };

/* Need room enough for ":MODS " prefix plus some room to grow. */
#define MAX_RULE_PREFIX (16)

#define SLASH_WILD3_SUFFIX "/***"

/* The dirbuf is set by push_local_filters() to the current subdirectory
 * relative to curr_dir that is being processed.  The path always has a
 * trailing slash appended, and the variable dirbuf_len contains the length
 * of this path prefix.  The path is always absolute. */
static char dirbuf[MAXPATHLEN+1];
static unsigned int dirbuf_len = 0;
static int dirbuf_depth;

/* This is True when we're scanning parent dirs for per-dir merge-files. */
static BOOL parent_dirscan = False;

/* This array contains a list of all the currently active per-dir merge
 * files.  This makes it easier to save the appropriate values when we
 * "push" down into each subdirectory. */
static filter_rule **mergelist_parents;
static int mergelist_cnt = 0;
static int mergelist_size = 0;

/* Each filter_list_struct describes a singly-linked list by keeping track
 * of both the head and tail pointers.  The list is slightly unusual in that
 * a parent-dir's content can be appended to the end of the local list in a
 * special way:  the last item in the local list has its "next" pointer set
 * to point to the inherited list, but the local list's tail pointer points
 * at the end of the local list.  Thus, if the local list is empty, the head
 * will be pointing at the inherited content but the tail will be NULL.  To
 * help you visualize this, here are the possible list arrangements:
 *
 * Completely Empty                     Local Content Only
 * ==================================   ====================================
 * head -> NULL                         head -> Local1 -> Local2 -> NULL
 * tail -> NULL                         tail -------------^
 *
 * Inherited Content Only               Both Local and Inherited Content
 * ==================================   ====================================
 * head -> Parent1 -> Parent2 -> NULL   head -> L1 -> L2 -> P1 -> P2 -> NULL
 * tail -> NULL                         tail ---------^
 *
 * This means that anyone wanting to traverse the whole list to use it just
 * needs to start at the head and use the "next" pointers until it goes
 * NULL.  To add new local content, we insert the item after the tail item
 * and update the tail (obviously, if "tail" was NULL, we insert it at the
 * head).  To clear the local list, WE MUST NOT FREE THE INHERITED CONTENT
 * because it is shared between the current list and our parent list(s).
 * The easiest way to handle this is to simply truncate the list after the
 * tail item and then free the local list from the head.  When inheriting
 * the list for a new local dir, we just save off the filter_list_struct
 * values (so we can pop back to them later) and set the tail to NULL.
 */

static void teardown_mergelist(filter_rule *ex)
{
	int j;

	if (!ex->u.mergelist)
		return;

	if (DEBUG_GTE(FILTER, 2)) {
		rprintf(FINFO, "[%s] deactivating mergelist #%d%s\n",
			who_am_i(), mergelist_cnt - 1,
			ex->u.mergelist->debug_type);
	}

	free(ex->u.mergelist->debug_type);
	free(ex->u.mergelist);

	for (j = 0; j < mergelist_cnt; j++) {
		if (mergelist_parents[j] == ex) {
			mergelist_parents[j] = NULL;
			break;
		}
	}
	while (mergelist_cnt && mergelist_parents[mergelist_cnt-1] == NULL)
		mergelist_cnt--;
}

static void free_filter(filter_rule *ex)
{
	if (ex->rflags & FILTRULE_PERDIR_MERGE)
		teardown_mergelist(ex);
	free(ex->pattern);
	free(ex);
}

static void free_filters(filter_rule *ent)
{
	while (ent) {
		filter_rule *next = ent->next;
		free_filter(ent);
		ent = next;
	}
}

/* Build a filter structure given a filter pattern.  The value in "pat"
 * is not null-terminated.  "rule" is either held or freed, so the
 * caller should not free it. */
static void add_rule(filter_rule_list *listp, const char *pat, unsigned int pat_len,
		     filter_rule *rule, int xflags)
{
	const char *cp;
	unsigned int pre_len, suf_len, slash_cnt = 0;

	if (DEBUG_GTE(FILTER, 2)) {
		rprintf(FINFO, "[%s] add_rule(%s%.*s%s)%s\n",
			who_am_i(), get_rule_prefix(rule, pat, 0, NULL),
			(int)pat_len, pat,
			(rule->rflags & FILTRULE_DIRECTORY) ? "/" : "",
			listp->debug_type);
	}

	/* These flags also indicate that we're reading a list that
	 * needs to be filtered now, not post-filtered later. */
	if (xflags & (XFLG_ANCHORED2ABS|XFLG_ABS_IF_SLASH)
		&& (rule->rflags & FILTRULES_SIDES)
			== (am_sender ? FILTRULE_RECEIVER_SIDE : FILTRULE_SENDER_SIDE)) {
		/* This filter applies only to the other side.  Drop it. */
		free_filter(rule);
		return;
	}

	if (pat_len > 1 && pat[pat_len-1] == '/') {
		pat_len--;
		rule->rflags |= FILTRULE_DIRECTORY;
	}

	for (cp = pat; cp < pat + pat_len; cp++) {
		if (*cp == '/')
			slash_cnt++;
	}

	if (!(rule->rflags & (FILTRULE_ABS_PATH | FILTRULE_MERGE_FILE))
	 && ((xflags & (XFLG_ANCHORED2ABS|XFLG_ABS_IF_SLASH) && *pat == '/')
	  || (xflags & XFLG_ABS_IF_SLASH && slash_cnt))) {
		rule->rflags |= FILTRULE_ABS_PATH;
		if (*pat == '/')
			pre_len = dirbuf_len - module_dirlen - 1;
		else
			pre_len = 0;
	} else
		pre_len = 0;

	/* The daemon wants dir-exclude rules to get an appended "/" + "***". */
	if (xflags & XFLG_DIR2WILD3
	 && BITS_SETnUNSET(rule->rflags, FILTRULE_DIRECTORY, FILTRULE_INCLUDE)) {
		rule->rflags &= ~FILTRULE_DIRECTORY;
		suf_len = sizeof SLASH_WILD3_SUFFIX - 1;
	} else
		suf_len = 0;

	if (!(rule->pattern = new_array(char, pre_len + pat_len + suf_len + 1)))
		out_of_memory("add_rule");
	if (pre_len) {
		memcpy(rule->pattern, dirbuf + module_dirlen, pre_len);
		for (cp = rule->pattern; cp < rule->pattern + pre_len; cp++) {
			if (*cp == '/')
				slash_cnt++;
		}
	}
	strlcpy(rule->pattern + pre_len, pat, pat_len + 1);
	pat_len += pre_len;
	if (suf_len) {
		memcpy(rule->pattern + pat_len, SLASH_WILD3_SUFFIX, suf_len+1);
		pat_len += suf_len;
		slash_cnt++;
	}

	if (strpbrk(rule->pattern, "*[?")) {
		rule->rflags |= FILTRULE_WILD;
		if ((cp = strstr(rule->pattern, "**")) != NULL) {
			rule->rflags |= FILTRULE_WILD2;
			/* If the pattern starts with **, note that. */
			if (cp == rule->pattern)
				rule->rflags |= FILTRULE_WILD2_PREFIX;
			/* If the pattern ends with ***, note that. */
			if (pat_len >= 3
			 && rule->pattern[pat_len-3] == '*'
			 && rule->pattern[pat_len-2] == '*'
			 && rule->pattern[pat_len-1] == '*')
				rule->rflags |= FILTRULE_WILD3_SUFFIX;
		}
	}

	if (rule->rflags & FILTRULE_PERDIR_MERGE) {
		filter_rule_list *lp;
		unsigned int len;
		int i;

		if ((cp = strrchr(rule->pattern, '/')) != NULL)
			cp++;
		else
			cp = rule->pattern;

		/* If the local merge file was already mentioned, don't
		 * add it again. */
		for (i = 0; i < mergelist_cnt; i++) {
			filter_rule *ex = mergelist_parents[i];
			const char *s;
			if (!ex)
				continue;
			s = strrchr(ex->pattern, '/');
			if (s)
				s++;
			else
				s = ex->pattern;
			len = strlen(s);
			if (len == pat_len - (cp - rule->pattern) && memcmp(s, cp, len) == 0) {
				free_filter(rule);
				return;
			}
		}

		if (!(lp = new_array0(filter_rule_list, 1)))
			out_of_memory("add_rule");
		if (asprintf(&lp->debug_type, " [per-dir %s]", cp) < 0)
			out_of_memory("add_rule");
		rule->u.mergelist = lp;

		if (mergelist_cnt == mergelist_size) {
			mergelist_size += 5;
			mergelist_parents = realloc_array(mergelist_parents,
						filter_rule *,
						mergelist_size);
			if (!mergelist_parents)
				out_of_memory("add_rule");
		}
		if (DEBUG_GTE(FILTER, 2)) {
			rprintf(FINFO, "[%s] activating mergelist #%d%s\n",
				who_am_i(), mergelist_cnt, lp->debug_type);
		}
		mergelist_parents[mergelist_cnt++] = rule;
	} else
		rule->u.slash_cnt = slash_cnt;

	if (!listp->tail) {
		rule->next = listp->head;
		listp->head = listp->tail = rule;
	} else {
		rule->next = listp->tail->next;
		listp->tail->next = rule;
		listp->tail = rule;
	}
}

/* This frees any non-inherited items, leaving just inherited items on the list. */
static void pop_filter_list(filter_rule_list *listp)
{
	filter_rule *inherited;

	if (!listp->tail)
		return;

	inherited = listp->tail->next;

	/* Truncate any inherited items from the local list. */
	listp->tail->next = NULL;
	/* Now free everything that is left. */
	free_filters(listp->head);

	listp->head = inherited;
	listp->tail = NULL;
}

/* This returns an expanded (absolute) filename for the merge-file name if
 * the name has any slashes in it OR if the parent_dirscan var is True;
 * otherwise it returns the original merge_file name.  If the len_ptr value
 * is non-NULL the merge_file name is limited by the referenced length
 * value and will be updated with the length of the resulting name.  We
 * always return a name that is null terminated, even if the merge_file
 * name was not. */
static char *parse_merge_name(const char *merge_file, unsigned int *len_ptr,
			      unsigned int prefix_skip)
{
	static char buf[MAXPATHLEN];
	char *fn, tmpbuf[MAXPATHLEN];
	unsigned int fn_len;

	if (!parent_dirscan && *merge_file != '/') {
		/* Return the name unchanged it doesn't have any slashes. */
		if (len_ptr) {
			const char *p = merge_file + *len_ptr;
			while (--p > merge_file && *p != '/') {}
			if (p == merge_file) {
				strlcpy(buf, merge_file, *len_ptr + 1);
				return buf;
			}
		} else if (strchr(merge_file, '/') == NULL)
			return (char *)merge_file;
	}

	fn = *merge_file == '/' ? buf : tmpbuf;
	if (sanitize_paths) {
		const char *r = prefix_skip ? "/" : NULL;
		/* null-terminate the name if it isn't already */
		if (len_ptr && merge_file[*len_ptr]) {
			char *to = fn == buf ? tmpbuf : buf;
			strlcpy(to, merge_file, *len_ptr + 1);
			merge_file = to;
		}
		if (!sanitize_path(fn, merge_file, r, dirbuf_depth, SP_DEFAULT)) {
			rprintf(FERROR, "merge-file name overflows: %s\n",
				merge_file);
			return NULL;
		}
		fn_len = strlen(fn);
	} else {
		strlcpy(fn, merge_file, len_ptr ? *len_ptr + 1 : MAXPATHLEN);
		fn_len = clean_fname(fn, CFN_COLLAPSE_DOT_DOT_DIRS);
	}

	/* If the name isn't in buf yet, it wasn't absolute. */
	if (fn != buf) {
		int d_len = dirbuf_len - prefix_skip;
		if (d_len + fn_len >= MAXPATHLEN) {
			rprintf(FERROR, "merge-file name overflows: %s\n", fn);
			return NULL;
		}
		memcpy(buf, dirbuf + prefix_skip, d_len);
		memcpy(buf + d_len, fn, fn_len + 1);
		fn_len = clean_fname(buf, CFN_COLLAPSE_DOT_DOT_DIRS);
	}

	if (len_ptr)
		*len_ptr = fn_len;
	return buf;
}

/* Sets the dirbuf and dirbuf_len values. */
void set_filter_dir(const char *dir, unsigned int dirlen)
{
	unsigned int len;
	if (*dir != '/') {
		memcpy(dirbuf, curr_dir, curr_dir_len);
		dirbuf[curr_dir_len] = '/';
		len = curr_dir_len + 1;
		if (len + dirlen >= MAXPATHLEN)
			dirlen = 0;
	} else
		len = 0;
	memcpy(dirbuf + len, dir, dirlen);
	dirbuf[dirlen + len] = '\0';
	dirbuf_len = clean_fname(dirbuf, CFN_COLLAPSE_DOT_DOT_DIRS);
	if (dirbuf_len > 1 && dirbuf[dirbuf_len-1] == '.'
	    && dirbuf[dirbuf_len-2] == '/')
		dirbuf_len -= 2;
	if (dirbuf_len != 1)
		dirbuf[dirbuf_len++] = '/';
	dirbuf[dirbuf_len] = '\0';
	if (sanitize_paths)
		dirbuf_depth = count_dir_elements(dirbuf + module_dirlen);
}

/* This routine takes a per-dir merge-file entry and finishes its setup.
 * If the name has a path portion then we check to see if it refers to a
 * parent directory of the first transfer dir.  If it does, we scan all the
 * dirs from that point through the parent dir of the transfer dir looking
 * for the per-dir merge-file in each one. */
static BOOL setup_merge_file(int mergelist_num, filter_rule *ex,
			     filter_rule_list *lp)
{
	char buf[MAXPATHLEN];
	char *x, *y, *pat = ex->pattern;
	unsigned int len;

	if (!(x = parse_merge_name(pat, NULL, 0)) || *x != '/')
		return 0;

	if (DEBUG_GTE(FILTER, 2)) {
		rprintf(FINFO, "[%s] performing parent_dirscan for mergelist #%d%s\n",
			who_am_i(), mergelist_num, lp->debug_type);
	}
	y = strrchr(x, '/');
	*y = '\0';
	ex->pattern = strdup(y+1);
	if (!*x)
		x = "/";
	if (*x == '/')
		strlcpy(buf, x, MAXPATHLEN);
	else
		pathjoin(buf, MAXPATHLEN, dirbuf, x);

	len = clean_fname(buf, CFN_COLLAPSE_DOT_DOT_DIRS);
	if (len != 1 && len < MAXPATHLEN-1) {
		buf[len++] = '/';
		buf[len] = '\0';
	}
	/* This ensures that the specified dir is a parent of the transfer. */
	for (x = buf, y = dirbuf; *x && *x == *y; x++, y++) {}
	if (*x)
		y += strlen(y); /* nope -- skip the scan */

	parent_dirscan = True;
	while (*y) {
		char save[MAXPATHLEN];
		strlcpy(save, y, MAXPATHLEN);
		*y = '\0';
		dirbuf_len = y - dirbuf;
		strlcpy(x, ex->pattern, MAXPATHLEN - (x - buf));
		parse_filter_file(lp, buf, ex, XFLG_ANCHORED2ABS);
		if (ex->rflags & FILTRULE_NO_INHERIT) {
			/* Free the undesired rules to clean up any per-dir
			 * mergelists they defined.  Otherwise pop_local_filters
			 * may crash trying to restore nonexistent state for
			 * those mergelists. */
			free_filters(lp->head);
			lp->head = NULL;
		}
		lp->tail = NULL;
		strlcpy(y, save, MAXPATHLEN);
		while ((*x++ = *y++) != '/') {}
	}
	parent_dirscan = False;
	if (DEBUG_GTE(FILTER, 2)) {
		rprintf(FINFO, "[%s] completed parent_dirscan for mergelist #%d%s\n",
			who_am_i(), mergelist_num, lp->debug_type);
	}
	free(pat);
	return 1;
}

struct local_filter_state {
	int mergelist_cnt;
	filter_rule_list mergelists[1];
};

/* Each time rsync changes to a new directory it call this function to
 * handle all the per-dir merge-files.  The "dir" value is the current path
 * relative to curr_dir (which might not be null-terminated).  We copy it
 * into dirbuf so that we can easily append a file name on the end. */
void *push_local_filters(const char *dir, unsigned int dirlen)
{
	struct local_filter_state *push;
	int i;

	set_filter_dir(dir, dirlen);
	if (DEBUG_GTE(FILTER, 2)) {
		rprintf(FINFO, "[%s] pushing local filters for %s\n",
			who_am_i(), dirbuf);
	}

	if (!mergelist_cnt) {
		/* No old state to save and no new merge files to push. */
		return NULL;
	}

	push = (struct local_filter_state *)new_array(char,
			  sizeof (struct local_filter_state)
			+ (mergelist_cnt-1) * sizeof (filter_rule_list));
	if (!push)
		out_of_memory("push_local_filters");

	push->mergelist_cnt = mergelist_cnt;
	for (i = 0; i < mergelist_cnt; i++) {
		filter_rule *ex = mergelist_parents[i];
		if (!ex)
			continue;
		memcpy(&push->mergelists[i], ex->u.mergelist, sizeof (filter_rule_list));
	}

	/* Note: parse_filter_file() might increase mergelist_cnt, so keep
	 * this loop separate from the above loop. */
	for (i = 0; i < mergelist_cnt; i++) {
		filter_rule *ex = mergelist_parents[i];
		filter_rule_list *lp;
		if (!ex)
			continue;
		lp = ex->u.mergelist;

		if (DEBUG_GTE(FILTER, 2)) {
			rprintf(FINFO, "[%s] pushing mergelist #%d%s\n",
				who_am_i(), i, lp->debug_type);
		}

		lp->tail = NULL; /* Switch any local rules to inherited. */
		if (ex->rflags & FILTRULE_NO_INHERIT)
			lp->head = NULL;

		if (ex->rflags & FILTRULE_FINISH_SETUP) {
			ex->rflags &= ~FILTRULE_FINISH_SETUP;
			if (setup_merge_file(i, ex, lp))
				set_filter_dir(dir, dirlen);
		}

		if (strlcpy(dirbuf + dirbuf_len, ex->pattern,
		    MAXPATHLEN - dirbuf_len) < MAXPATHLEN - dirbuf_len) {
			parse_filter_file(lp, dirbuf, ex,
					  XFLG_ANCHORED2ABS);
		} else {
			io_error |= IOERR_GENERAL;
			rprintf(FERROR,
			    "cannot add local filter rules in long-named directory: %s\n",
			    full_fname(dirbuf));
		}
		dirbuf[dirbuf_len] = '\0';
	}

	return (void*)push;
}

void pop_local_filters(void *mem)
{
	struct local_filter_state *pop = (struct local_filter_state *)mem;
	int i;
	int old_mergelist_cnt = pop ? pop->mergelist_cnt : 0;

	if (DEBUG_GTE(FILTER, 2))
		rprintf(FINFO, "[%s] popping local filters\n", who_am_i());

	for (i = mergelist_cnt; i-- > 0; ) {
		filter_rule *ex = mergelist_parents[i];
		filter_rule_list *lp;
		if (!ex)
			continue;
		lp = ex->u.mergelist;

		if (DEBUG_GTE(FILTER, 2)) {
			rprintf(FINFO, "[%s] popping mergelist #%d%s\n",
				who_am_i(), i, lp->debug_type);
		}

		pop_filter_list(lp);
		if (i >= old_mergelist_cnt && lp->head) {
			/* This mergelist does not exist in the state to be restored, but it
			 * still has inherited rules.  This can sometimes happen if a per-dir
			 * merge file calls setup_merge_file() in push_local_filters() and that
			 * leaves some inherited rules that aren't in the pushed list state. */
			if (DEBUG_GTE(FILTER, 2)) {
				rprintf(FINFO, "[%s] freeing parent_dirscan filters of mergelist #%d%s\n",
					who_am_i(), i, ex->u.mergelist->debug_type);
			}
			pop_filter_list(lp);
		}
	}

	if (!pop)
		return; /* No state to restore. */

	for (i = 0; i < old_mergelist_cnt; i++) {
		filter_rule *ex = mergelist_parents[i];
		if (!ex)
			continue;
		memcpy(ex->u.mergelist, &pop->mergelists[i], sizeof (filter_rule_list));
	}

	free(pop);
}

void change_local_filter_dir(const char *dname, int dlen, int dir_depth)
{
	static int cur_depth = -1;
	static void *filt_array[MAXPATHLEN/2+1];

	if (!dname) {
		for ( ; cur_depth >= 0; cur_depth--) {
			if (filt_array[cur_depth]) {
				pop_local_filters(filt_array[cur_depth]);
				filt_array[cur_depth] = NULL;
			}
		}
		return;
	}

	assert(dir_depth < MAXPATHLEN/2+1);

	for ( ; cur_depth >= dir_depth; cur_depth--) {
		if (filt_array[cur_depth]) {
			pop_local_filters(filt_array[cur_depth]);
			filt_array[cur_depth] = NULL;
		}
	}

	cur_depth = dir_depth;
	filt_array[cur_depth] = push_local_filters(dname, dlen);
}

static int rule_matches(const char *fname, filter_rule *ex, int name_is_dir)
{
	int slash_handling, str_cnt = 0, anchored_match = 0;
	int ret_match = ex->rflags & FILTRULE_NEGATE ? 0 : 1;
	char *p, *pattern = ex->pattern;
	const char *strings[16]; /* more than enough */
	const char *name = fname + (*fname == '/');

	if (!*name)
		return 0;

	if (!ex->u.slash_cnt && !(ex->rflags & FILTRULE_WILD2)) {
		/* If the pattern does not have any slashes AND it does
		 * not have a "**" (which could match a slash), then we
		 * just match the name portion of the path. */
		if ((p = strrchr(name,'/')) != NULL)
			name = p+1;
	} else if (ex->rflags & FILTRULE_ABS_PATH && *fname != '/'
	    && curr_dir_len > module_dirlen + 1) {
		/* If we're matching against an absolute-path pattern,
		 * we need to prepend our full path info. */
		strings[str_cnt++] = curr_dir + module_dirlen + 1;
		strings[str_cnt++] = "/";
	} else if (ex->rflags & FILTRULE_WILD2_PREFIX && *fname != '/') {
		/* Allow "**"+"/" to match at the start of the string. */
		strings[str_cnt++] = "/";
	}
	strings[str_cnt++] = name;
	if (name_is_dir) {
		/* Allow a trailing "/"+"***" to match the directory. */
		if (ex->rflags & FILTRULE_WILD3_SUFFIX)
			strings[str_cnt++] = "/";
	} else if (ex->rflags & FILTRULE_DIRECTORY)
		return !ret_match;
	strings[str_cnt] = NULL;

	if (*pattern == '/') {
		anchored_match = 1;
		pattern++;
	}

	if (!anchored_match && ex->u.slash_cnt
	    && !(ex->rflags & FILTRULE_WILD2)) {
		/* A non-anchored match with an infix slash and no "**"
		 * needs to match the last slash_cnt+1 name elements. */
		slash_handling = ex->u.slash_cnt + 1;
	} else if (!anchored_match && !(ex->rflags & FILTRULE_WILD2_PREFIX)
				   && ex->rflags & FILTRULE_WILD2) {
		/* A non-anchored match with an infix or trailing "**" (but not
		 * a prefixed "**") needs to try matching after every slash. */
		slash_handling = -1;
	} else {
		/* The pattern matches only at the start of the path or name. */
		slash_handling = 0;
	}

	if (ex->rflags & FILTRULE_WILD) {
		if (wildmatch_array(pattern, strings, slash_handling))
			return ret_match;
	} else if (str_cnt > 1) {
		if (litmatch_array(pattern, strings, slash_handling))
			return ret_match;
	} else if (anchored_match) {
		if (strcmp(name, pattern) == 0)
			return ret_match;
	} else {
		int l1 = strlen(name);
		int l2 = strlen(pattern);
		if (l2 <= l1 &&
		    strcmp(name+(l1-l2),pattern) == 0 &&
		    (l1==l2 || name[l1-(l2+1)] == '/')) {
			return ret_match;
		}
	}

	return !ret_match;
}

static void report_filter_result(enum logcode code, char const *name,
				 filter_rule const *ent,
				 int name_is_dir, const char *type)
{
	/* If a trailing slash is present to match only directories,
	 * then it is stripped out by add_rule().  So as a special
	 * case we add it back in here. */

	if (DEBUG_GTE(FILTER, 1)) {
		static char *actions[2][2]
		    = { {"show", "hid"}, {"risk", "protect"} };
		const char *w = who_am_i();
		rprintf(code, "[%s] %sing %s %s because of pattern %s%s%s\n",
		    w, actions[*w!='s'][!(ent->rflags & FILTRULE_INCLUDE)],
		    name_is_dir ? "directory" : "file", name, ent->pattern,
		    ent->rflags & FILTRULE_DIRECTORY ? "/" : "", type);
	}
}

/* Return -1 if file "name" is defined to be excluded by the specified
 * exclude list, 1 if it is included, and 0 if it was not matched. */
int check_filter(filter_rule_list *listp, enum logcode code,
		 const char *name, int name_is_dir)
{
	filter_rule *ent;

	for (ent = listp->head; ent; ent = ent->next) {
		if (ignore_perishable && ent->rflags & FILTRULE_PERISHABLE)
			continue;
		if (ent->rflags & FILTRULE_PERDIR_MERGE) {
			int rc = check_filter(ent->u.mergelist, code, name,
					      name_is_dir);
			if (rc)
				return rc;
			continue;
		}
		if (ent->rflags & FILTRULE_CVS_IGNORE) {
			int rc = check_filter(&cvs_filter_list, code, name,
					      name_is_dir);
			if (rc)
				return rc;
			continue;
		}
		if (rule_matches(name, ent, name_is_dir)) {
			report_filter_result(code, name, ent, name_is_dir,
					     listp->debug_type);
			return ent->rflags & FILTRULE_INCLUDE ? 1 : -1;
		}
	}

	return 0;
}

#define RULE_STRCMP(s,r) rule_strcmp((s), (r), sizeof (r) - 1)

static const uchar *rule_strcmp(const uchar *str, const char *rule, int rule_len)
{
	if (strncmp((char*)str, rule, rule_len) != 0)
		return NULL;
	if (isspace(str[rule_len]) || str[rule_len] == '_' || !str[rule_len])
		return str + rule_len - 1;
	if (str[rule_len] == ',')
		return str + rule_len;
	return NULL;
}

#define FILTRULES_FROM_CONTAINER (FILTRULE_ABS_PATH | FILTRULE_INCLUDE \
				| FILTRULE_DIRECTORY | FILTRULE_NEGATE \
				| FILTRULE_PERISHABLE)

/* Gets the next include/exclude rule from *rulestr_ptr and advances
 * *rulestr_ptr to point beyond it.  Stores the pattern's start (within
 * *rulestr_ptr) and length in *pat_ptr and *pat_len_ptr, and returns a newly
 * allocated filter_rule containing the rest of the information.  Returns
 * NULL if there are no more rules in the input.
 *
 * The template provides defaults for the new rule to inherit, and the
 * template rflags and the xflags additionally affect parsing. */
static filter_rule *parse_rule_tok(const char **rulestr_ptr,
				   const filter_rule *template, int xflags,
				   const char **pat_ptr, unsigned int *pat_len_ptr)
{
	const uchar *s = (const uchar *)*rulestr_ptr;
	filter_rule *rule;
	unsigned int len;

	if (template->rflags & FILTRULE_WORD_SPLIT) {
		/* Skip over any initial whitespace. */
		while (isspace(*s))
			s++;
		/* Update to point to real start of rule. */
		*rulestr_ptr = (const char *)s;
	}
	if (!*s)
		return NULL;

	if (!(rule = new0(filter_rule)))
		out_of_memory("parse_rule_tok");

	/* Inherit from the template.  Don't inherit FILTRULES_SIDES; we check
	 * that later. */
	rule->rflags = template->rflags & FILTRULES_FROM_CONTAINER;

	/* Figure out what kind of a filter rule "s" is pointing at.  Note
	 * that if FILTRULE_NO_PREFIXES is set, the rule is either an include
	 * or an exclude based on the inheritance of the FILTRULE_INCLUDE
	 * flag (above).  XFLG_OLD_PREFIXES indicates a compatibility mode
	 * for old include/exclude patterns where just "+ " and "- " are
	 * allowed as optional prefixes.  */
	if (template->rflags & FILTRULE_NO_PREFIXES) {
		if (*s == '!' && template->rflags & FILTRULE_CVS_IGNORE)
			rule->rflags |= FILTRULE_CLEAR_LIST; /* Tentative! */
	} else if (xflags & XFLG_OLD_PREFIXES) {
		if (*s == '-' && s[1] == ' ') {
			rule->rflags &= ~FILTRULE_INCLUDE;
			s += 2;
		} else if (*s == '+' && s[1] == ' ') {
			rule->rflags |= FILTRULE_INCLUDE;
			s += 2;
		} else if (*s == '!')
			rule->rflags |= FILTRULE_CLEAR_LIST; /* Tentative! */
	} else {
		char ch = 0;
		BOOL prefix_specifies_side = False;
		switch (*s) {
		case 'c':
			if ((s = RULE_STRCMP(s, "clear")) != NULL)
				ch = '!';
			break;
		case 'd':
			if ((s = RULE_STRCMP(s, "dir-merge")) != NULL)
				ch = ':';
			break;
		case 'e':
			if ((s = RULE_STRCMP(s, "exclude")) != NULL)
				ch = '-';
			break;
		case 'h':
			if ((s = RULE_STRCMP(s, "hide")) != NULL)
				ch = 'H';
			break;
		case 'i':
			if ((s = RULE_STRCMP(s, "include")) != NULL)
				ch = '+';
			break;
		case 'm':
			if ((s = RULE_STRCMP(s, "merge")) != NULL)
				ch = '.';
			break;
		case 'p':
			if ((s = RULE_STRCMP(s, "protect")) != NULL)
				ch = 'P';
			break;
		case 'r':
			if ((s = RULE_STRCMP(s, "risk")) != NULL)
				ch = 'R';
			break;
		case 's':
			if ((s = RULE_STRCMP(s, "show")) != NULL)
				ch = 'S';
			break;
		default:
			ch = *s;
			if (s[1] == ',')
				s++;
			break;
		}
		switch (ch) {
		case ':':
			rule->rflags |= FILTRULE_PERDIR_MERGE
				      | FILTRULE_FINISH_SETUP;
			/* FALL THROUGH */
		case '.':
			rule->rflags |= FILTRULE_MERGE_FILE;
			break;
		case '+':
			rule->rflags |= FILTRULE_INCLUDE;
			break;
		case '-':
			break;
		case 'S':
			rule->rflags |= FILTRULE_INCLUDE;
			/* FALL THROUGH */
		case 'H':
			rule->rflags |= FILTRULE_SENDER_SIDE;
			prefix_specifies_side = True;
			break;
		case 'R':
			rule->rflags |= FILTRULE_INCLUDE;
			/* FALL THROUGH */
		case 'P':
			rule->rflags |= FILTRULE_RECEIVER_SIDE;
			prefix_specifies_side = True;
			break;
		case '!':
			rule->rflags |= FILTRULE_CLEAR_LIST;
			break;
		default:
			rprintf(FERROR, "Unknown filter rule: `%s'\n", *rulestr_ptr);
			exit_cleanup(RERR_SYNTAX);
		}
		while (ch != '!' && *++s && *s != ' ' && *s != '_') {
			if (template->rflags & FILTRULE_WORD_SPLIT && isspace(*s)) {
				s--;
				break;
			}
			switch (*s) {
			default:
			    invalid:
				rprintf(FERROR,
					"invalid modifier '%c' at position %d in filter rule: %s\n",
					*s, (int)(s - (const uchar *)*rulestr_ptr), *rulestr_ptr);
				exit_cleanup(RERR_SYNTAX);
			case '-':
				if (!BITS_SETnUNSET(rule->rflags, FILTRULE_MERGE_FILE, FILTRULE_NO_PREFIXES))
					goto invalid;
				rule->rflags |= FILTRULE_NO_PREFIXES;
				break;
			case '+':
				if (!BITS_SETnUNSET(rule->rflags, FILTRULE_MERGE_FILE, FILTRULE_NO_PREFIXES))
					goto invalid;
				rule->rflags |= FILTRULE_NO_PREFIXES
					      | FILTRULE_INCLUDE;
				break;
			case '/':
				rule->rflags |= FILTRULE_ABS_PATH;
				break;
			case '!':
				/* Negation really goes with the pattern, so it
				 * isn't useful as a merge-file default. */
				if (rule->rflags & FILTRULE_MERGE_FILE)
					goto invalid;
				rule->rflags |= FILTRULE_NEGATE;
				break;
			case 'C':
				if (rule->rflags & FILTRULE_NO_PREFIXES || prefix_specifies_side)
					goto invalid;
				rule->rflags |= FILTRULE_NO_PREFIXES
					      | FILTRULE_WORD_SPLIT
					      | FILTRULE_NO_INHERIT
					      | FILTRULE_CVS_IGNORE;
				break;
			case 'e':
				if (!(rule->rflags & FILTRULE_MERGE_FILE))
					goto invalid;
				rule->rflags |= FILTRULE_EXCLUDE_SELF;
				break;
			case 'n':
				if (!(rule->rflags & FILTRULE_MERGE_FILE))
					goto invalid;
				rule->rflags |= FILTRULE_NO_INHERIT;
				break;
			case 'p':
				rule->rflags |= FILTRULE_PERISHABLE;
				break;
			case 'r':
				if (prefix_specifies_side)
					goto invalid;
				rule->rflags |= FILTRULE_RECEIVER_SIDE;
				break;
			case 's':
				if (prefix_specifies_side)
					goto invalid;
				rule->rflags |= FILTRULE_SENDER_SIDE;
				break;
			case 'w':
				if (!(rule->rflags & FILTRULE_MERGE_FILE))
					goto invalid;
				rule->rflags |= FILTRULE_WORD_SPLIT;
				break;
			}
		}
		if (*s)
			s++;
	}
	if (template->rflags & FILTRULES_SIDES) {
		if (rule->rflags & FILTRULES_SIDES) {
			/* The filter and template both specify side(s).  This
			 * is dodgy (and won't work correctly if the template is
			 * a one-sided per-dir merge rule), so reject it. */
			rprintf(FERROR,
				"specified-side merge file contains specified-side filter: %s\n",
				*rulestr_ptr);
			exit_cleanup(RERR_SYNTAX);
		}
		rule->rflags |= template->rflags & FILTRULES_SIDES;
	}

	if (template->rflags & FILTRULE_WORD_SPLIT) {
		const uchar *cp = s;
		/* Token ends at whitespace or the end of the string. */
		while (!isspace(*cp) && *cp != '\0')
			cp++;
		len = cp - s;
	} else
		len = strlen((char*)s);

	if (rule->rflags & FILTRULE_CLEAR_LIST) {
		if (!(rule->rflags & FILTRULE_NO_PREFIXES)
		 && !(xflags & XFLG_OLD_PREFIXES) && len) {
			rprintf(FERROR,
				"'!' rule has trailing characters: %s\n", *rulestr_ptr);
			exit_cleanup(RERR_SYNTAX);
		}
		if (len > 1)
			rule->rflags &= ~FILTRULE_CLEAR_LIST;
	} else if (!len && !(rule->rflags & FILTRULE_CVS_IGNORE)) {
		rprintf(FERROR, "unexpected end of filter rule: %s\n", *rulestr_ptr);
		exit_cleanup(RERR_SYNTAX);
	}

	/* --delete-excluded turns an un-modified include/exclude into a sender-side rule.  */
	if (delete_excluded
	 && !(rule->rflags & (FILTRULES_SIDES|FILTRULE_MERGE_FILE|FILTRULE_PERDIR_MERGE)))
		rule->rflags |= FILTRULE_SENDER_SIDE;

	*pat_ptr = (const char *)s;
	*pat_len_ptr = len;
	*rulestr_ptr = *pat_ptr + len;
	return rule;
}

static char default_cvsignore[] =
	/* These default ignored items come from the CVS manual. */
	"RCS SCCS CVS CVS.adm RCSLOG cvslog.* tags TAGS"
	" .make.state .nse_depinfo *~ #* .#* ,* _$* *$"
	" *.old *.bak *.BAK *.orig *.rej .del-*"
	" *.a *.olb *.o *.obj *.so *.exe"
	" *.Z *.elc *.ln core"
	/* The rest we added to suit ourself. */
	" .svn/ .git/ .hg/ .bzr/";

static void get_cvs_excludes(uint32 rflags)
{
	static int initialized = 0;
	char *p, fname[MAXPATHLEN];

	if (initialized)
		return;
	initialized = 1;

	parse_filter_str(&cvs_filter_list, default_cvsignore,
			 rule_template(rflags | (protocol_version >= 30 ? FILTRULE_PERISHABLE : 0)),
			 0);

	p = module_id >= 0 && lp_use_chroot(module_id) ? "/" : getenv("HOME");
	if (p && pathjoin(fname, MAXPATHLEN, p, ".cvsignore") < MAXPATHLEN)
		parse_filter_file(&cvs_filter_list, fname, rule_template(rflags), 0);

	parse_filter_str(&cvs_filter_list, getenv("CVSIGNORE"), rule_template(rflags), 0);
}

const filter_rule *rule_template(uint32 rflags)
{
	static filter_rule template; /* zero-initialized */
	template.rflags = rflags;
	return &template;
}

void parse_filter_str(filter_rule_list *listp, const char *rulestr,
		     const filter_rule *template, int xflags)
{
	filter_rule *rule;
	const char *pat;
	unsigned int pat_len;

	if (!rulestr)
		return;

	while (1) {
		uint32 new_rflags;

		/* Remember that the returned string is NOT '\0' terminated! */
		if (!(rule = parse_rule_tok(&rulestr, template, xflags, &pat, &pat_len)))
			break;

		if (pat_len >= MAXPATHLEN) {
			rprintf(FERROR, "discarding over-long filter: %.*s\n",
				(int)pat_len, pat);
		    free_continue:
			free_filter(rule);
			continue;
		}

		new_rflags = rule->rflags;
		if (new_rflags & FILTRULE_CLEAR_LIST) {
			if (DEBUG_GTE(FILTER, 2)) {
				rprintf(FINFO,
					"[%s] clearing filter list%s\n",
					who_am_i(), listp->debug_type);
			}
			pop_filter_list(listp);
			listp->head = NULL;
			goto free_continue;
		}

		if (new_rflags & FILTRULE_MERGE_FILE) {
			if (!pat_len) {
				pat = ".cvsignore";
				pat_len = 10;
			}
			if (new_rflags & FILTRULE_EXCLUDE_SELF) {
				const char *name;
				filter_rule *excl_self;

				if (!(excl_self = new0(filter_rule)))
					out_of_memory("parse_filter_str");
				/* Find the beginning of the basename and add an exclude for it. */
				for (name = pat + pat_len; name > pat && name[-1] != '/'; name--) {}
				add_rule(listp, name, (pat + pat_len) - name, excl_self, 0);
				rule->rflags &= ~FILTRULE_EXCLUDE_SELF;
			}
			if (new_rflags & FILTRULE_PERDIR_MERGE) {
				if (parent_dirscan) {
					const char *p;
					unsigned int len = pat_len;
					if ((p = parse_merge_name(pat, &len, module_dirlen)))
						add_rule(listp, p, len, rule, 0);
					else
						free_filter(rule);
					continue;
				}
			} else {
				const char *p;
				unsigned int len = pat_len;
				if ((p = parse_merge_name(pat, &len, 0)))
					parse_filter_file(listp, p, rule, XFLG_FATAL_ERRORS);
				free_filter(rule);
				continue;
			}
		}

		add_rule(listp, pat, pat_len, rule, xflags);

		if (new_rflags & FILTRULE_CVS_IGNORE
		    && !(new_rflags & FILTRULE_MERGE_FILE))
			get_cvs_excludes(new_rflags);
	}
}

void parse_filter_file(filter_rule_list *listp, const char *fname, const filter_rule *template, int xflags)
{
	FILE *fp;
	char line[BIGPATHBUFLEN];
	char *eob = line + sizeof line - 1;
	BOOL word_split = (template->rflags & FILTRULE_WORD_SPLIT) != 0;

	if (!fname || !*fname)
		return;

	if (*fname != '-' || fname[1] || am_server) {
		if (daemon_filter_list.head) {
			strlcpy(line, fname, sizeof line);
			clean_fname(line, CFN_COLLAPSE_DOT_DOT_DIRS);
			if (check_filter(&daemon_filter_list, FLOG, line, 0) < 0)
				fp = NULL;
			else
				fp = fopen(line, "rb");
		} else
			fp = fopen(fname, "rb");
	} else
		fp = stdin;

	if (DEBUG_GTE(FILTER, 2)) {
		rprintf(FINFO, "[%s] parse_filter_file(%s,%x,%x)%s\n",
			who_am_i(), fname, template->rflags, xflags,
			fp ? "" : " [not found]");
	}

	if (!fp) {
		if (xflags & XFLG_FATAL_ERRORS) {
			rsyserr(FERROR, errno,
				"failed to open %sclude file %s",
				template->rflags & FILTRULE_INCLUDE ? "in" : "ex",
				fname);
			exit_cleanup(RERR_FILEIO);
		}
		return;
	}
	dirbuf[dirbuf_len] = '\0';

	while (1) {
		char *s = line;
		int ch, overflow = 0;
		while (1) {
			if ((ch = getc(fp)) == EOF) {
				if (ferror(fp) && errno == EINTR) {
					clearerr(fp);
					continue;
				}
				break;
			}
			if (word_split && isspace(ch))
				break;
			if (eol_nulls? !ch : (ch == '\n' || ch == '\r'))
				break;
			if (s < eob)
				*s++ = ch;
			else
				overflow = 1;
		}
		if (overflow) {
			rprintf(FERROR, "discarding over-long filter: %s...\n", line);
			s = line;
		}
		*s = '\0';
		/* Skip an empty token and (when line parsing) comments. */
		if (*line && (word_split || (*line != ';' && *line != '#')))
			parse_filter_str(listp, line, template, xflags);
		if (ch == EOF)
			break;
	}
	fclose(fp);
}

/* If the "for_xfer" flag is set, the prefix is made compatible with the
 * current protocol_version (if possible) or a NULL is returned (if not
 * possible). */
char *get_rule_prefix(filter_rule *rule, const char *pat, int for_xfer,
		      unsigned int *plen_ptr)
{
	static char buf[MAX_RULE_PREFIX+1];
	char *op = buf;
	int legal_len = for_xfer && protocol_version < 29 ? 1 : MAX_RULE_PREFIX-1;

	if (rule->rflags & FILTRULE_PERDIR_MERGE) {
		if (legal_len == 1)
			return NULL;
		*op++ = ':';
	} else if (rule->rflags & FILTRULE_INCLUDE)
		*op++ = '+';
	else if (legal_len != 1
	    || ((*pat == '-' || *pat == '+') && pat[1] == ' '))
		*op++ = '-';
	else
		legal_len = 0;

	if (rule->rflags & FILTRULE_ABS_PATH)
		*op++ = '/';
	if (rule->rflags & FILTRULE_NEGATE)
		*op++ = '!';
	if (rule->rflags & FILTRULE_CVS_IGNORE)
		*op++ = 'C';
	else {
		if (rule->rflags & FILTRULE_NO_INHERIT)
			*op++ = 'n';
		if (rule->rflags & FILTRULE_WORD_SPLIT)
			*op++ = 'w';
		if (rule->rflags & FILTRULE_NO_PREFIXES) {
			if (rule->rflags & FILTRULE_INCLUDE)
				*op++ = '+';
			else
				*op++ = '-';
		}
	}
	if (rule->rflags & FILTRULE_EXCLUDE_SELF)
		*op++ = 'e';
	if (rule->rflags & FILTRULE_SENDER_SIDE
	    && (!for_xfer || protocol_version >= 29))
		*op++ = 's';
	if (rule->rflags & FILTRULE_RECEIVER_SIDE
	    && (!for_xfer || protocol_version >= 29
	     || (delete_excluded && am_sender)))
		*op++ = 'r';
	if (rule->rflags & FILTRULE_PERISHABLE) {
		if (!for_xfer || protocol_version >= 30)
			*op++ = 'p';
		else if (am_sender)
			return NULL;
	}
	if (op - buf > legal_len)
		return NULL;
	if (legal_len)
		*op++ = ' ';
	*op = '\0';
	if (plen_ptr)
		*plen_ptr = op - buf;
	return buf;
}

static void send_rules(int f_out, filter_rule_list *flp)
{
	filter_rule *ent, *prev = NULL;

	for (ent = flp->head; ent; ent = ent->next) {
		unsigned int len, plen, dlen;
		int elide = 0;
		char *p;

		/* Note we need to check delete_excluded here in addition to
		 * the code in parse_rule_tok() because some rules may have
		 * been added before we found the --delete-excluded option.
		 * We must also elide any CVS merge-file rules to avoid a
		 * backward compatibility problem, and we elide any no-prefix
		 * merge files as an optimization (since they can only have
		 * include/exclude rules). */
		if (ent->rflags & FILTRULE_SENDER_SIDE)
			elide = am_sender ? 1 : -1;
		if (ent->rflags & FILTRULE_RECEIVER_SIDE)
			elide = elide ? 0 : am_sender ? -1 : 1;
		else if (delete_excluded && !elide
		 && (!(ent->rflags & FILTRULE_PERDIR_MERGE)
		  || ent->rflags & FILTRULE_NO_PREFIXES))
			elide = am_sender ? 1 : -1;
		if (elide < 0) {
			if (prev)
				prev->next = ent->next;
			else
				flp->head = ent->next;
		} else
			prev = ent;
		if (elide > 0)
			continue;
		if (ent->rflags & FILTRULE_CVS_IGNORE
		    && !(ent->rflags & FILTRULE_MERGE_FILE)) {
			int f = am_sender || protocol_version < 29 ? f_out : -2;
			send_rules(f, &cvs_filter_list);
			if (f == f_out)
				continue;
		}
		p = get_rule_prefix(ent, ent->pattern, 1, &plen);
		if (!p) {
			rprintf(FERROR,
				"filter rules are too modern for remote rsync.\n");
			exit_cleanup(RERR_PROTOCOL);
		}
		if (f_out < 0)
			continue;
		len = strlen(ent->pattern);
		dlen = ent->rflags & FILTRULE_DIRECTORY ? 1 : 0;
		if (!(plen + len + dlen))
			continue;
		write_int(f_out, plen + len + dlen);
		if (plen)
			write_buf(f_out, p, plen);
		write_buf(f_out, ent->pattern, len);
		if (dlen)
			write_byte(f_out, '/');
	}
	flp->tail = prev;
}

/* This is only called by the client. */
void send_filter_list(int f_out)
{
	int receiver_wants_list = prune_empty_dirs
	    || (delete_mode && (!delete_excluded || protocol_version >= 29));

	if (local_server || (am_sender && !receiver_wants_list))
		f_out = -1;
	if (cvs_exclude && am_sender) {
		if (protocol_version >= 29)
			parse_filter_str(&filter_list, ":C", rule_template(0), 0);
		parse_filter_str(&filter_list, "-C", rule_template(0), 0);
	}

	send_rules(f_out, &filter_list);

	if (f_out >= 0)
		write_int(f_out, 0);

	if (cvs_exclude) {
		if (!am_sender || protocol_version < 29)
			parse_filter_str(&filter_list, ":C", rule_template(0), 0);
		if (!am_sender)
			parse_filter_str(&filter_list, "-C", rule_template(0), 0);
	}
}

/* This is only called by the server. */
void recv_filter_list(int f_in)
{
	char line[BIGPATHBUFLEN];
	int xflags = protocol_version >= 29 ? 0 : XFLG_OLD_PREFIXES;
	int receiver_wants_list = prune_empty_dirs
	    || (delete_mode
	     && (!delete_excluded || protocol_version >= 29));
	unsigned int len;

	if (!local_server && (am_sender || receiver_wants_list)) {
		while ((len = read_int(f_in)) != 0) {
			if (len >= sizeof line)
				overflow_exit("recv_rules");
			read_sbuf(f_in, line, len);
			parse_filter_str(&filter_list, line, rule_template(0), xflags);
		}
	}

	if (cvs_exclude) {
		if (local_server || am_sender || protocol_version < 29)
			parse_filter_str(&filter_list, ":C", rule_template(0), 0);
		if (local_server || am_sender)
			parse_filter_str(&filter_list, "-C", rule_template(0), 0);
	}

	if (local_server) /* filter out any rules that aren't for us. */
		send_rules(-1, &filter_list);
}
