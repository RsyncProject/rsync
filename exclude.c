/* -*- c-file-style: "linux" -*-
 *
 * Copyright (C) 1996-2001 by Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 1996 by Paul Mackerras
 * Copyright (C) 2002 by Martin Pool
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
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

/* a lot of this stuff was originally derived from GNU tar, although
   it has now changed so much that it is hard to tell :) */

/* include/exclude cluestick added by Martin Pool <mbp@samba.org> */

#include "rsync.h"

extern int verbose;
extern int eol_nulls;
extern int list_only;
extern int recurse;
extern int io_error;
extern int sanitize_paths;
extern int protocol_version;

extern char curr_dir[];
extern unsigned int curr_dir_len;
extern unsigned int module_dirlen;

struct filter_list_struct filter_list = { 0, 0, "" };
struct filter_list_struct server_filter_list = { 0, 0, "server " };

/* Need room enough for ":MODS " prefix plus some room to grow. */
#define MAX_RULE_PREFIX (16)

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
static void make_filter(struct filter_list_struct *listp, const char *pat,
			unsigned int pat_len, unsigned int mflags)
{
	struct filter_struct *ret;
	const char *cp;
	unsigned int ex_len;

	if (verbose > 2) {
		rprintf(FINFO, "[%s] make_filter(%.*s, %s%s)\n",
			who_am_i(), (int)pat_len, pat,
			mflags & MATCHFLG_PERDIR_MERGE ? "per-dir-merge"
			: mflags & MATCHFLG_INCLUDE ? "include" : "exclude",
			listp->debug_type);
	}

	ret = new(struct filter_struct);
	if (!ret)
		out_of_memory("make_filter");

	memset(ret, 0, sizeof ret[0]);

	if (mflags & MATCHFLG_ABS_PATH) {
		if (*pat != '/') {
			mflags &= ~MATCHFLG_ABS_PATH;
			ex_len = 0;
		} else
			ex_len = dirbuf_len - module_dirlen - 1;
	} else
		ex_len = 0;
	ret->pattern = new_array(char, ex_len + pat_len + 1);
	if (!ret->pattern)
		out_of_memory("make_filter");
	if (ex_len)
		memcpy(ret->pattern, dirbuf + module_dirlen, ex_len);
	strlcpy(ret->pattern + ex_len, pat, pat_len + 1);
	pat_len += ex_len;

	if (strpbrk(ret->pattern, "*[?")) {
		mflags |= MATCHFLG_WILD;
		if ((cp = strstr(ret->pattern, "**")) != NULL) {
			mflags |= MATCHFLG_WILD2;
			/* If the pattern starts with **, note that. */
			if (cp == ret->pattern)
				mflags |= MATCHFLG_WILD2_PREFIX;
		}
	}

	if (pat_len > 1 && ret->pattern[pat_len-1] == '/') {
		ret->pattern[pat_len-1] = 0;
		mflags |= MATCHFLG_DIRECTORY;
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
			out_of_memory("make_filter");
		lp->head = lp->tail = NULL;
		if (asprintf(&lp->debug_type, " (per-dir %s)", cp) < 0)
			out_of_memory("make_filter");
		ret->u.mergelist = lp;

		if (mergelist_cnt == mergelist_size) {
			mergelist_size += 5;
			mergelist_parents = realloc_array(mergelist_parents,
						struct filter_struct *,
						mergelist_size);
			if (!mergelist_parents)
				out_of_memory("make_filter");
		}
		mergelist_parents[mergelist_cnt++] = ret;
	} else {
		for (cp = ret->pattern; (cp = strchr(cp, '/')) != NULL; cp++)
			ret->u.slash_cnt++;
	}

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
		if (!sanitize_path(fn, merge_file, r, dirbuf_depth)) {
			rprintf(FERROR, "merge-file name overflows: %s\n",
				merge_file);
			return NULL;
		}
	} else {
		strlcpy(fn, merge_file, len_ptr ? *len_ptr + 1 : MAXPATHLEN);
		clean_fname(fn, 1);
	}
	
	fn_len = strlen(fn);
	if (fn == buf)
		goto done;

	if (dirbuf_len + fn_len >= MAXPATHLEN) {
		rprintf(FERROR, "merge-file name overflows: %s\n", fn);
		return NULL;
	}
	memcpy(buf, dirbuf + prefix_skip, dirbuf_len - prefix_skip);
	memcpy(buf + dirbuf_len - prefix_skip, fn, fn_len + 1);
	fn_len = clean_fname(buf, 1);

    done:
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
	dirbuf_len = clean_fname(dirbuf, 1);
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
			     struct filter_list_struct *lp, int flags)
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

	len = clean_fname(buf, 1);
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
		add_filter_file(lp, buf, flags | XFLG_ABS_PATH);
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

	/* Note: add_filter_file() might increase mergelist_cnt, so keep
	 * this loop separate from the above loop. */
	for (i = 0; i < mergelist_cnt; i++) {
		struct filter_struct *ex = mergelist_parents[i];
		struct filter_list_struct *lp = ex->u.mergelist;
		int flags = 0;

		if (verbose > 2) {
			rprintf(FINFO, "[%s] pushing filter list%s\n",
				who_am_i(), lp->debug_type);
		}

		lp->tail = NULL; /* Switch any local rules to inherited. */
		if (ex->match_flags & MATCHFLG_NO_INHERIT)
			lp->head = NULL;
		if (ex->match_flags & MATCHFLG_WORD_SPLIT)
			flags |= XFLG_WORD_SPLIT;
		if (ex->match_flags & MATCHFLG_NO_PREFIXES)
			flags |= XFLG_NO_PREFIXES;
		if (ex->match_flags & MATCHFLG_INCLUDE)
			flags |= XFLG_DEF_INCLUDE;
		else if (ex->match_flags & MATCHFLG_NO_PREFIXES)
			flags |= XFLG_DEF_EXCLUDE;

		if (ex->match_flags & MATCHFLG_FINISH_SETUP) {
			ex->match_flags &= ~MATCHFLG_FINISH_SETUP;
			if (setup_merge_file(ex, lp, flags))
				set_filter_dir(dir, dirlen);
		}

		if (strlcpy(dirbuf + dirbuf_len, ex->pattern,
		    MAXPATHLEN - dirbuf_len) < MAXPATHLEN - dirbuf_len)
			add_filter_file(lp, dirbuf, flags | XFLG_ABS_PATH);
		else {
			io_error |= IOERR_GENERAL;
			rprintf(FINFO,
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

static int rule_matches(char *name, struct filter_struct *ex, int name_is_dir)
{
	char *p, full_name[MAXPATHLEN];
	int match_start = 0;
	char *pattern = ex->pattern;

	if (!*name)
		return 0;

	/* If the pattern does not have any slashes AND it does not have
	 * a "**" (which could match a slash), then we just match the
	 * name portion of the path. */
	if (!ex->u.slash_cnt && !(ex->match_flags & MATCHFLG_WILD2)) {
		if ((p = strrchr(name,'/')) != NULL)
			name = p+1;
	}
	else if (ex->match_flags & MATCHFLG_ABS_PATH && *name != '/'
	    && curr_dir_len > module_dirlen + 1) {
		pathjoin(full_name, sizeof full_name,
			 curr_dir + module_dirlen + 1, name);
		name = full_name;
	}

	if (ex->match_flags & MATCHFLG_DIRECTORY && !name_is_dir)
		return 0;

	if (*pattern == '/') {
		match_start = 1;
		pattern++;
		if (*name == '/')
			name++;
	}

	if (ex->match_flags & MATCHFLG_WILD) {
		/* A non-anchored match with an infix slash and no "**"
		 * needs to match the last slash_cnt+1 name elements. */
		if (!match_start && ex->u.slash_cnt
		    && !(ex->match_flags & MATCHFLG_WILD2)) {
			int cnt = ex->u.slash_cnt + 1;
			for (p = name + strlen(name) - 1; p >= name; p--) {
				if (*p == '/' && !--cnt)
					break;
			}
			name = p+1;
		}
		if (wildmatch(pattern, name))
			return 1;
		if (ex->match_flags & MATCHFLG_WILD2_PREFIX) {
			/* If the **-prefixed pattern has a '/' as the next
			 * character, then try to match the rest of the
			 * pattern at the root. */
			if (pattern[2] == '/' && wildmatch(pattern+3, name))
				return 1;
		}
		else if (!match_start && ex->match_flags & MATCHFLG_WILD2) {
			/* A non-anchored match with an infix or trailing "**"
			 * (but not a prefixed "**") needs to try matching
			 * after every slash. */
			while ((name = strchr(name, '/')) != NULL) {
				name++;
				if (wildmatch(pattern, name))
					return 1;
			}
		}
	} else if (match_start) {
		if (strcmp(name,pattern) == 0)
			return 1;
	} else {
		int l1 = strlen(name);
		int l2 = strlen(pattern);
		if (l2 <= l1 &&
		    strcmp(name+(l1-l2),pattern) == 0 &&
		    (l1==l2 || name[l1-(l2+1)] == '/')) {
			return 1;
		}
	}

	return 0;
}


static void report_filter_result(char const *name,
                                 struct filter_struct const *ent,
                                 int name_is_dir, const char *type)
{
	/* If a trailing slash is present to match only directories,
	 * then it is stripped out by make_filter.  So as a special
	 * case we add it back in here. */

	if (verbose >= 2) {
		rprintf(FINFO, "[%s] %scluding %s %s because of pattern %s%s%s\n",
			who_am_i(),
			ent->match_flags & MATCHFLG_INCLUDE ? "in" : "ex",
			name_is_dir ? "directory" : "file", name, ent->pattern,
			ent->match_flags & MATCHFLG_DIRECTORY ? "/" : "", type);
	}
}


/*
 * Return -1 if file "name" is defined to be excluded by the specified
 * exclude list, 1 if it is included, and 0 if it was not matched.
 */
int check_filter(struct filter_list_struct *listp, char *name, int name_is_dir)
{
	struct filter_struct *ent;

	for (ent = listp->head; ent; ent = ent->next) {
		if (ent->match_flags & MATCHFLG_PERDIR_MERGE) {
			int rc = check_filter(ent->u.mergelist, name,
					      name_is_dir);
			if (rc)
				return rc;
			continue;
		}
		if (rule_matches(name, ent, name_is_dir)) {
			report_filter_result(name, ent, name_is_dir,
					      listp->debug_type);
			return ent->match_flags & MATCHFLG_INCLUDE ? 1 : -1;
		}
	}

	return 0;
}


/* Get the next include/exclude arg from the string.  The token will not
 * be '\0' terminated, so use the returned length to limit the string.
 * Also, be sure to add this length to the returned pointer before passing
 * it back to ask for the next token.  This routine parses the "!" (list-
 * clearing) token and (if xflags does NOT contain XFLG_NO_PREFIXES) the
 * +/- prefixes for overriding the include/exclude mode.  The *flag_ptr
 * value will also be set to the MATCHFLG_* bits for the current token.
 */
static const char *get_filter_tok(const char *p, int xflags,
				unsigned int *len_ptr, unsigned int *flag_ptr)
{
	const unsigned char *s = (const unsigned char *)p;
	unsigned int len, mflags = 0;
	int empty_pat_is_OK = 0;

	if (xflags & XFLG_WORD_SPLIT) {
		/* Skip over any initial whitespace. */
		while (isspace(*s))
			s++;
		/* Update to point to real start of rule. */
		p = (const char *)s;
	}
	if (!*s)
		return NULL;

	/* Figure out what kind of a filter rule "s" is pointing at. */
	if (!(xflags & (XFLG_DEF_INCLUDE | XFLG_DEF_EXCLUDE))) {
		char *mods = "";
		switch (*s) {
		case ':':
			mflags |= MATCHFLG_PERDIR_MERGE
				| MATCHFLG_FINISH_SETUP;
			/* FALL THROUGH */
		case '.':
			mflags |= MATCHFLG_MERGE_FILE;
			mods = "-+Cens";
			break;
		case '+':
			mflags |= MATCHFLG_INCLUDE;
			break;
		case '-':
			break;
		case '!':
			mflags |= MATCHFLG_CLEAR_LIST;
			mods = NULL;
			break;
		default:
			rprintf(FERROR, "Unknown filter rule: %s\n", p);
			exit_cleanup(RERR_SYNTAX);
		}
		while (mods && *++s && *s != ' ' && *s != '=' && *s != '_') {
			if (strchr(mods, *s) == NULL) {
				if (xflags & XFLG_WORD_SPLIT && isspace(*s)) {
					s--;
					break;
				}
				rprintf(FERROR,
					"unknown option '%c' in filter rule: %s\n",
					*s, p);
				exit_cleanup(RERR_SYNTAX);
			}
			switch (*s) {
			case '-':
				mflags |= MATCHFLG_NO_PREFIXES;
				break;
			case '+':
				mflags |= MATCHFLG_NO_PREFIXES
					| MATCHFLG_INCLUDE;
				break;
			case 'C':
				empty_pat_is_OK = 1;
				mflags |= MATCHFLG_NO_PREFIXES
					| MATCHFLG_WORD_SPLIT
					| MATCHFLG_NO_INHERIT;
				break;
			case 'e':
				mflags |= MATCHFLG_EXCLUDE_SELF;
				break;
			case 'n':
				mflags |= MATCHFLG_NO_INHERIT;
				break;
			case 's':
				mflags |= MATCHFLG_WORD_SPLIT;
				break;
			}
		}
		if (*s)
			s++;
	} else if (!(xflags & XFLG_NO_PREFIXES)
	    && (*s == '-' || *s == '+') && s[1] == ' ') {
		if (*s == '+')
			mflags |= MATCHFLG_INCLUDE;
		s += 2;
	} else {
		if (xflags & XFLG_DEF_INCLUDE)
			mflags |= MATCHFLG_INCLUDE;
		if (*s == '!')
			mflags |= MATCHFLG_CLEAR_LIST; /* Tentative! */
	}

	if (xflags & XFLG_DIRECTORY)
		mflags |= MATCHFLG_DIRECTORY;

	if (xflags & XFLG_WORD_SPLIT) {
		const unsigned char *cp = s;
		/* Token ends at whitespace or the end of the string. */
		while (!isspace(*cp) && *cp != '\0')
			cp++;
		len = cp - s;
	} else
		len = strlen((char*)s);

	if (mflags & MATCHFLG_CLEAR_LIST) {
		if (!(xflags & (XFLG_DEF_INCLUDE | XFLG_DEF_EXCLUDE)) && len) {
			rprintf(FERROR,
				"'!' rule has trailing characters: %s\n", p);
			exit_cleanup(RERR_SYNTAX);
		}
		if (len > 1)
			mflags &= ~MATCHFLG_CLEAR_LIST;
	} else if (!len && !empty_pat_is_OK) {
		rprintf(FERROR, "unexpected end of filter rule: %s\n", p);
		exit_cleanup(RERR_SYNTAX);
	}

	if (xflags & XFLG_ABS_PATH)
		mflags |= MATCHFLG_ABS_PATH;

	*len_ptr = len;
	*flag_ptr = mflags;
	return (const char *)s;
}


void add_filter(struct filter_list_struct *listp, const char *pattern,
		int xflags)
{
	unsigned int pat_len, mflags;
	const char *cp, *p;

	if (!pattern)
		return;

	while (1) {
		/* Remember that the returned string is NOT '\0' terminated! */
		cp = get_filter_tok(pattern, xflags, &pat_len, &mflags);
		if (!cp)
			break;
		if (pat_len >= MAXPATHLEN) {
			rprintf(FERROR, "discarding over-long filter: %s\n",
				cp);
			continue;
		}
		pattern = cp + pat_len;

		if (mflags & MATCHFLG_CLEAR_LIST) {
			if (verbose > 2) {
				rprintf(FINFO,
					"[%s] clearing filter list%s\n",
					who_am_i(), listp->debug_type);
			}
			clear_filter_list(listp);
			continue;
		}

		if (!pat_len) {
			cp = ".cvsignore";
			pat_len = 10;
		}

		if (mflags & MATCHFLG_MERGE_FILE) {
			unsigned int len = pat_len;
			if (mflags & MATCHFLG_EXCLUDE_SELF) {
				const char *name = strrchr(cp, '/');
				if (name)
					len -= ++name - cp;
				else
					name = cp;
				make_filter(listp, name, len, 0);
				mflags &= ~MATCHFLG_EXCLUDE_SELF;
				len = pat_len;
			}
			if (mflags & MATCHFLG_PERDIR_MERGE) {
				if (parent_dirscan) {
					if (!(p = parse_merge_name(cp, &len, module_dirlen)))
						continue;
					make_filter(listp, p, len, mflags);
					continue;
				}
			} else {
				int flgs = XFLG_FATAL_ERRORS;
				if (!(p = parse_merge_name(cp, &len, 0)))
					continue;
				if (mflags & MATCHFLG_INCLUDE)
					flgs |= XFLG_DEF_INCLUDE;
				else if (mflags & MATCHFLG_NO_PREFIXES)
					flgs |= XFLG_DEF_EXCLUDE;
				add_filter_file(listp, p, flgs);
				continue;
			}
		}

		make_filter(listp, cp, pat_len, mflags);
	}
}


void add_filter_file(struct filter_list_struct *listp, const char *fname,
		     int xflags)
{
	FILE *fp;
	char line[MAXPATHLEN+MAX_RULE_PREFIX+1]; /* +1 for trailing slash. */
	char *eob = line + sizeof line - 1;
	int word_split = xflags & XFLG_WORD_SPLIT;

	if (!fname || !*fname)
		return;

	if (*fname != '-' || fname[1])
		fp = fopen(fname, "rb");
	else
		fp = stdin;
	if (!fp) {
		if (xflags & XFLG_FATAL_ERRORS) {
			rsyserr(FERROR, errno,
				"failed to open %sclude file %s",
				xflags & XFLG_DEF_INCLUDE ? "in" : "ex",
				safe_fname(fname));
			exit_cleanup(RERR_FILEIO);
		}
		return;
	}
	dirbuf[dirbuf_len] = '\0';

	if (verbose > 2) {
		rprintf(FINFO, "[%s] add_filter_file(%s,%d)\n",
			who_am_i(), safe_fname(fname), xflags);
	}

	while (1) {
		char *s = line;
		int ch, overflow = 0;
		while (1) {
			if ((ch = getc(fp)) == EOF) {
				if (ferror(fp) && errno == EINTR)
					continue;
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
			add_filter(listp, line, xflags);
		if (ch == EOF)
			break;
	}
	fclose(fp);
}

char *get_rule_prefix(int match_flags, const char *pat, unsigned int *plen_ptr)
{
	static char buf[MAX_RULE_PREFIX+1];
	char *op = buf;

	if (match_flags & MATCHFLG_PERDIR_MERGE) {
		*op++ = ':';
		if (match_flags & MATCHFLG_WORD_SPLIT)
			*op++ = 's';
		if (match_flags & MATCHFLG_NO_INHERIT)
			*op++ = 'n';
		if (match_flags & MATCHFLG_EXCLUDE_SELF)
			*op++ = 'e';
		if (match_flags & MATCHFLG_NO_PREFIXES) {
			if (match_flags & MATCHFLG_INCLUDE)
				*op++ = '+';
			else
				*op++ = '-';
		}
		*op++ = ' ';
	} else if (match_flags & MATCHFLG_INCLUDE) {
		*op++ = '+';
		*op++ = ' ';
	} else if (protocol_version >= 29
	    || ((*pat == '-' || *pat == '+') && pat[1] == ' ')) {
		*op++ = '-';
		*op++ = ' ';
	}
	*op = '\0';
	if (plen_ptr)
		*plen_ptr = op - buf;
	if (op - buf > MAX_RULE_PREFIX)
		overflow("get_rule_prefix");
	return buf;
}

void send_filter_list(int f)
{
	struct filter_struct *ent;

	/* This is a complete hack - blame Rusty.  FIXME!
	 * Remove this hack when older rsyncs (below 2.6.4) are gone. */
	if (list_only == 1 && !recurse)
		add_filter(&filter_list, "/*/*", XFLG_DEF_EXCLUDE);

	for (ent = filter_list.head; ent; ent = ent->next) {
		unsigned int len, plen, dlen;
		char *p;

		len = strlen(ent->pattern);
		if (len == 0 || len >= MAXPATHLEN)
			continue;
		p = get_rule_prefix(ent->match_flags, ent->pattern, &plen);
		if (protocol_version < 29 && *p == ':') {
			if (strcmp(p, ":sn- ") == 0
			    && strcmp(ent->pattern, ".cvsignore") == 0)
				continue;
			rprintf(FERROR,
				"remote rsync is too old to understand per-directory merge files.\n");
			exit_cleanup(RERR_SYNTAX);
		}
		dlen = ent->match_flags & MATCHFLG_DIRECTORY ? 1 : 0;
		write_int(f, plen + len + dlen);
		if (plen)
			write_buf(f, p, plen);
		write_buf(f, ent->pattern, len);
		if (dlen)
			write_byte(f, '/');
	}

	write_int(f, 0);
}


void recv_filter_list(int f)
{
	char line[MAXPATHLEN+MAX_RULE_PREFIX+1]; /* +1 for trailing slash. */
	unsigned int xflags = protocol_version >= 29 ? 0 : XFLG_DEF_EXCLUDE;
	unsigned int l;

	while ((l = read_int(f)) != 0) {
		if (l >= sizeof line)
			overflow("recv_filter_list");
		read_sbuf(f, line, l);
		add_filter(&filter_list, line, xflags);
	}
}


static char default_cvsignore[] = 
	/* These default ignored items come from the CVS manual. */
	"RCS SCCS CVS CVS.adm RCSLOG cvslog.* tags TAGS"
	" .make.state .nse_depinfo *~ #* .#* ,* _$* *$"
	" *.old *.bak *.BAK *.orig *.rej .del-*"
	" *.a *.olb *.o *.obj *.so *.exe"
	" *.Z *.elc *.ln core"
	/* The rest we added to suit ourself. */
	" .svn/";

void add_cvs_excludes(void)
{
	static unsigned int cvs_flags = XFLG_WORD_SPLIT | XFLG_NO_PREFIXES
				      | XFLG_DEF_EXCLUDE;
	char fname[MAXPATHLEN];
	char *p;

	add_filter(&filter_list, ":C", 0);
	add_filter(&filter_list, default_cvsignore, cvs_flags);

	if ((p = getenv("HOME"))
	    && pathjoin(fname, sizeof fname, p, ".cvsignore") < sizeof fname) {
		add_filter_file(&filter_list, fname, cvs_flags);
	}

	add_filter(&filter_list, getenv("CVSIGNORE"), cvs_flags);
}
