/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
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

/* a lot of this stuff was originally derived from GNU tar, although
   it has now changed so much that it is hard to tell :) */

#include "rsync.h"

extern int verbose;
extern int delete_mode;

static struct exclude_struct **exclude_list;

/*
 * Optimization for special case when all included files are explicitly
 *   listed without wildcards in the "exclude" list followed by a "- *"
 *   to exclude the rest.
 * Contributed by Dave Dykstra <dwd@bell-labs.com>
 */
static int only_included_files = 1;
static struct exclude_struct *exclude_the_rest;

int send_included_file_names(int f,struct file_list *flist)
{
	struct exclude_struct *ex, **ex_list;
	int n;
	char *p;

	if (!only_included_files || (exclude_the_rest == NULL) || delete_mode)
		return 0;

	if (verbose > 1) {
		rprintf(FINFO,"(using include-only optimization) ");
	}

	/* set exclude_list to NULL temporarily so check_exclude */
	/*   will always return true */
	ex_list = exclude_list;
	exclude_list = NULL;
	for (n=0; (ex = ex_list[n]) != NULL; n++) {
		if (ex == exclude_the_rest)
			break;
		p = ex->pattern;
		while (*p == '/') {
			/* skip the allowed beginning slashes */
			p++;
		}
		/* silently skip files that don't exist to
		   be more like non-optimized case */
		if (access(p,0) == 0)
			send_file_name(f,flist,p,0,0);
	}
	exclude_list = ex_list;
	
	return 1;
}

/* build an exclude structure given a exclude pattern */
static struct exclude_struct *make_exclude(char *pattern, int include)
{
	struct exclude_struct *ret;

	ret = (struct exclude_struct *)malloc(sizeof(*ret));
	if (!ret) out_of_memory("make_exclude");

	memset(ret, 0, sizeof(*ret));

	if (strncmp(pattern,"- ",2) == 0) {
		pattern += 2;
	} else if (strncmp(pattern,"+ ",2) == 0) {
		ret->include = 1;
		pattern += 2;
	} else {
		ret->include = include;
	}

	ret->pattern = strdup(pattern);

	if (!ret->pattern) out_of_memory("make_exclude");

	if (strpbrk(pattern, "*[?")) {
	    if (!ret->include && (*pattern == '*') && (*(pattern+1) == '\0')) {
		    exclude_the_rest = ret;
	    } else {
		    only_included_files = 0;
	    }
	    ret->regular_exp = 1;
	    ret->fnmatch_flags = strstr(pattern, "**") ? 0 : FNM_PATHNAME;
	} else if (!ret->include) {
		only_included_files = 0;
	}

	if (strlen(pattern) > 1 && pattern[strlen(pattern)-1] == '/') {
		ret->pattern[strlen(pattern)-1] = 0;
		ret->directory = 1;
	}

	if (!strchr(ret->pattern,'/')) {
		ret->local = 1;
	}

	return ret;
}

static void free_exclude(struct exclude_struct *ex)
{
	free(ex->pattern);
	memset(ex,0,sizeof(*ex));
	free(ex);
}

static int check_one_exclude(char *name,struct exclude_struct *ex,
			     STRUCT_STAT *st)
{
	char *p;
	int match_start=0;
	char *pattern = ex->pattern;

	if (ex->local && (p=strrchr(name,'/')))
		name = p+1;

	if (!name[0]) return 0;

	if (ex->directory && !S_ISDIR(st->st_mode)) return 0;

	if (*pattern == '/' && *name != '/') {
		match_start = 1;
		pattern++;
	}

	if (ex->regular_exp) {
		if (fnmatch(pattern, name, ex->fnmatch_flags) == 0)
			return 1;
	} else {
		int l1 = strlen(name);
		int l2 = strlen(pattern);
		if (l2 <= l1 && 
		    strcmp(name+(l1-l2),pattern) == 0 &&
		    (l1==l2 || (!match_start && name[l1-(l2+1)] == '/')))
			return 1;
	}

	return 0;
}


int check_exclude(char *name,struct exclude_struct **local_exclude_list,
		  STRUCT_STAT *st)
{
	int n;

	if (name && (name[0] == '.') && !name[1])
		/* never exclude '.', even if somebody does --exclude '*' */
		return 0;

	if (exclude_list) {
		for (n=0; exclude_list[n]; n++)
			if (check_one_exclude(name,exclude_list[n],st))
				return !exclude_list[n]->include;
	}

	if (local_exclude_list) {
		for (n=0; local_exclude_list[n]; n++)
			if (check_one_exclude(name,local_exclude_list[n],st))
				return !local_exclude_list[n]->include;
	}

	return 0;
}


void add_exclude_list(char *pattern,struct exclude_struct ***list, int include)
{
	int len=0;
	if (list && *list)
		for (; (*list)[len]; len++) ;

	if (strcmp(pattern,"!") == 0) {
		if (verbose > 2)
			rprintf(FINFO,"clearing exclude list\n");
		while ((len)--) {
			free_exclude((*list)[len]);
		}
		free((*list));
		*list = NULL;
		only_included_files = 1;
		exclude_the_rest = NULL;
		return;
	}

	*list = (struct exclude_struct **)Realloc(*list,sizeof(struct exclude_struct *)*(len+2));
	
	if (!*list || !((*list)[len] = make_exclude(pattern, include)))
		out_of_memory("add_exclude");
	
	if (verbose > 2)
		rprintf(FINFO,"add_exclude(%s)\n",pattern);
	
	(*list)[len+1] = NULL;
}

void add_exclude(char *pattern, int include)
{
	add_exclude_list(pattern,&exclude_list, include);
}

struct exclude_struct **make_exclude_list(char *fname,
					  struct exclude_struct **list1,
					  int fatal, int include)
{
	struct exclude_struct **list=list1;
	FILE *f = fopen(fname,"r");
	char line[MAXPATHLEN];
	if (!f) {
		if (fatal) {
			rprintf(FERROR,"%s : %s\n",fname,strerror(errno));
			exit_cleanup(RERR_FILEIO);
		}
		return list;
	}

	while (fgets(line,MAXPATHLEN,f)) {
		int l = strlen(line);
		if (l && line[l-1] == '\n') l--;
		line[l] = 0;
		if (line[0] && (line[0] != ';') && (line[0] != '#')) {
			/* Skip lines starting with semicolon or pound.
			   It probably wouldn't cause any harm to not skip
			     them but there's no need to save them. */
			add_exclude_list(line,&list,include);
		}
	}
	fclose(f);
	return list;
}


void add_exclude_file(char *fname,int fatal,int include)
{
	if (!fname || !*fname) return;

	exclude_list = make_exclude_list(fname,exclude_list,fatal,include);
}


void send_exclude_list(int f)
{
	int i;
	extern int remote_version;

	if (!exclude_list) {
		write_int(f,0);
		return;
	}

	for (i=0;exclude_list[i];i++) {
		char *pattern = exclude_list[i]->pattern; 
		int l;

		l = strlen(pattern);
		if (l == 0) continue;
		if (exclude_list[i]->include) {
			if (remote_version < 19) {
				rprintf(FERROR,"remote rsync does not support include syntax - aborting\n");
				exit_cleanup(RERR_UNSUPPORTED);
			}
			write_int(f,l+2);
			write_buf(f,"+ ",2);
		} else {
			write_int(f,l);
		}
		write_buf(f,pattern,l);
	}    

	write_int(f,0);
}


void recv_exclude_list(int f)
{
	char line[MAXPATHLEN];
	int l;
	while ((l=read_int(f))) {
		if (l >= MAXPATHLEN) overflow("recv_exclude_list");
		read_sbuf(f,line,l);
		add_exclude(line,0);
	}
}

/* Get the next include/exclude arg from the string. It works in a similar way
** to strtok - initially an arg is sent over, from then on NULL. This
** routine takes into account any +/- in the strings and does not
** consider the space following it as a delimeter.
*/
char *get_exclude_tok(char *p)
{
	static char *s;
	static int more;
	char *t;

	if (p) {
		s=p;
		if (*p)
			more=1;
	}

	if (!more)
		return(NULL);

	/* Skip over any initial spaces */
	while(isspace(*s))
		s++;

	/* Are we at the end of the string? */
	if (*s) {
		/* remember the beginning of the token */
		t=s;

		/* Is this a '+' or '-' followed by a space (not whitespace)? */
		if ((*s=='+' || *s=='-') && *(s+1)==' ')
			s+=2;
	
		/* Skip to the next space or the end of the string */
		while(!isspace(*s) && *s!='\0')
			s++;
	} else {
		t=NULL;
	}

	/* Have we reached the end of the string? */
	if (*s)
		*s++='\0';
	else
		more=0;
	return(t);
}

	
void add_exclude_line(char *p)
{
	char *tok;
	if (!p || !*p) return;
	p = strdup(p);
	if (!p) out_of_memory("add_exclude_line");
 	for (tok=get_exclude_tok(p); tok; tok=get_exclude_tok(NULL))
		add_exclude(tok, 0);
	free(p);
}

void add_include_line(char *p)
{
	char *tok;
	if (!p || !*p) return;
	p = strdup(p);
	if (!p) out_of_memory("add_include_line");
	for (tok=get_exclude_tok(p); tok; tok=get_exclude_tok(NULL))
		add_exclude(tok, 1);
	free(p);
}


static char *cvs_ignore_list[] = {
  "RCS","SCCS","CVS","CVS.adm","RCSLOG","cvslog.*",
  "tags","TAGS",".make.state",".nse_depinfo",
  "*~", "#*", ".#*", ",*", "*.old", "*.bak", "*.BAK", "*.orig",
  "*.rej", ".del-*", "*.a", "*.o", "*.obj", "*.so", "*.Z", "*.elc", "*.ln",
  "core",NULL};



void add_cvs_excludes(void)
{
	char fname[MAXPATHLEN];
	char *p;
	int i;
  
	for (i=0; cvs_ignore_list[i]; i++)
		add_exclude(cvs_ignore_list[i], 0);

	if ((p=getenv("HOME")) && strlen(p) < (MAXPATHLEN-12)) {
		slprintf(fname,sizeof(fname), "%s/.cvsignore",p);
		add_exclude_file(fname,0,0);
	}

	add_exclude_line(getenv("CVSIGNORE"));
}
