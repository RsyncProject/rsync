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

extern char curr_dir[];

struct exclude_list_struct exclude_list = { 0, 0, "" };
struct exclude_list_struct local_exclude_list = { 0, 0, "per-dir .cvsignore " };
struct exclude_list_struct server_exclude_list = { 0, 0, "server " };
char *exclude_path_prefix = NULL;

/** Build an exclude structure given an exclude pattern. */
static void make_exclude(struct exclude_list_struct *listp, const char *pat,
			 unsigned int pat_len, unsigned int mflags)
{
	struct exclude_struct *ret;
	const char *cp;
	unsigned int ex_len;

	ret = new(struct exclude_struct);
	if (!ret)
		out_of_memory("make_exclude");

	memset(ret, 0, sizeof ret[0]);

	if (exclude_path_prefix)
		mflags |= MATCHFLG_ABS_PATH;
	if (exclude_path_prefix && *pat == '/')
		ex_len = strlen(exclude_path_prefix);
	else
		ex_len = 0;
	ret->pattern = new_array(char, ex_len + pat_len + 1);
	if (!ret->pattern)
		out_of_memory("make_exclude");
	if (ex_len)
		memcpy(ret->pattern, exclude_path_prefix, ex_len);
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

	for (cp = ret->pattern; (cp = strchr(cp, '/')) != NULL; cp++)
		ret->slash_cnt++;

	ret->match_flags = mflags;

	if (!listp->tail)
		listp->head = listp->tail = ret;
	else {
		listp->tail->next = ret;
		listp->tail = ret;
	}
}

static void free_exclude(struct exclude_struct *ex)
{
	free(ex->pattern);
	free(ex);
}

void clear_exclude_list(struct exclude_list_struct *listp)
{
	struct exclude_struct *ent, *next;

	for (ent = listp->head; ent; ent = next) {
		next = ent->next;
		free_exclude(ent);
	}

	listp->head = listp->tail = NULL;
}

static int check_one_exclude(char *name, struct exclude_struct *ex,
                             int name_is_dir)
{
	char *p, full_name[MAXPATHLEN];
	int match_start = 0;
	char *pattern = ex->pattern;

	if (!*name)
		return 0;

	/* If the pattern does not have any slashes AND it does not have
	 * a "**" (which could match a slash), then we just match the
	 * name portion of the path. */
	if (!ex->slash_cnt && !(ex->match_flags & MATCHFLG_WILD2)) {
		if ((p = strrchr(name,'/')) != NULL)
			name = p+1;
	}
	else if (ex->match_flags & MATCHFLG_ABS_PATH && *name != '/'
	    && curr_dir[1]) {
		pathjoin(full_name, sizeof full_name, curr_dir + 1, name);
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
		if (!match_start && ex->slash_cnt
		    && !(ex->match_flags & MATCHFLG_WILD2)) {
			int cnt = ex->slash_cnt + 1;
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


static void report_exclude_result(char const *name,
                                  struct exclude_struct const *ent,
                                  int name_is_dir, const char *type)
{
	/* If a trailing slash is present to match only directories,
	 * then it is stripped out by make_exclude.  So as a special
	 * case we add it back in here. */

	if (verbose >= 2) {
		rprintf(FINFO, "[%s] %scluding %s %s because of %spattern %s%s\n",
			who_am_i(),
			ent->match_flags & MATCHFLG_INCLUDE ? "in" : "ex",
			name_is_dir ? "directory" : "file", name, type,
			ent->pattern,
			ent->match_flags & MATCHFLG_DIRECTORY ? "/" : "");
	}
}


/*
 * Return -1 if file "name" is defined to be excluded by the specified
 * exclude list, 1 if it is included, and 0 if it was not matched.
 */
int check_exclude(struct exclude_list_struct *listp, char *name, int name_is_dir)
{
	struct exclude_struct *ent;

	for (ent = listp->head; ent; ent = ent->next) {
		if (check_one_exclude(name, ent, name_is_dir)) {
			report_exclude_result(name, ent, name_is_dir,
					      listp->debug_type);
			return ent->match_flags & MATCHFLG_INCLUDE ? 1 : -1;
		}
	}

	return 0;
}


/* Get the next include/exclude arg from the string.  The token will not
 * be '\0' terminated, so use the returned length to limit the string.
 * Also, be sure to add this length to the returned pointer before passing
 * it back to ask for the next token.  This routine parses the +/- prefixes
 * and the "!" token unless xflags contains XFLG_WORDS_ONLY.  The *flag_ptr
 * value will also be set to the MATCHFLG_* bits for the current token.
 */
static const char *get_exclude_tok(const char *p, unsigned int *len_ptr,
				   unsigned int *flag_ptr, int xflags)
{
	const unsigned char *s = (const unsigned char *)p;
	unsigned int len, mflags = 0;

	if (xflags & XFLG_WORD_SPLIT) {
		/* Skip over any initial whitespace. */
		while (isspace(*s))
			s++;
		/* Update for "!" check. */
		p = (const char *)s;
	}

	/* Is this a '+' or '-' followed by a space (not whitespace)? */
	if (!(xflags & XFLG_WORDS_ONLY)
	    && (*s == '-' || *s == '+') && s[1] == ' ') {
		if (*s == '+')
			mflags |= MATCHFLG_INCLUDE;
		s += 2;
	} else if (xflags & XFLG_DEF_INCLUDE)
		mflags |= MATCHFLG_INCLUDE;
	if (xflags & XFLG_DIRECTORY)
		mflags |= MATCHFLG_DIRECTORY;

	if (xflags & XFLG_WORD_SPLIT) {
		const unsigned char *cp = s;
		/* Token ends at whitespace or the end of the string. */
		while (!isspace(*cp) && *cp != '\0')
			cp++;
		len = cp - s;
	} else
		len = strlen(s);

	if (*p == '!' && len == 1 && !(xflags & XFLG_WORDS_ONLY))
		mflags |= MATCHFLG_CLEAR_LIST;

	*len_ptr = len;
	*flag_ptr = mflags;
	return (const char *)s;
}


void add_exclude(struct exclude_list_struct *listp, const char *pattern,
		 int xflags)
{
	unsigned int pat_len, mflags;
	const char *cp;

	if (!pattern)
		return;

	cp = pattern;
	pat_len = 0;
	while (1) {
		cp = get_exclude_tok(cp + pat_len, &pat_len, &mflags, xflags);
		if (!pat_len)
			break;

		if (mflags & MATCHFLG_CLEAR_LIST) {
			if (verbose > 2) {
				rprintf(FINFO,
					"[%s] clearing %sexclude list\n",
					who_am_i(), listp->debug_type);
			}
			clear_exclude_list(listp);
			continue;
		}

		make_exclude(listp, cp, pat_len, mflags);

		if (verbose > 2) {
			rprintf(FINFO, "[%s] add_exclude(%.*s, %s%sclude)\n",
				who_am_i(), (int)pat_len, cp, listp->debug_type,
				mflags & MATCHFLG_INCLUDE ? "in" : "ex");
		}
	}
}


void add_exclude_file(struct exclude_list_struct *listp, const char *fname,
		      int xflags)
{
	FILE *fp;
	char line[MAXPATHLEN+3]; /* Room for "x " prefix and trailing slash. */
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
				"failed to open %s file %s",
				xflags & XFLG_DEF_INCLUDE ? "include" : "exclude",
				fname);
			exit_cleanup(RERR_FILEIO);
		}
		return;
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
			rprintf(FERROR, "discarding over-long exclude: %s...\n", line);
			s = line;
		}
		*s = '\0';
		/* Skip an empty token and (when line parsing) comments. */
		if (*line && (word_split || (*line != ';' && *line != '#')))
			add_exclude(listp, line, xflags);
		if (ch == EOF)
			break;
	}
	fclose(fp);
}


void send_exclude_list(int f)
{
	struct exclude_struct *ent;

	/* This is a complete hack - blame Rusty.
	 *
	 * FIXME: This pattern shows up in the output of
	 * report_exclude_result(), which is not ideal. */
	if (list_only && !recurse)
		add_exclude(&exclude_list, "/*/*", 0);

	for (ent = exclude_list.head; ent; ent = ent->next) {
		unsigned int l;
		char p[MAXPATHLEN+1];

		l = strlcpy(p, ent->pattern, sizeof p);
		if (l == 0 || l >= MAXPATHLEN)
			continue;
		if (ent->match_flags & MATCHFLG_DIRECTORY) {
			p[l++] = '/';
			p[l] = '\0';
		}

		if (ent->match_flags & MATCHFLG_INCLUDE) {
			write_int(f, l + 2);
			write_buf(f, "+ ", 2);
		} else if ((*p == '-' || *p == '+') && p[1] == ' ') {
			write_int(f, l + 2);
			write_buf(f, "- ", 2);
		} else
			write_int(f, l);
		write_buf(f, p, l);
	}

	write_int(f, 0);
}


void recv_exclude_list(int f)
{
	char line[MAXPATHLEN+3]; /* Room for "x " prefix and trailing slash. */
	unsigned int l;

	while ((l = read_int(f)) != 0) {
		if (l >= sizeof line)
			overflow("recv_exclude_list");
		read_sbuf(f, line, l);
		add_exclude(&exclude_list, line, 0);
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
	char fname[MAXPATHLEN];
	char *p;

	add_exclude(&exclude_list, default_cvsignore,
		    XFLG_WORD_SPLIT | XFLG_WORDS_ONLY);

	if ((p = getenv("HOME"))
	    && pathjoin(fname, sizeof fname, p, ".cvsignore") < sizeof fname) {
		add_exclude_file(&exclude_list, fname,
				 XFLG_WORD_SPLIT | XFLG_WORDS_ONLY);
	}

	add_exclude(&exclude_list, getenv("CVSIGNORE"),
		    XFLG_WORD_SPLIT | XFLG_WORDS_ONLY);
}
