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

/*
  a lot of this stuff was derived from GNU tar
  */

#include "rsync.h"

extern int verbose;

static char **exclude_list;

static int is_regex(char *str)
{
  return strchr(str, '*') || strchr(str, '[') || strchr(str, '?');
}


static int check_one_exclude(char *name,char *pattern)
{
  char *p;

  if (!strchr(pattern,'/') && (p=strrchr(name,'/')))
    name = p+1;

  if (!name[0]) return 0;

  if (*pattern == '/' && *name != '/') pattern++;

  if (is_regex(pattern)) {
    if (fnmatch(pattern, name, 0) == 0)
      return 1;
  } else {
    int l1 = strlen(name);
    int l2 = strlen(pattern);
    if (l2 <= l1 && 
	strcmp(name+(l1-l2),pattern) == 0 &&
	(l1==l2 || name[l1-(l2+1)] == '/'))
      return 1;
  }

  return 0;
}


int check_exclude(char *name,char **local_exclude_list)
{
  int n;

  if (exclude_list) {
    for (n=0; exclude_list[n]; n++)
      if (check_one_exclude(name,exclude_list[n]))
	return 1;
  }

  if (local_exclude_list) {
    for (n=0; local_exclude_list[n]; n++)
      if (check_one_exclude(name,local_exclude_list[n]))
	return 1;      
  }

  return 0;
}


void add_exclude_list(char *pattern,char ***list)
{
  int len=0;
  if (list && *list)
    for (; (*list)[len]; len++) ;

  if (strcmp(pattern,"!") == 0) {
    if (verbose > 2)
      rprintf(FINFO,"clearing exclude list\n");
    while ((len)--) 
      free((*list)[len]);
    free((*list));
    *list = NULL;
    return;
  }

  if (!*list) {
    *list = (char **)malloc(sizeof(char *)*2);
  } else {
    *list = (char **)realloc(*list,sizeof(char *)*(len+2));
  }

  if (!*list || !((*list)[len] = strdup(pattern)))
    out_of_memory("add_exclude");

  if (verbose > 2)
    rprintf(FINFO,"add_exclude(%s)\n",pattern);
  
  (*list)[len+1] = NULL;
}

void add_exclude(char *pattern)
{
  add_exclude_list(pattern,&exclude_list);
}

char **make_exclude_list(char *fname,char **list1,int fatal)
{
  char **list=list1;
  FILE *f = fopen(fname,"r");
  char line[MAXPATHLEN];
  if (!f) {
    if (fatal) {
      rprintf(FERROR,"%s : %s\n",fname,strerror(errno));
      exit_cleanup(1);
    }
    return list;
  }

  while (fgets(line,MAXPATHLEN,f)) {
    int l = strlen(line);
    if (l && line[l-1] == '\n') l--;
    line[l] = 0;
    if (line[0]) add_exclude_list(line,&list);
  }
  fclose(f);
  return list;
}


void add_exclude_file(char *fname,int fatal)
{
	if (!fname || !*fname) return;

	exclude_list = make_exclude_list(fname,exclude_list,fatal);
}


void send_exclude_list(int f)
{
  int i;
  if (exclude_list) 
    for (i=0;exclude_list[i];i++) {
      int l = strlen(exclude_list[i]);
      if (l == 0) continue;
      write_int(f,l);
      write_buf(f,exclude_list[i],l);
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
	  add_exclude(line);
  }
}


void add_exclude_line(char *p)
{
	char *tok;
	if (!p || !*p) return;
	p = strdup(p);
	if (!p) out_of_memory("add_exclude_line");
	for (tok=strtok(p," "); tok; tok=strtok(NULL," "))
		add_exclude(tok);
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
    add_exclude(cvs_ignore_list[i]);

  if ((p=getenv("HOME")) && strlen(p) < (MAXPATHLEN-12)) {
	  slprintf(fname,sizeof(fname)-1, "%s/.cvsignore",p);
	  add_exclude_file(fname,0);
  }

  add_exclude_line(getenv("CVSIGNORE"));
}
