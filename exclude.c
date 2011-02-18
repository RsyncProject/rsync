/*
 * The filter include/exclude routines.
 *
 * Copyright (C) 1996-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2002 Martin Pool
 * Copyright (C) 2003-2009 Wayne Davison
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

struct filter_list_struct filter_list = { 0, 0, "" };
struct filter_list_struct cvs_filter_list = { 0, 0, " [global CVS]" };
struct filter_list_struct daemon_filter_list = { 0, 0, " [daemon]" };

/* Need room enough for ":MODS " prefix plus some room to grow. */
#define MAX_RULE_PREFIX (16)

#define MODIFIERS_MERGE_FILE "-+Cenw"
#define MODIFIERS_INCL_EXCL "/!Crsp"
#define MODIFIERS_HIDE_PROTECT "/!p"

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
static struct filter_struct **mergelist_parents;
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

static void free_filter(struct filter_struct *ex)
{
	if (ex->match_flags & MATCHFLG_PERDIR_MERGE) {
		free(ex->u.mergelist->debug_type);
		free(ex->u.mergelist);
		mergelist_cnt--;
	}
	free(ex->pattern);
	free(ex);
}

/* Build a filter structure given a filter pattern.  The value in "pat"
 * is not null-terminated. */
static void add_rule(struct filter_list_struct *listp, const char *pat,
		     unsigned int pat_len, uint32 mflags, int xflags)
{
	struct filter_struct *ret;
	const char *cp;
	unsigned int pre_len, suf_len, slash_cnt = 0;

	if (verbose > 2) {
		rprintf(FINFO, "[%s] add_rule(%s%.*s%s)%s\n",
			who_am_i(), get_rule_prefix(mflags, pat, 0, NULL),
			(int)pat_len, pat,
			(mflags & MATCHFLG_DIRECTORY) ? "/" : "",
			listp->debug_type);
	}

	/* These flags also indicate that we're reading a list that
	 * needs to be filtered now, not post-filtered later. */
	if (xflags & (XFLG_ANCHORED2ABS|XFLG_ABS_IF_SLASH)) {
		uint32 mf = mflags & (MATCHFLG_RECEIVER_SIDE|MATCHFLG_SENDER_SIDE);
		if (am_sender) {
			if (mf == MATCHFLG_RECEIVER_SIDE)
				return;
		} else {
			if (mf == MATCHFLG_SENDER_SIDE)
				return;
		}
	}

	if (!(ret = new0(struct filter_struct)))
		out_of_memory("add_rule");

	if (pat_len > 1 && pat[pat_len-1] == '/') {
		pat_len--;
		mflags |= MATCHFLG_DIRECTORY;
	}

	for (cp = pat; cp < pat + pat_len; cp++) {
		if (*cp == '/')
			slash_cnt++;
	}

	if (!(mflags & (MATCHFLG_ABS_PATH | MATCHFLG_MERGE_FILE))
	 && ((xflags & (XFLG_ANCHORED2ABS|XFLG_ABS_IF_SLASH) && *pat == '/')
	  || (xflags & XFLG_ABS_IF_SLASH && slash_cnt))) {
		mflags |= MATCHFLG_ABS_PATH;
		if (*pat == '/')
			pre_len = dirbuf_len - module_dirlen - 1;
		else
			pre_len = 0;
	} else
		pre_len = 0;

	/* The daemon wants dir-exclude rules to get an appended "/" + "***". */
	if (xflags & XFLG_DIR2WILD3
	 && BITS_SETnUNSET(mflags, MATCHFLG_DIRECTORY, MATCHFLG_INCLUDE)) {
		mflags &= ~MATCHFLG_DIRECTORY;
		suf_len = sizeof SLASH_WILD3_SUFFIX - 1;
	} else
		suf_len = 0;

	if (!(ret->pattern = new_array(char, pre_len + pat_len + suf_len + 1)))
		out_of_memory("add_rule");
	if (pre_len) {
		memcpy(ret->pattern, dirbuf + module_dirlen, pre_len);
		for (cp = ret->pattern; cp < ret->pattern + pre_len; cp++) {
			if (*cp == '/')
				slash_cnt++;
		}
	}
	strlcpy(ret->pattern + pre_len, pat, pat_len + 1);
	pat_len += pre_len;
	if (suf_len) {
		memcpy(ret->pattern + pat_len, SLASH_WILD3_SUFFIX, suf_len+1);
		pat_len += suf_len;
		slash_cnt++;
	}

	if (strpbrk(ret->pattern, "*[?")) {
		mflags |= MATCHFLG_WILD;
		if ((cp = strstr(ret->pattern, "**")) != NULL) {
			mflags |= MATCHFLG_WILD2;
			/* If the pattern starts with **, note that. */
			if (cp == ret->pattern)
				mflags |= MATCHFLG_WILD2_PREFIX;
			/* If the pattern ends with ***, note that. */
			if (pat_len >= 3
			 && ret->pattern[pat_len-3] == '*'
			 && ret->pattern[pat_len-2] == '*'
			 && ret->pattern[pat_len-1] == '*')
				mflags |= MATCHFLG_WILD3_SUFFIX;
		}
	}

	if (mflags & MATCHFLG_PERDIR_MERGE) {
		struct filter_list_struct *lp;
		unsigned int len;
		int i;

		if ((cp = strrchr(ret->pattern, '/')) != NULL)
			cp++;
		else
			cp = ret->pattern;

		/* If the local merge file was already mentioned, don't
		 * add it again. */
		for (i = 0; i < mergelist_cnt; i++) {
			struct filter_struct *ex = mergelist_parents[i];
			const char *s = strrchr(ex->pattern, '/');
			if (s)
				s++;
			else
				s = ex->pattern;
			len = strlen(s);
			if (len == pat_len - (cp - ret->pattern)
			    && memcmp(s, cp, len) == 0) {
				free_filter(ret);
				return;
			}
		}

		if (!(lp = new_array(struct filter_list_struct, 1)))
			out_of_memory("add_rule");
		lp->head = lp->tail = NULL;
		if (asprintf(&lp->debug_type, " [per-dir %s]", cp) < 0)
			out_of_memory("add_rule");
		ret->u.mergelist = lp;

		if (mergelist_cnt == mergelist_size) {
			mergelist_size += 5;
			mergelist_parents = realloc_array(mergelist_parents,
						struct filter_struct *,
						mergelist_size);
			if (!mergelist_parents)
				out_of_memory("add_rule");
		}
		mergelist_parents[mergelist_cnt++] = ret;
	} else
		ret->u.slash_cnt = slash_cnt;

	ret->match_flags = mflags;

	if (!listp->tail) {
		ret->next = listp->head;
		listp->head = listp->tail = ret;
	} else {
		ret->next = listp->tail->next;
		listp->tail->next = ret;
		listp->tail = ret;
	}
}

static void clear_filter_list(struct filter_list_struct *listp)
{
	if (listp->tail) {
		struct filter_struct *ent, *next;
		/* Truncate any inherited items from the local list. */
		listp->tail->next = NULL;
		/* Now free everything that is left. */
		for (ent = listp->head; ent; ent = next) {
			next = ent->next;
			free_filter(ent);
		}
	}

	listp->head = listp->tail = NULL;
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

	/* If the name isn't in buf yet, it's wasn't absolute. */
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
static BOOL setup_merge_file(struct filter_struct *ex,
			     struct filter_list_struct *lp)
{
	char buf[MAXPATHLEN];
	char *x, *y, *pat = ex->pattern;
	unsigned int len;

	if (!(x = parse_merge_name(pat, NULL, 0)) || *x != '/')
		return 0;

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
		parse_filter_file(lp, buf, ex->match_flags, XFLG_ANCHORED2ABS);
		if (ex->match_flags & MATCHFLG_NO_INHERIT)
			lp->head = NULL;
		lp->tail = NULL;
		strlcpy(y, save, MAXPATHLEN);
		while ((*x++ = *y++) != '/') {}
	}
	parent_dirscan = False;
	free(pat);
	return 1;
}

/* Each time rsync changes to a new directory it call this function to
 * handle all the per-dir merge-files.  The "dir" value is the current path
 * relative to curr_dir (which might not be null-terminated).  We copy it
 * into dirbuf so that we can easily append a file name on the end. */
void *push_local_filters(const char *dir, unsigned int dirlen)
{
	struct filter_list_struct *ap, *push;
	int i;

	set_filter_dir(dir, dirlen);

	if (!mergelist_cnt)
		return NULL;

	push = new_array(struct filter_list_struct, mergelist_cnt);
	if (!push)
		out_of_memory("push_local_filters");

	for (i = 0, ap = push; i < mergelist_cnt; i++) {
		memcpy(ap++, mergelist_parents[i]->u.mergelist,
		       sizeof (struct filter_list_struct));
	}

	/* Note: parse_filter_file() might increase mergelist_cnt, so keep
	 * this loop separate from the above loop. */
	for (i = 0; i < mergelist_cnt; i++) {
		struct filter_struct *ex = mergelist_parents[i];
		struct filter_list_struct *lp = ex->u.mergelist;

		if (verbose > 2) {
			rprintf(FINFO, "[%s] pushing filter list%s\n",
				who_am_i(), lp->debug_type);
		}

		lp->tail = NULL; /* Switch any local rules to inherited. */
		if (ex->match_flags & MATCHFLG_NO_INHERIT)
			lp->head = NULL;

		if (ex->match_flags & MATCHFLG_FINISH_SETUP) {
			ex->match_flags &= ~MATCHFLG_FINISH_SETUP;
			if (setup_merge_file(ex, lp))
				set_filter_dir(dir, dirlen);
		}

		if (strlcpy(dirbuf + dirbuf_len, ex->pattern,
		    MAXPATHLEN - dirbuf_len) < MAXPATHLEN - dirbuf_len) {
			parse_filter_file(lp, dirbuf, ex->match_flags,
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
	struct filter_list_struct *ap, *pop = (struct filter_list_struct*)mem;
	int i;

	for (i = mergelist_cnt; i-- > 0; ) {
		struct filter_struct *ex = mergelist_parents[i];
		struct filter_list_struct *lp = ex->u.mergelist;

		if (verbose > 2) {
			rprintf(FINFO, "[%s] popping filter list%s\n",
				who_am_i(), lp->debug_type);
		}

		clear_filter_list(lp);
	}

	if (!pop)
		return;

	for (i = 0, ap = pop; i < mergelist_cnt; i++) {
		memcpy(mergelist_parents[i]->u.mergelist, ap++,
		       sizeof (struct filter_list_struct));
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

static int rule_matches(const char *fname, struct filter_struct *ex, int name_is_dir)
{
	int slash_handling, str_cnt = 0, anchored_match = 0;
	int ret_match = ex->match_flags & MATCHFLG_NEGATE ? 0 : 1;
	char *p, *pattern = ex->pattern;
	const char *strings[16]; /* more than enough */
	const char *name = fname + (*fname == '/');

	if (!*name)
		return 0;

	if (!ex->u.slash_cnt && !(ex->match_flags & MATCHFLG_WILD2)) {
		/* If the pattern does not have any slashes AND it does
		 * not have a "**" (which could match a slash), then we
		 * just match the name portion of the path. */
		if ((p = strrchr(name,'/')) != NULL)
			name = p+1;
	} else if (ex->match_flags & MATCHFLG_ABS_PATH && *fname != '/'
	    && curr_dir_len > module_dirlen + 1) {
		/* If we're matching against an absolute-path pattern,
		 * we need to prepend our full path info. */
		strings[str_cnt++] = curr_dir + module_dirlen + 1;
		strings[str_cnt++] = "/";
	} else if (ex->match_flags & MATCHFLG_WILD2_PREFIX && *fname != '/') {
		/* Allow "**"+"/" to match at the start of the string. */
		strings[str_cnt++] = "/";
	}
	strings[str_cnt++] = name;
	if (name_is_dir) {
		/* Allow a trailing "/"+"***" to match the directory. */
		if (ex->match_flags & MATCHFLG_WILD3_SUFFIX)
			strings[str_cnt++] = "/";
	} else if (ex->match_flags & MATCHFLG_DIRECTORY)
		return !ret_match;
	strings[str_cnt] = NULL;

	if (*pattern == '/') {
		anchored_match = 1;
		pattern++;
	}

	if (!anchored_match && ex->u.slash_cnt
	    && !(ex->match_flags & MATCHFLG_WILD2)) {
		/* A non-anchored match with an infix slash and no "**"
		 * needs to match the last slash_cnt+1 name elements. */
		slash_handling = ex->u.slash_cnt + 1;
	} else if (!anchored_match && !(ex->match_flags & MATCHFLG_WILD2_PREFIX)
				   && ex->match_flags & MATCHFLG_WILD2) {
		/* A non-anchored match with an infix or trailing "**" (but not
		 * a prefixed "**") needs to try matching after every slash. */
		slash_handling = -1;
	} else {
		/* The pattern matches only at the start of the path or name. */
		slash_handling = 0;
	}

	if (ex->match_flags & MATCHFLG_WILD) {
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
                                 struct filter_struct const *ent,
                                 int name_is_dir, const char *type)
{
	/* If a trailing slash is present to match only directories,
	 * then it is stripped out by add_rule().  So as a special
	 * case we add it back in here. */

	if (verbose >= 2) {
		static char *actions[2][2]
		    = { {"show", "hid"}, {"risk", "protect"} };
		const char *w = who_am_i();
		rprintf(code, "[%s] %sing %s %s because of pattern %s%s%s\n",
		    w, actions[*w!='s'][!(ent->match_flags&MATCHFLG_INCLUDE)],
		    name_is_dir ? "directory" : "file", name, ent->pattern,
		    ent->match_flags & MATCHFLG_DIRECTORY ? "/" : "", type);
	}
}


/*
 * Return -1 if file "name" is defined to be excluded by the specified
 * exclude list, 1 if it is included, and 0 if it was not matched.
 */
int check_filter(struct filter_list_struct *listp, enum logcode code,
		 const char *name, int name_is_dir)
{
	struct filter_struct *ent;

	for (ent = listp->head; ent; ent = ent->next) {
		if (ignore_perishable && ent->match_flags & MATCHFLG_PERISHABLE)
			continue;
		if (ent->match_flags & MATCHFLG_PERDIR_MERGE) {
			int rc = check_filter(ent->u.mergelist, code, name,
					      name_is_dir);
			if (rc)
				return rc;
			continue;
		}
		if (ent->match_flags & MATCHFLG_CVS_IGNORE) {
			int rc = check_filter(&cvs_filter_list, code, name,
					      name_is_dir);
			if (rc)
				return rc;
			continue;
		}
		if (rule_matches(name, ent, name_is_dir)) {
			report_filter_result(code, name, ent, name_is_dir,
					     listp->debug_type);
			return ent->match_flags & MATCHFLG_INCLUDE ? 1 : -1;
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

/* Get the next include/exclude arg from the string.  The token will not
 * be '\0' terminated, so use the returned length to limit the string.
 * Also, be sure to add this length to the returned pointer before passing
 * it back to ask for the next token.  This routine parses the "!" (list-
 * clearing) token and (depending on the mflags) the various prefixes.
 * The *mflags_ptr value will be set on exit to the new MATCHFLG_* bits
 * for the current token. */
static const char *parse_rule_tok(const char *p, uint32 mflags, int xflags,
				  unsigned int *len_ptr, uint32 *mflags_ptr)
{
	const uchar *s = (const uchar *)p;
	uint32 new_mflags;
	unsigned int len;

	if (mflags & MATCHFLG_WORD_SPLIT) {
		/* Skip over any initial whitespace. */
		while (isspace(*s))
			s++;
		/* Update to point to real start of rule. */
		p = (const char *)s;
	}
	if (!*s)
		return NULL;

	new_mflags = mflags & MATCHFLGS_FROM_CONTAINER;

	/* Figure out what kind of a filter rule "s" is pointing at.  Note
	 * that if MATCHFLG_NO_PREFIXES is set, the rule is either an include
	 * or an exclude based on the inheritance of the MATCHFLG_INCLUDE
	 * flag (above).  XFLG_OLD_PREFIXES indicates a compatibility mode
	 * for old include/exclude patterns where just "+ " and "- " are
	 * allowed as optional prefixes.  */
	if (mflags & MATCHFLG_NO_PREFIXES) {
		if (*s == '!' && mflags & MATCHFLG_CVS_IGNORE)
			new_mflags |= MATCHFLG_CLEAR_LIST; /* Tentative! */
	} else if (xflags & XFLG_OLD_PREFIXES) {
		if (*s == '-' && s[1] == ' ') {
			new_mflags &= ~MATCHFLG_INCLUDE;
			s += 2;
		} else if (*s == '+' && s[1] == ' ') {
			new_mflags |= MATCHFLG_INCLUDE;
			s += 2;
		} else if (*s == '!')
			new_mflags |= MATCHFLG_CLEAR_LIST; /* Tentative! */
	} else {
		char ch = 0, *mods = "";
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
			new_mflags |= MATCHFLG_PERDIR_MERGE
				    | MATCHFLG_FINISH_SETUP;
			/* FALL THROUGH */
		case '.':
			new_mflags |= MATCHFLG_MERGE_FILE;
			mods = MODIFIERS_INCL_EXCL MODIFIERS_MERGE_FILE;
			break;
		case '+':
			new_mflags |= MATCHFLG_INCLUDE;
			/* FALL THROUGH */
		case '-':
			mods = MODIFIERS_INCL_EXCL;
			break;
		case 'S':
			new_mflags |= MATCHFLG_INCLUDE;
			/* FALL THROUGH */
		case 'H':
			new_mflags |= MATCHFLG_SENDER_SIDE;
			mods = MODIFIERS_HIDE_PROTECT;
			break;
		case 'R':
			new_mflags |= MATCHFLG_INCLUDE;
			/* FALL THROUGH */
		case 'P':
			new_mflags |= MATCHFLG_RECEIVER_SIDE;
			mods = MODIFIERS_HIDE_PROTECT;
			break;
		case '!':
			new_mflags |= MATCHFLG_CLEAR_LIST;
			mods = NULL;
			break;
		default:
			rprintf(FERROR, "Unknown filter rule: `%s'\n", p);
			exit_cleanup(RERR_SYNTAX);
		}
		while (mods && *++s && *s != ' ' && *s != '_') {
			if (strchr(mods, *s) == NULL) {
				if (mflags & MATCHFLG_WORD_SPLIT && isspace(*s)) {
					s--;
					break;
				}
			    invalid:
				rprintf(FERROR,
					"invalid modifier sequence at '%c' in filter rule: %s\n",
					*s, p);
				exit_cleanup(RERR_SYNTAX);
			}
			switch (*s) {
			case '-':
				if (new_mflags & MATCHFLG_NO_PREFIXES)
				    goto invalid;
				new_mflags |= MATCHFLG_NO_PREFIXES;
				break;
			case '+':
				if (new_mflags & MATCHFLG_NO_PREFIXES)
				    goto invalid;
				new_mflags |= MATCHFLG_NO_PREFIXES
					    | MATCHFLG_INCLUDE;
				break;
			case '/':
				new_mflags |= MATCHFLG_ABS_PATH;
				break;
			case '!':
				new_mflags |= MATCHFLG_NEGATE;
				break;
			case 'C':
				if (new_mflags & MATCHFLG_NO_PREFIXES)
				    goto invalid;
				new_mflags |= MATCHFLG_NO_PREFIXES
					    | MATCHFLG_WORD_SPLIT
					    | MATCHFLG_NO_INHERIT
					    | MATCHFLG_CVS_IGNORE;
				break;
			case 'e':
				new_mflags |= MATCHFLG_EXCLUDE_SELF;
				break;
			case 'n':
				new_mflags |= MATCHFLG_NO_INHERIT;
				break;
			case 'p':
				new_mflags |= MATCHFLG_PERISHABLE;
				break;
			case 'r':
				new_mflags |= MATCHFLG_RECEIVER_SIDE;
				break;
			case 's':
				new_mflags |= MATCHFLG_SENDER_SIDE;
				break;
			case 'w':
				new_mflags |= MATCHFLG_WORD_SPLIT;
				break;
			}
		}
		if (*s)
			s++;
	}

	if (mflags & MATCHFLG_WORD_SPLIT) {
		const uchar *cp = s;
		/* Token ends at whitespace or the end of the string. */
		while (!isspace(*cp) && *cp != '\0')
			cp++;
		len = cp - s;
	} else
		len = strlen((char*)s);

	if (new_mflags & MATCHFLG_CLEAR_LIST) {
		if (!(mflags & MATCHFLG_NO_PREFIXES)
		 && !(xflags & XFLG_OLD_PREFIXES) && len) {
			rprintf(FERROR,
				"'!' rule has trailing characters: %s\n", p);
			exit_cleanup(RERR_SYNTAX);
		}
		if (len > 1)
			new_mflags &= ~MATCHFLG_CLEAR_LIST;
	} else if (!len && !(new_mflags & MATCHFLG_CVS_IGNORE)) {
		rprintf(FERROR, "unexpected end of filter rule: %s\n", p);
		exit_cleanup(RERR_SYNTAX);
	}

	/* --delete-excluded turns an un-modified include/exclude into a
	 * sender-side rule.  We also affect per-dir merge files that take
	 * no prefixes as a simple optimization. */
	if (delete_excluded
	 && !(new_mflags & (MATCHFLG_RECEIVER_SIDE|MATCHFLG_SENDER_SIDE))
	 && (!(new_mflags & MATCHFLG_PERDIR_MERGE)
	  || new_mflags & MATCHFLG_NO_PREFIXES))
		new_mflags |= MATCHFLG_SENDER_SIDE;

	*len_ptr = len;
	*mflags_ptr = new_mflags;
	return (const char *)s;
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

static void get_cvs_excludes(uint32 mflags)
{
	static int initialized = 0;
	char *p, fname[MAXPATHLEN];

	if (initialized)
		return;
	initialized = 1;

	parse_rule(&cvs_filter_list, default_cvsignore,
		   mflags | (protocol_version >= 30 ? MATCHFLG_PERISHABLE : 0),
		   0);

	p = module_id >= 0 && lp_use_chroot(module_id) ? "/" : getenv("HOME");
	if (p && pathjoin(fname, MAXPATHLEN, p, ".cvsignore") < MAXPATHLEN)
		parse_filter_file(&cvs_filter_list, fname, mflags, 0);

	parse_rule(&cvs_filter_list, getenv("CVSIGNORE"), mflags, 0);
}


void parse_rule(struct filter_list_struct *listp, const char *pattern,
		uint32 mflags, int xflags)
{
	unsigned int pat_len;
	uint32 new_mflags;
	const char *cp, *p;

	if (!pattern)
		return;

	while (1) {
		/* Remember that the returned string is NOT '\0' terminated! */
		cp = parse_rule_tok(pattern, mflags, xflags,
				    &pat_len, &new_mflags);
		if (!cp)
			break;

		pattern = cp + pat_len;

		if (pat_len >= MAXPATHLEN) {
			rprintf(FERROR, "discarding over-long filter: %.*s\n",
				(int)pat_len, cp);
			continue;
		}

		if (new_mflags & MATCHFLG_CLEAR_LIST) {
			if (verbose > 2) {
				rprintf(FINFO,
					"[%s] clearing filter list%s\n",
					who_am_i(), listp->debug_type);
			}
			clear_filter_list(listp);
			continue;
		}

		if (new_mflags & MATCHFLG_MERGE_FILE) {
			unsigned int len;
			if (!pat_len) {
				cp = ".cvsignore";
				pat_len = 10;
			}
			len = pat_len;
			if (new_mflags & MATCHFLG_EXCLUDE_SELF) {
				const char *name = cp + len;
				while (name > cp && name[-1] != '/') name--;
				len -= name - cp;
				add_rule(listp, name, len, 0, 0);
				new_mflags &= ~MATCHFLG_EXCLUDE_SELF;
				len = pat_len;
			}
			if (new_mflags & MATCHFLG_PERDIR_MERGE) {
				if (parent_dirscan) {
					if (!(p = parse_merge_name(cp, &len,
								module_dirlen)))
						continue;
					add_rule(listp, p, len, new_mflags, 0);
					continue;
				}
			} else {
				if (!(p = parse_merge_name(cp, &len, 0)))
					continue;
				parse_filter_file(listp, p, new_mflags,
						  XFLG_FATAL_ERRORS);
				continue;
			}
		}

		add_rule(listp, cp, pat_len, new_mflags, xflags);

		if (new_mflags & MATCHFLG_CVS_IGNORE
		    && !(new_mflags & MATCHFLG_MERGE_FILE))
			get_cvs_excludes(new_mflags);
	}
}


void parse_filter_file(struct filter_list_struct *listp, const char *fname,
		       uint32 mflags, int xflags)
{
	FILE *fp;
	char line[BIGPATHBUFLEN];
	char *eob = line + sizeof line - 1;
	int word_split = mflags & MATCHFLG_WORD_SPLIT;

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

	if (verbose > 2) {
		rprintf(FINFO, "[%s] parse_filter_file(%s,%x,%x)%s\n",
			who_am_i(), fname, mflags, xflags,
			fp ? "" : " [not found]");
	}

	if (!fp) {
		if (xflags & XFLG_FATAL_ERRORS) {
			rsyserr(FERROR, errno,
				"failed to open %sclude file %s",
				mflags & MATCHFLG_INCLUDE ? "in" : "ex",
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
			parse_rule(listp, line, mflags, xflags);
		if (ch == EOF)
			break;
	}
	fclose(fp);
}

/* If the "for_xfer" flag is set, the prefix is made compatible with the
 * current protocol_version (if possible) or a NULL is returned (if not
 * possible). */
char *get_rule_prefix(int match_flags, const char *pat, int for_xfer,
		      unsigned int *plen_ptr)
{
	static char buf[MAX_RULE_PREFIX+1];
	char *op = buf;
	int legal_len = for_xfer && protocol_version < 29 ? 1 : MAX_RULE_PREFIX-1;

	if (match_flags & MATCHFLG_PERDIR_MERGE) {
		if (legal_len == 1)
			return NULL;
		*op++ = ':';
	} else if (match_flags & MATCHFLG_INCLUDE)
		*op++ = '+';
	else if (legal_len != 1
	    || ((*pat == '-' || *pat == '+') && pat[1] == ' '))
		*op++ = '-';
	else
		legal_len = 0;

	if (match_flags & MATCHFLG_ABS_PATH)
		*op++ = '/';
	if (match_flags & MATCHFLG_NEGATE)
		*op++ = '!';
	if (match_flags & MATCHFLG_CVS_IGNORE)
		*op++ = 'C';
	else {
		if (match_flags & MATCHFLG_NO_INHERIT)
			*op++ = 'n';
		if (match_flags & MATCHFLG_WORD_SPLIT)
			*op++ = 'w';
		if (match_flags & MATCHFLG_NO_PREFIXES) {
			if (match_flags & MATCHFLG_INCLUDE)
				*op++ = '+';
			else
				*op++ = '-';
		}
	}
	if (match_flags & MATCHFLG_EXCLUDE_SELF)
		*op++ = 'e';
	if (match_flags & MATCHFLG_SENDER_SIDE
	    && (!for_xfer || protocol_version >= 29))
		*op++ = 's';
	if (match_flags & MATCHFLG_RECEIVER_SIDE
	    && (!for_xfer || protocol_version >= 29
	     || (delete_excluded && am_sender)))
		*op++ = 'r';
	if (match_flags & MATCHFLG_PERISHABLE) {
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

static void send_rules(int f_out, struct filter_list_struct *flp)
{
	struct filter_struct *ent, *prev = NULL;

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
		if (ent->match_flags & MATCHFLG_SENDER_SIDE)
			elide = am_sender ? 1 : -1;
		if (ent->match_flags & MATCHFLG_RECEIVER_SIDE)
			elide = elide ? 0 : am_sender ? -1 : 1;
		else if (delete_excluded && !elide
		 && (!(ent->match_flags & MATCHFLG_PERDIR_MERGE)
		  || ent->match_flags & MATCHFLG_NO_PREFIXES))
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
		if (ent->match_flags & MATCHFLG_CVS_IGNORE
		    && !(ent->match_flags & MATCHFLG_MERGE_FILE)) {
			int f = am_sender || protocol_version < 29 ? f_out : -2;
			send_rules(f, &cvs_filter_list);
			if (f == f_out)
				continue;
		}
		p = get_rule_prefix(ent->match_flags, ent->pattern, 1, &plen);
		if (!p) {
			rprintf(FERROR,
				"filter rules are too modern for remote rsync.\n");
			exit_cleanup(RERR_PROTOCOL);
		}
		if (f_out < 0)
			continue;
		len = strlen(ent->pattern);
		dlen = ent->match_flags & MATCHFLG_DIRECTORY ? 1 : 0;
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
			parse_rule(&filter_list, ":C", 0, 0);
		parse_rule(&filter_list, "-C", 0, 0);
	}

	send_rules(f_out, &filter_list);

	if (f_out >= 0)
		write_int(f_out, 0);

	if (cvs_exclude) {
		if (!am_sender || protocol_version < 29)
			parse_rule(&filter_list, ":C", 0, 0);
		if (!am_sender)
			parse_rule(&filter_list, "-C", 0, 0);
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
			parse_rule(&filter_list, line, 0, xflags);
		}
	}

	if (cvs_exclude) {
		if (local_server || am_sender || protocol_version < 29)
			parse_rule(&filter_list, ":C", 0, 0);
		if (local_server || am_sender)
			parse_rule(&filter_list, "-C", 0, 0);
	}

	if (local_server) /* filter out any rules that aren't for us. */
		send_rules(-1, &filter_list);
}
