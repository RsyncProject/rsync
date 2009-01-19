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
 */

/* This is based on loadparm.c from Samba, written by Andrew Tridgell
 * and Karl Auer.  Some of the changes are:
 *
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2009 Wayne Davison <wayned@samba.org>
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
 * 4) If it's a global then initialise it in init_globals. If a local module
 *    (ie. section) parameter then initialise it in the Locals structure
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

extern item_list dparam_list;

#define strequal(a, b) (strcasecmp(a, b)==0)
#define BOOLSTR(b) ((b) ? "Yes" : "No")

#ifndef LOG_DAEMON
#define LOG_DAEMON 0
#endif

#define DEFAULT_DONT_COMPRESS "*.gz *.zip *.z *.rpm *.deb *.iso *.bz2" \
	" *.t[gb]z *.7z *.mp[34] *.mov *.avi *.ogg *.jpg *.jpeg"

/* the following are used by loadparm for option lists */
typedef enum {
	P_BOOL, P_BOOLREV, P_CHAR, P_INTEGER,
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
#define SECTION_PTR(s, p) (((char*)(s)) + (ptrdiff_t)(((char*)(p))-(char*)&Locals))

/*
 * This structure describes global (ie., server-wide) parameters.
 */
typedef struct {
	char *bind_address;
	char *motd_file;
	char *pid_file;
	char *socket_options;

	int rsync_port;
} global_vars;

static global_vars Globals;

/*
 * This structure describes a single section.  Their order must match the
 * initializers below, which you can accomplish by keeping each sub-section
 * sorted.  (e.g. in vim, just visually select each subsection and use !sort.)
 */
typedef struct {
	char *auth_users;
	char *charset;
	char *comment;
	char *dont_compress;
	char *exclude;
	char *exclude_from;
	char *filter;
	char *gid;
	char *hosts_allow;
	char *hosts_deny;
	char *include;
	char *include_from;
	char *incoming_chmod;
	char *lock_file;
	char *log_file;
	char *log_format;
	char *name;
	char *outgoing_chmod;
	char *path;
	char *postxfer_exec;
	char *prexfer_exec;
	char *refuse_options;
	char *secrets_file;
	char *temp_dir;
	char *uid;

	int max_connections;
	int max_verbosity;
	int syslog_facility;
	int timeout;

	BOOL fake_super;
	BOOL ignore_errors;
	BOOL ignore_nonreadable;
	BOOL list;
	BOOL munge_symlinks;
	BOOL numeric_ids;
	BOOL read_only;
	BOOL reverse_lookup;
	BOOL strict_modes;
	BOOL transfer_logging;
	BOOL use_chroot;
	BOOL write_only;
} local_vars;

typedef struct {
	global_vars g;
	local_vars l;
} all_vars;

/* This is a default section used to prime a sections structure.  In order
 * to make these easy to keep sorted in the same way as the variables
 * above, use the variable name in the leading comment, including a
 * trailing ';' (to avoid a sorting problem with trailing digits). */
static local_vars Locals = {
 /* auth_users; */		NULL,
 /* charset; */ 		NULL,
 /* comment; */ 		NULL,
 /* dont_compress; */		DEFAULT_DONT_COMPRESS,
 /* exclude; */			NULL,
 /* exclude_from; */		NULL,
 /* filter; */			NULL,
 /* gid; */			NOBODY_GROUP,
 /* hosts_allow; */		NULL,
 /* hosts_deny; */		NULL,
 /* include; */			NULL,
 /* include_from; */		NULL,
 /* incoming_chmod; */		NULL,
 /* lock_file; */		DEFAULT_LOCK_FILE,
 /* log_file; */		NULL,
 /* log_format; */		"%o %h [%a] %m (%u) %f %l",
 /* name; */			NULL,
 /* outgoing_chmod; */		NULL,
 /* path; */			NULL,
 /* postxfer_exec; */		NULL,
 /* prexfer_exec; */		NULL,
 /* refuse_options; */		NULL,
 /* secrets_file; */		NULL,
 /* temp_dir; */ 		NULL,
 /* uid; */			NOBODY_USER,

 /* max_connections; */		0,
 /* max_verbosity; */		1,
 /* syslog_facility; */		LOG_DAEMON,
 /* timeout; */			0,

 /* fake_super; */		False,
 /* ignore_errors; */		False,
 /* ignore_nonreadable; */	False,
 /* list; */			True,
 /* munge_symlinks; */		(BOOL)-1,
 /* numeric_ids; */		(BOOL)-1,
 /* read_only; */		True,
 /* reverse_lookup; */		True,
 /* strict_modes; */		True,
 /* transfer_logging; */	False,
 /* use_chroot; */		True,
 /* write_only; */		False,
};

/* local variables */
static item_list section_list = EMPTY_ITEM_LIST;
static item_list section_stack = EMPTY_ITEM_LIST;
static int iSectionIndex = -1;
static BOOL bInGlobalSection = True;

#define NUMPARAMETERS (sizeof (parm_table) / sizeof (struct parm_struct))

static struct enum_list enum_facilities[] = {
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

static struct parm_struct parm_table[] =
{
 {"address",           P_STRING, P_GLOBAL,&Globals.bind_address,       NULL,0},
 {"motd file",         P_STRING, P_GLOBAL,&Globals.motd_file,          NULL,0},
 {"pid file",          P_STRING, P_GLOBAL,&Globals.pid_file,           NULL,0},
 {"port",              P_INTEGER,P_GLOBAL,&Globals.rsync_port,         NULL,0},
 {"socket options",    P_STRING, P_GLOBAL,&Globals.socket_options,     NULL,0},

 {"auth users",        P_STRING, P_LOCAL, &Locals.auth_users,          NULL,0},
 {"charset",           P_STRING, P_LOCAL, &Locals.charset,             NULL,0},
 {"comment",           P_STRING, P_LOCAL, &Locals.comment,             NULL,0},
 {"dont compress",     P_STRING, P_LOCAL, &Locals.dont_compress,       NULL,0},
 {"exclude from",      P_STRING, P_LOCAL, &Locals.exclude_from,        NULL,0},
 {"exclude",           P_STRING, P_LOCAL, &Locals.exclude,             NULL,0},
 {"fake super",        P_BOOL,   P_LOCAL, &Locals.fake_super,          NULL,0},
 {"filter",            P_STRING, P_LOCAL, &Locals.filter,              NULL,0},
 {"gid",               P_STRING, P_LOCAL, &Locals.gid,                 NULL,0},
 {"hosts allow",       P_STRING, P_LOCAL, &Locals.hosts_allow,         NULL,0},
 {"hosts deny",        P_STRING, P_LOCAL, &Locals.hosts_deny,          NULL,0},
 {"ignore errors",     P_BOOL,   P_LOCAL, &Locals.ignore_errors,       NULL,0},
 {"ignore nonreadable",P_BOOL,   P_LOCAL, &Locals.ignore_nonreadable,  NULL,0},
 {"include from",      P_STRING, P_LOCAL, &Locals.include_from,        NULL,0},
 {"include",           P_STRING, P_LOCAL, &Locals.include,             NULL,0},
 {"incoming chmod",    P_STRING, P_LOCAL, &Locals.incoming_chmod,      NULL,0},
 {"list",              P_BOOL,   P_LOCAL, &Locals.list,                NULL,0},
 {"lock file",         P_STRING, P_LOCAL, &Locals.lock_file,           NULL,0},
 {"log file",          P_STRING, P_LOCAL, &Locals.log_file,            NULL,0},
 {"log format",        P_STRING, P_LOCAL, &Locals.log_format,          NULL,0},
 {"max connections",   P_INTEGER,P_LOCAL, &Locals.max_connections,     NULL,0},
 {"max verbosity",     P_INTEGER,P_LOCAL, &Locals.max_verbosity,       NULL,0},
 {"munge symlinks",    P_BOOL,   P_LOCAL, &Locals.munge_symlinks,      NULL,0},
 {"name",              P_STRING, P_LOCAL, &Locals.name,                NULL,0},
 {"numeric ids",       P_BOOL,   P_LOCAL, &Locals.numeric_ids,         NULL,0},
 {"outgoing chmod",    P_STRING, P_LOCAL, &Locals.outgoing_chmod,      NULL,0},
 {"path",              P_PATH,   P_LOCAL, &Locals.path,                NULL,0},
#ifdef HAVE_PUTENV
 {"post-xfer exec",    P_STRING, P_LOCAL, &Locals.postxfer_exec,       NULL,0},
 {"pre-xfer exec",     P_STRING, P_LOCAL, &Locals.prexfer_exec,        NULL,0},
#endif
 {"read only",         P_BOOL,   P_LOCAL, &Locals.read_only,           NULL,0},
 {"refuse options",    P_STRING, P_LOCAL, &Locals.refuse_options,      NULL,0},
 {"reverse lookup",    P_BOOL,   P_LOCAL, &Locals.reverse_lookup,      NULL,0},
 {"secrets file",      P_STRING, P_LOCAL, &Locals.secrets_file,        NULL,0},
 {"strict modes",      P_BOOL,   P_LOCAL, &Locals.strict_modes,        NULL,0},
 {"syslog facility",   P_ENUM,   P_LOCAL, &Locals.syslog_facility,     enum_facilities,0},
 {"temp dir",          P_PATH,   P_LOCAL, &Locals.temp_dir,            NULL,0},
 {"timeout",           P_INTEGER,P_LOCAL, &Locals.timeout,             NULL,0},
 {"transfer logging",  P_BOOL,   P_LOCAL, &Locals.transfer_logging,    NULL,0},
 {"uid",               P_STRING, P_LOCAL, &Locals.uid,                 NULL,0},
 {"use chroot",        P_BOOL,   P_LOCAL, &Locals.use_chroot,          NULL,0},
 {"write only",        P_BOOL,   P_LOCAL, &Locals.write_only,          NULL,0},
 {NULL,                P_BOOL,   P_NONE,  NULL,                        NULL,0}
};

/* Initialise the global parameter structure. */
static void init_globals(void)
{
	memset(&Globals, 0, sizeof Globals);
}

/* Initialise the Locals parameter structure. */
static void init_locals(void)
{
	/* Nothing needed yet... */
}

/* In this section all the functions that are used to access the
 * parameters from the rest of the program are defined. */

#define FN_GLOBAL_STRING(fn_name, ptr) \
 char *fn_name(void) {return *(char **)(ptr) ? *(char **)(ptr) : "";}
#define FN_GLOBAL_BOOL(fn_name, ptr) \
 BOOL fn_name(void) {return *(BOOL *)(ptr);}
#define FN_GLOBAL_CHAR(fn_name, ptr) \
 char fn_name(void) {return *(char *)(ptr);}
#define FN_GLOBAL_INTEGER(fn_name, ptr) \
 int fn_name(void) {return *(int *)(ptr);}

#define FN_LOCAL_STRING(fn_name, val) \
 char *fn_name(int i) {return LP_SNUM_OK(i) && iSECTION(i).val? iSECTION(i).val : (Locals.val? Locals.val : "");}
#define FN_LOCAL_BOOL(fn_name, val) \
 BOOL fn_name(int i) {return LP_SNUM_OK(i)? iSECTION(i).val : Locals.val;}
#define FN_LOCAL_CHAR(fn_name, val) \
 char fn_name(int i) {return LP_SNUM_OK(i)? iSECTION(i).val : Locals.val;}
#define FN_LOCAL_INTEGER(fn_name, val) \
 int fn_name(int i) {return LP_SNUM_OK(i)? iSECTION(i).val : Locals.val;}

FN_GLOBAL_STRING(lp_bind_address, &Globals.bind_address)
FN_GLOBAL_STRING(lp_motd_file, &Globals.motd_file)
FN_GLOBAL_STRING(lp_pid_file, &Globals.pid_file)
FN_GLOBAL_STRING(lp_socket_options, &Globals.socket_options)

FN_GLOBAL_INTEGER(lp_rsync_port, &Globals.rsync_port)

FN_LOCAL_STRING(lp_auth_users, auth_users)
FN_LOCAL_STRING(lp_charset, charset)
FN_LOCAL_STRING(lp_comment, comment)
FN_LOCAL_STRING(lp_dont_compress, dont_compress)
FN_LOCAL_STRING(lp_exclude, exclude)
FN_LOCAL_STRING(lp_exclude_from, exclude_from)
FN_LOCAL_STRING(lp_filter, filter)
FN_LOCAL_STRING(lp_gid, gid)
FN_LOCAL_STRING(lp_hosts_allow, hosts_allow)
FN_LOCAL_STRING(lp_hosts_deny, hosts_deny)
FN_LOCAL_STRING(lp_include, include)
FN_LOCAL_STRING(lp_include_from, include_from)
FN_LOCAL_STRING(lp_incoming_chmod, incoming_chmod)
FN_LOCAL_STRING(lp_lock_file, lock_file)
FN_LOCAL_STRING(lp_log_file, log_file)
FN_LOCAL_STRING(lp_log_format, log_format)
FN_LOCAL_STRING(lp_name, name)
FN_LOCAL_STRING(lp_outgoing_chmod, outgoing_chmod)
FN_LOCAL_STRING(lp_path, path)
FN_LOCAL_STRING(lp_postxfer_exec, postxfer_exec)
FN_LOCAL_STRING(lp_prexfer_exec, prexfer_exec)
FN_LOCAL_STRING(lp_refuse_options, refuse_options)
FN_LOCAL_STRING(lp_secrets_file, secrets_file)
FN_LOCAL_STRING(lp_temp_dir, temp_dir)
FN_LOCAL_STRING(lp_uid, uid)

FN_LOCAL_INTEGER(lp_max_connections, max_connections)
FN_LOCAL_INTEGER(lp_max_verbosity, max_verbosity)
FN_LOCAL_INTEGER(lp_syslog_facility, syslog_facility)
FN_LOCAL_INTEGER(lp_timeout, timeout)

FN_LOCAL_BOOL(lp_fake_super, fake_super)
FN_LOCAL_BOOL(lp_ignore_errors, ignore_errors)
FN_LOCAL_BOOL(lp_ignore_nonreadable, ignore_nonreadable)
FN_LOCAL_BOOL(lp_list, list)
FN_LOCAL_BOOL(lp_munge_symlinks, munge_symlinks)
FN_LOCAL_BOOL(lp_numeric_ids, numeric_ids)
FN_LOCAL_BOOL(lp_read_only, read_only)
FN_LOCAL_BOOL(lp_reverse_lookup, reverse_lookup)
FN_LOCAL_BOOL(lp_strict_modes, strict_modes)
FN_LOCAL_BOOL(lp_transfer_logging, transfer_logging)
FN_LOCAL_BOOL(lp_use_chroot, use_chroot)
FN_LOCAL_BOOL(lp_write_only, write_only)

/* Assign a copy of v to *s.  Handles NULL strings.  *v must
 * be initialized when this is called, either to NULL or a malloc'd
 * string.
 *
 * FIXME There is a small leak here in that sometimes the existing
 * value will be dynamically allocated, and the old copy is lost.
 * However, we can't always deallocate the old value, because in the
 * case of Locals, it points to a static string.  It would be nice
 * to have either all-strdup'd values, or to never need to free
 * memory. */
static void string_set(char **s, const char *v)
{
	if (!v) {
		*s = NULL;
		return;
	}
	if (!(*s = strdup(v)))
		out_of_memory("string_set");
}

/* Copy the local_vars, duplicating any strings in the source. */
static void copy_section(local_vars *psectionDest, local_vars *psectionSource)
{
	int i;

	for (i = 0; parm_table[i].label; i++) {
		if (parm_table[i].ptr && parm_table[i].class == P_LOCAL) {
			void *def_ptr = parm_table[i].ptr;
			void *src_ptr = SECTION_PTR(psectionSource, def_ptr);
			void *dest_ptr = SECTION_PTR(psectionDest, def_ptr);

			switch (parm_table[i].type) {
			case P_BOOL:
			case P_BOOLREV:
				*(BOOL *)dest_ptr = *(BOOL *)src_ptr;
				break;

			case P_INTEGER:
			case P_ENUM:
			case P_OCTAL:
				*(int *)dest_ptr = *(int *)src_ptr;
				break;

			case P_CHAR:
				*(char *)dest_ptr = *(char *)src_ptr;
				break;

			case P_PATH:
			case P_STRING:
				string_set(dest_ptr, *(char **)src_ptr);
				break;

			default:
				break;
			}
		}
	}
}

/* Initialise a section to the defaults. */
static void init_section(local_vars *psection)
{
	memset(psection, 0, sizeof Locals);
	copy_section(psection, &Locals);
}

/* Do a case-insensitive, whitespace-ignoring string compare. */
static int strwicmp(char *psz1, char *psz2)
{
	/* if BOTH strings are NULL, return TRUE, if ONE is NULL return */
	/* appropriate value. */
	if (psz1 == psz2)
		return 0;

	if (psz1 == NULL)
		return -1;

	if (psz2 == NULL)
		return 1;

	/* sync the strings on first non-whitespace */
	while (1) {
		while (isSpace(psz1))
			psz1++;
		while (isSpace(psz2))
			psz2++;
		if (toUpper(psz1) != toUpper(psz2) || *psz1 == '\0' || *psz2 == '\0')
			break;
		psz1++;
		psz2++;
	}
	return *psz1 - *psz2;
}

/* Find a section by name. Otherwise works like get_section. */
static int getsectionbyname(char *name)
{
	int i;

	for (i = section_list.count - 1; i >= 0; i--) {
		if (strwicmp(iSECTION(i).name, name) == 0)
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
		if (strwicmp(parm_table[iIndex].label, parmname) == 0)
			return iIndex;
	}

	rprintf(FLOG, "Unknown Parameter encountered: \"%s\"\n", parmname);
	return -1;
}

/* Set a boolean variable from the text value stored in the passed string.
 * Returns True in success, False if the passed string does not correctly
 * represent a boolean. */
static BOOL set_boolean(BOOL *pb, char *parmvalue)
{
	if (strwicmp(parmvalue, "yes") == 0
	 || strwicmp(parmvalue, "true") == 0
	 || strwicmp(parmvalue, "1") == 0)
		*pb = True;
	else if (strwicmp(parmvalue, "no") == 0
	      || strwicmp(parmvalue, "False") == 0
	      || strwicmp(parmvalue, "0") == 0)
		*pb = False;
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
	case P_BOOL:
		set_boolean(parm_ptr, parmvalue);
		break;

	case P_BOOLREV:
		set_boolean(parm_ptr, parmvalue);
		*(BOOL *)parm_ptr = ! *(BOOL *)parm_ptr;
		break;

	case P_INTEGER:
		*(int *)parm_ptr = atoi(parmvalue);
		break;

	case P_CHAR:
		*(char *)parm_ptr = *parmvalue;
		break;

	case P_OCTAL:
		sscanf(parmvalue, "%o", (int *)parm_ptr);
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
			all_vars *vp = EXPAND_ITEM_LIST(&section_stack, all_vars, 2);
			memcpy(&vp->g, &Globals, sizeof Globals);
			memcpy(&vp->l, &Locals, sizeof Locals);
		} else if (strcmp(sectionname+1, "pop") == 0
		 || strcmp(sectionname+1, "reset") == 0) {
			all_vars *vp = ((all_vars*)section_stack.items) + section_stack.count - 1;
			if (!section_stack.count)
				return False;
			memcpy(&Globals, &vp->g, sizeof Globals);
			memcpy(&Locals, &vp->l, sizeof Locals);
			if (sectionname[1] == 'p')
				section_stack.count--;
		} else
			return False;
		return True;
	}

	isglobal = strwicmp(sectionname, GLOBAL_NAME) == 0;

	/* if we were in a global section then do the local inits */
	if (bInGlobalSection && !isglobal) {
		if (!section_list.count)
			set_dparams(0);
		init_locals();
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

	init_globals();

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
