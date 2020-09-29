/*
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
 *
 * This is based on loadparm.c from Samba, written by Andrew Tridgell
 * and Karl Auer.  Some of the changes are:
 *
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2020 Wayne Davison
 */

/* Load parameters.
 *
 *  This module provides suitable callback functions for the params
 *  module. It builds the internal table of section details which is
 *  then used by the rest of the server.
 *
 * To add a parameter:
 *
 * 1) add it to the global_vars or local_vars structure definition
 * 2) add it to the parm_table
 * 3) add it to the list of available functions (eg: using FN_GLOBAL_STRING())
 * 4) initialise it in the Defaults static structure
 *
 * Notes:
 *   The configuration file is processed sequentially for speed. For this
 *   reason, there is a fair bit of sequence-dependent code here - ie., code
 *   which assumes that certain things happen before others. In particular, the
 *   code which happens at the boundary between sections is delicately poised,
 *   so be careful!
 */

#include "rsync.h"
#include "itypes.h"
#include "ifuncs.h"
#include "default-dont-compress.h"

extern item_list dparam_list;

#define strequal(a, b) (strcasecmp(a, b)==0)

#ifndef LOG_DAEMON
#define LOG_DAEMON 0
#endif

/* the following are used by loadparm for option lists */
typedef enum {
	P_BOOL, P_BOOLREV, P_BOOL3, P_CHAR, P_INTEGER,
	P_OCTAL, P_PATH, P_STRING, P_ENUM
} parm_type;

typedef enum {
	P_LOCAL, P_GLOBAL, P_NONE
} parm_class;

struct enum_list {
	int value;
	char *name;
};

struct parm_struct {
	char *label;
	parm_type type;
	parm_class class;
	void *ptr;
	struct enum_list *enum_list;
	unsigned flags;
};

#ifndef GLOBAL_NAME
#define GLOBAL_NAME "global"
#endif

/* some helpful bits */
#define iSECTION(i) ((local_vars*)section_list.items)[i]
#define LP_SNUM_OK(i) ((i) >= 0 && (i) < (int)section_list.count)
#define SECTION_PTR(s, p) (((char*)(s)) + (ptrdiff_t)(((char*)(p))-(char*)&Vars.l))

/* Stack of "Vars" values used by the &include directive. */
static item_list Vars_stack = EMPTY_ITEM_LIST;

/* The array of section values that holds all the defined modules. */
static item_list section_list = EMPTY_ITEM_LIST;

static int iSectionIndex = -1;
static BOOL bInGlobalSection = True;

static struct enum_list enum_syslog_facility[] = {
#ifdef LOG_AUTH
	{ LOG_AUTH, "auth" },
#endif
#ifdef LOG_AUTHPRIV
	{ LOG_AUTHPRIV, "authpriv" },
#endif
#ifdef LOG_CRON
	{ LOG_CRON, "cron" },
#endif
#ifdef LOG_DAEMON
	{ LOG_DAEMON, "daemon" },
#endif
#ifdef LOG_FTP
	{ LOG_FTP, "ftp" },
#endif
#ifdef LOG_KERN
	{ LOG_KERN, "kern" },
#endif
#ifdef LOG_LPR
	{ LOG_LPR, "lpr" },
#endif
#ifdef LOG_MAIL
	{ LOG_MAIL, "mail" },
#endif
#ifdef LOG_NEWS
	{ LOG_NEWS, "news" },
#endif
#ifdef LOG_AUTH
	{ LOG_AUTH, "security" },
#endif
#ifdef LOG_SYSLOG
	{ LOG_SYSLOG, "syslog" },
#endif
#ifdef LOG_USER
	{ LOG_USER, "user" },
#endif
#ifdef LOG_UUCP
	{ LOG_UUCP, "uucp" },
#endif
#ifdef LOG_LOCAL0
	{ LOG_LOCAL0, "local0" },
#endif
#ifdef LOG_LOCAL1
	{ LOG_LOCAL1, "local1" },
#endif
#ifdef LOG_LOCAL2
	{ LOG_LOCAL2, "local2" },
#endif
#ifdef LOG_LOCAL3
	{ LOG_LOCAL3, "local3" },
#endif
#ifdef LOG_LOCAL4
	{ LOG_LOCAL4, "local4" },
#endif
#ifdef LOG_LOCAL5
	{ LOG_LOCAL5, "local5" },
#endif
#ifdef LOG_LOCAL6
	{ LOG_LOCAL6, "local6" },
#endif
#ifdef LOG_LOCAL7
	{ LOG_LOCAL7, "local7" },
#endif
	{ -1, NULL }
};

/* Expand %VAR% references.  Any unknown vars or unrecognized
 * syntax leaves the raw chars unchanged. */
static char *expand_vars(const char *str)
{
	char *buf, *t;
	const char *f;
	int bufsize;

	if (!str || !strchr(str, '%'))
		return (char *)str; /* TODO change return value to const char* at some point. */

	bufsize = strlen(str) + 2048;
	buf = new_array(char, bufsize+1); /* +1 for trailing '\0' */

	for (t = buf, f = str; bufsize && *f; ) {
		if (*f == '%' && isUpper(f+1)) {
			char *percent = strchr(f+1, '%');
			if (percent && percent - f < bufsize) {
				char *val;
				strlcpy(t, f+1, percent - f);
				val = getenv(t);
				if (val) {
					int len = strlcpy(t, val, bufsize+1);
					if (len > bufsize)
						break;
					bufsize -= len;
					t += len;
					f = percent + 1;
					continue;
				}
			}
		}
		*t++ = *f++;
		bufsize--;
	}
	*t = '\0';

	if (*f) {
		rprintf(FLOG, "Overflowed buf in expand_vars() trying to expand: %s\n", str);
		exit_cleanup(RERR_MALLOC);
	}

	if (bufsize && (buf = realloc(buf, t - buf + 1)) == NULL)
		out_of_memory("expand_vars");

	return buf;
}

/* Each "char* foo" has an associated "BOOL foo_EXP" that tracks if the string has been expanded yet or not. */

/* NOTE: use this function and all the FN_{GLOBAL,LOCAL} ones WITHOUT a trailing semicolon! */
#define RETURN_EXPANDED(val) {if (!val ## _EXP) {val = expand_vars(val); val ## _EXP = True;} return val ? val : "";}

/* In this section all the functions that are used to access the
 * parameters from the rest of the program are defined. */

#define FN_GLOBAL_STRING(fn_name, val) \
 char *fn_name(void) RETURN_EXPANDED(Vars.g.val)
#define FN_GLOBAL_BOOL(fn_name, val) \
 BOOL fn_name(void) {return Vars.g.val;}
#define FN_GLOBAL_CHAR(fn_name, val) \
 char fn_name(void) {return Vars.g.val;}
#define FN_GLOBAL_INTEGER(fn_name, val) \
 int fn_name(void) {return Vars.g.val;}

#define FN_LOCAL_STRING(fn_name, val) \
 char *fn_name(int i) {if (LP_SNUM_OK(i) && iSECTION(i).val) RETURN_EXPANDED(iSECTION(i).val) else RETURN_EXPANDED(Vars.l.val)}
#define FN_LOCAL_BOOL(fn_name, val) \
 BOOL fn_name(int i) {return LP_SNUM_OK(i)? iSECTION(i).val : Vars.l.val;}
#define FN_LOCAL_CHAR(fn_name, val) \
 char fn_name(int i) {return LP_SNUM_OK(i)? iSECTION(i).val : Vars.l.val;}
#define FN_LOCAL_INTEGER(fn_name, val) \
 int fn_name(int i) {return LP_SNUM_OK(i)? iSECTION(i).val : Vars.l.val;}

/* The following include file contains:
 *
 * typedef global_vars - describes global (ie., server-wide) parameters.
 * typedef local_vars - describes a single section.
 * typedef all_vars - a combination of global_vars & local_vars.
 * all_vars Defaults - the default values for all the variables.
 * all_vars Vars - the currently configured values for all the variables.
 * struct parm_struct parm_table - the strings & variables for the parser.
 * FN_{LOCAL,GLOBAL}_{TYPE}() definition for all the lp_var_name() accessors.
 */

#include "daemon-parm.h"

/* Initialise the Default all_vars structure. */
void reset_daemon_vars(void)
{
	memcpy(&Vars, &Defaults, sizeof Vars);
}

/* Assign a copy of v to *s.  Handles NULL strings.  We don't worry
 * about overwriting a malloc'd string because the long-running
 * (port-listening) daemon only loads the config file once, and the
 * per-job (forked or xinitd-ran) daemon only re-reads the file at
 * the start, so any lost memory is inconsequential. */
static inline void string_set(char **s, const char *v)
{
	*s = v ? strdup(v) : NULL;
}

/* Copy local_vars into a new section. No need to strdup since we don't free. */
static void copy_section(local_vars *psectionDest, local_vars *psectionSource)
{
	memcpy(psectionDest, psectionSource, sizeof psectionDest[0]);
}

/* Initialise a section to the defaults. */
static void init_section(local_vars *psection)
{
	memset(psection, 0, sizeof (local_vars));
	copy_section(psection, &Vars.l);
}

/* Do a case-insensitive, whitespace-ignoring string equality check. */
static int strwiEQ(char *psz1, char *psz2)
{
	/* If one or both strings are NULL, we return equality right away. */
	if (psz1 == psz2)
		return 1;
	if (psz1 == NULL || psz2 == NULL)
		return 0;

	/* sync the strings on first non-whitespace */
	while (1) {
		while (isSpace(psz1))
			psz1++;
		while (isSpace(psz2))
			psz2++;
		if (*psz1 == '\0' || *psz2 == '\0')
			break;
		if (toUpper(psz1) != toUpper(psz2))
			break;
		psz1++;
		psz2++;
	}
	return *psz1 == *psz2;
}

/* Find a section by name. Otherwise works like get_section. */
static int getsectionbyname(char *name)
{
	int i;

	for (i = section_list.count - 1; i >= 0; i--) {
		if (strwiEQ(iSECTION(i).name, name))
			break;
	}

	return i;
}

/* Add a new section to the sections array w/the default values. */
static int add_a_section(char *name)
{
	int i;
	local_vars *s;

	/* it might already exist */
	if (name) {
		i = getsectionbyname(name);
		if (i >= 0)
			return i;
	}

	i = section_list.count;
	s = EXPAND_ITEM_LIST(&section_list, local_vars, 2);

	init_section(s);
	if (name)
		string_set(&s->name, name);

	return i;
}

/* Map a parameter's string representation to something we can use.
 * Returns False if the parameter string is not recognised, else TRUE. */
static int map_parameter(char *parmname)
{
	int iIndex;

	if (*parmname == '-')
		return -1;

	for (iIndex = 0; parm_table[iIndex].label; iIndex++) {
		if (strwiEQ(parm_table[iIndex].label, parmname))
			return iIndex;
	}

	rprintf(FLOG, "Unknown Parameter encountered: \"%s\"\n", parmname);
	return -1;
}

/* Set a boolean variable from the text value stored in the passed string.
 * Returns True in success, False if the passed string does not correctly
 * represent a boolean. */
static BOOL set_boolean(BOOL *pb, char *parmvalue, int allow_unset)
{
	if (strwiEQ(parmvalue, "yes") || strwiEQ(parmvalue, "true") || strwiEQ(parmvalue, "1"))
		*pb = True;
	else if (strwiEQ(parmvalue, "no") || strwiEQ(parmvalue, "false") || strwiEQ(parmvalue, "0"))
		*pb = False;
	else if (allow_unset && (strwiEQ(parmvalue, "unset") || strwiEQ(parmvalue, "-1")))
		*pb = Unset;
	else {
		rprintf(FLOG, "Badly formed boolean in configuration file: \"%s\".\n", parmvalue);
		return False;
	}
	return True;
}

/* Process a parameter. */
static BOOL do_parameter(char *parmname, char *parmvalue)
{
	int parmnum, i;
	void *parm_ptr; /* where we are going to store the result */
	void *def_ptr;
	char *cp;

	parmnum = map_parameter(parmname);

	if (parmnum < 0) {
		rprintf(FLOG, "IGNORING unknown parameter \"%s\"\n", parmname);
		return True;
	}

	def_ptr = parm_table[parmnum].ptr;

	if (bInGlobalSection)
		parm_ptr = def_ptr;
	else {
		if (parm_table[parmnum].class == P_GLOBAL) {
			rprintf(FLOG, "Global parameter %s found in module section!\n", parmname);
			return True;
		}
		parm_ptr = SECTION_PTR(&iSECTION(iSectionIndex), def_ptr);
	}

	/* now switch on the type of variable it is */
	switch (parm_table[parmnum].type) {
	case P_PATH:
	case P_STRING:
		/* delay expansion of %VAR% strings */
		break;
	default:
		/* expand any %VAR% strings now */
		parmvalue = expand_vars(parmvalue);
		break;
	}

	switch (parm_table[parmnum].type) {
	case P_BOOL:
		set_boolean(parm_ptr, parmvalue, False);
		break;

	case P_BOOL3:
		set_boolean(parm_ptr, parmvalue, True);
		break;

	case P_BOOLREV:
		set_boolean(parm_ptr, parmvalue, False);
		*(BOOL *)parm_ptr = ! *(BOOL *)parm_ptr;
		break;

	case P_INTEGER:
		*(int *)parm_ptr = atoi(parmvalue);
		break;

	case P_CHAR:
		*(char *)parm_ptr = *parmvalue;
		break;

	case P_OCTAL:
		sscanf(parmvalue, "%o", (unsigned int *)parm_ptr);
		break;

	case P_PATH:
		string_set(parm_ptr, parmvalue);
		if ((cp = *(char**)parm_ptr) != NULL) {
			int len = strlen(cp);
			while (len > 1 && cp[len-1] == '/') len--;
			cp[len] = '\0';
		}
		break;

	case P_STRING:
		string_set(parm_ptr, parmvalue);
		break;

	case P_ENUM:
		for (i=0; parm_table[parmnum].enum_list[i].name; i++) {
			if (strequal(parmvalue, parm_table[parmnum].enum_list[i].name)) {
				*(int *)parm_ptr = parm_table[parmnum].enum_list[i].value;
				break;
			}
		}
		if (!parm_table[parmnum].enum_list[i].name) {
			if (atoi(parmvalue) > 0)
				*(int *)parm_ptr = atoi(parmvalue);
		}
		break;
	}

	return True;
}

/* Process a new section (rsync module).
 * Returns True on success, False on failure. */
static BOOL do_section(char *sectionname)
{
	BOOL isglobal;

	if (*sectionname == ']') { /* A special push/pop/reset directive from params.c */
		bInGlobalSection = 1;
		if (strcmp(sectionname+1, "push") == 0) {
			all_vars *vp = EXPAND_ITEM_LIST(&Vars_stack, all_vars, 2);
			memcpy(vp, &Vars, sizeof Vars);
		} else if (strcmp(sectionname+1, "pop") == 0
		 || strcmp(sectionname+1, "reset") == 0) {
			all_vars *vp = ((all_vars*)Vars_stack.items) + Vars_stack.count - 1;
			if (!Vars_stack.count)
				return False;
			memcpy(&Vars, vp, sizeof Vars);
			if (sectionname[1] == 'p')
				Vars_stack.count--;
		} else
			return False;
		return True;
	}

	isglobal = strwiEQ(sectionname, GLOBAL_NAME);

	/* At the end of the global section, add any --dparam items. */
	if (bInGlobalSection && !isglobal) {
		if (!section_list.count)
			set_dparams(0);
	}

	/* if we've just struck a global section, note the fact. */
	bInGlobalSection = isglobal;

	/* check for multiple global sections */
	if (bInGlobalSection)
		return True;

#if 0
	/* If we have a current section, tidy it up before moving on. */
	if (iSectionIndex >= 0) {
		/* Add any tidy work as needed ... */
		if (problem)
			return False;
	}
#endif

	if (strchr(sectionname, '/') != NULL) {
		rprintf(FLOG, "Warning: invalid section name in configuration file: %s\n", sectionname);
		return False;
	}

	if ((iSectionIndex = add_a_section(sectionname)) < 0) {
		rprintf(FLOG, "Failed to add a new module\n");
		bInGlobalSection = True;
		return False;
	}

	return True;
}

/* Load the modules from the config file. Return True on success,
 * False on failure. */
int lp_load(char *pszFname, int globals_only)
{
	bInGlobalSection = True;

	reset_daemon_vars();

	/* We get sections first, so have to start 'behind' to make up. */
	iSectionIndex = -1;
	return pm_process(pszFname, globals_only ? NULL : do_section, do_parameter);
}

BOOL set_dparams(int syntax_check_only)
{
	char *equal, *val, **params = dparam_list.items;
	unsigned j;

	for (j = 0; j < dparam_list.count; j++) {
		equal = strchr(params[j], '='); /* options.c verified this */
		*equal = '\0';
		if (syntax_check_only) {
			if (map_parameter(params[j]) < 0) {
				rprintf(FERROR, "Unknown parameter \"%s\"\n", params[j]);
				*equal = '=';
				return False;
			}
		} else {
			for (val = equal+1; isSpace(val); val++) {}
			do_parameter(params[j], val);
		}
		*equal = '=';
	}

	return True;
}

/* Return the max number of modules (sections). */
int lp_num_modules(void)
{
	return section_list.count;
}

/* Return the number of the module with the given name, or -1 if it doesn't
 * exist. Note that this is a DIFFERENT ANIMAL from the internal function
 * getsectionbyname()! This works ONLY if all sections have been loaded,
 * and does not copy the found section. */
int lp_number(char *name)
{
	int i;

	for (i = section_list.count - 1; i >= 0; i--) {
		if (strcmp(lp_name(i), name) == 0)
			break;
	}

	return i;
}
