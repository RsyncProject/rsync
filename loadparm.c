/* This is based on loadparm.c from Samba, written by Andrew Tridgell
   and Karl Auer */

/* some fixes
 *
 * Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
 */

/* 
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
 *  Load parameters.
 *
 *  This module provides suitable callback functions for the params
 *  module. It builds the internal table of service details which is
 *  then used by the rest of the server.
 *
 * To add a parameter:
 *
 * 1) add it to the global or service structure definition
 * 2) add it to the parm_table
 * 3) add it to the list of available functions (eg: using FN_GLOBAL_STRING())
 * 4) If it's a global then initialise it in init_globals. If a local
 *    (ie. service) parameter then initialise it in the sDefault structure
 *  
 *
 * Notes:
 *   The configuration file is processed sequentially for speed. It is NOT
 *   accessed randomly as happens in 'real' Windows. For this reason, there
 *   is a fair bit of sequence-dependent code here - ie., code which assumes
 *   that certain things happen before others. In particular, the code which
 *   happens at the boundary between sections is delicately poised, so be
 *   careful!
 *
 */

/* TODO: Parameter to set debug level on server. */

#include "rsync.h"
#define PTR_DIFF(p1,p2) ((ptrdiff_t)(((char *)(p1)) - (char *)(p2)))
#define strequal(a,b) (strcasecmp(a,b)==0)
#define BOOLSTR(b) ((b) ? "Yes" : "No")
typedef char pstring[1024];
#define pstrcpy(a,b) strlcpy(a,b,sizeof(pstring))

/* the following are used by loadparm for option lists */
typedef enum
{
	P_BOOL,P_BOOLREV,P_CHAR,P_INTEGER,P_OCTAL,
	P_STRING,P_GSTRING,P_ENUM,P_SEP
} parm_type;

typedef enum
{
	P_LOCAL,P_GLOBAL,P_SEPARATOR,P_NONE
} parm_class;

struct enum_list {
	int value;
	char *name;
};

struct parm_struct
{
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
#define pSERVICE(i) ServicePtrs[i]
#define iSERVICE(i) (*pSERVICE(i))
#define LP_SNUM_OK(iService) (((iService) >= 0) && ((iService) < iNumServices))

/* 
 * This structure describes global (ie., server-wide) parameters.
 */
typedef struct
{
	char *motd_file;
	char *log_file;
	char *pid_file;
	int syslog_facility;
	char *socket_options;
} global;

static global Globals;



/* 
 * This structure describes a single service. 
 */
typedef struct
{
	char *name;
	char *path;
	char *comment;
	char *lock_file;
	BOOL read_only;
	BOOL list;
	BOOL use_chroot;
	BOOL transfer_logging;
	BOOL ignore_errors;
	char *uid;
	char *gid;
	char *hosts_allow;
	char *hosts_deny;
	char *auth_users;
	char *secrets_file;
	BOOL strict_modes;
	char *exclude;
	char *exclude_from;
	char *include;
	char *include_from;
	char *log_format;
	char *refuse_options;
	char *dont_compress;
	int timeout;
	int max_connections;
	BOOL ignore_nonreadable;
} service;


/* This is a default service used to prime a services structure */
static service sDefault = 
{
	NULL,    /* name */
	NULL,    /* path */
	NULL,    /* comment */
	DEFAULT_LOCK_FILE,    /* lock file */
	True,    /* read only */
	True,    /* list */
	True,    /* use chroot */
	False,   /* transfer logging */
	False,   /* ignore errors */
	"nobody",/* uid */
	
	/* TODO: This causes problems on Debian, where it is called
	 * "nogroup".  Debian patch this in their version of the
	 * package, but it would be nice to be consistent.  Possibly
	 * other systems are different again.
	 *
	 * What is the best behaviour?  Perhaps always using (gid_t)
	 * -2? */
	"nobody",/* gid */
	
	NULL,    /* hosts allow */
	NULL,    /* hosts deny */
	NULL,    /* auth users */
	NULL,    /* secrets file */
	True,   /* strict modes */
	NULL,    /* exclude */
	NULL,    /* exclude from */
	NULL,    /* include */
	NULL,    /* include from */
	"%o %h [%a] %m (%u) %f %l",    /* log format */
	NULL,    /* refuse options */
	"*.gz *.tgz *.zip *.z *.rpm *.deb *.iso *.bz2 *.tbz",    /* dont compress */
	0,        /* timeout */
	0,        /* max connections */
	False     /* ignore nonreadable */
};



/* local variables */
static service **ServicePtrs = NULL;
static int iNumServices = 0;
static int iServiceIndex = 0;
static BOOL bInGlobalSection = True;

#define NUMPARAMETERS (sizeof(parm_table) / sizeof(struct parm_struct))

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
	{ -1, NULL }};


/* note that we do not initialise the defaults union - it is not allowed in ANSI C */
static struct parm_struct parm_table[] =
{
  {"motd file",        P_STRING,  P_GLOBAL, &Globals.motd_file,    NULL,   0},
  {"syslog facility",  P_ENUM,    P_GLOBAL, &Globals.syslog_facility, enum_facilities,0},
  {"socket options",   P_STRING,  P_GLOBAL, &Globals.socket_options,NULL,  0},
  {"log file",         P_STRING,  P_GLOBAL, &Globals.log_file,      NULL,  0},
  {"pid file",         P_STRING,  P_GLOBAL, &Globals.pid_file,      NULL,  0},

  {"timeout",          P_INTEGER, P_LOCAL,  &sDefault.timeout,     NULL,  0},
  {"max connections",  P_INTEGER, P_LOCAL,  &sDefault.max_connections,NULL, 0},
  {"name",             P_STRING,  P_LOCAL,  &sDefault.name,        NULL,   0},
  {"comment",          P_STRING,  P_LOCAL,  &sDefault.comment,     NULL,   0},
  {"lock file",        P_STRING,  P_LOCAL,  &sDefault.lock_file,   NULL,   0},
  {"path",             P_STRING,  P_LOCAL,  &sDefault.path,        NULL,   0},
  {"read only",        P_BOOL,    P_LOCAL,  &sDefault.read_only,   NULL,   0},
  {"list",             P_BOOL,    P_LOCAL,  &sDefault.list,        NULL,   0},
  {"use chroot",       P_BOOL,    P_LOCAL,  &sDefault.use_chroot,  NULL,   0},
  {"ignore nonreadable",P_BOOL,   P_LOCAL,  &sDefault.ignore_nonreadable,  NULL,   0},
  {"uid",              P_STRING,  P_LOCAL,  &sDefault.uid,         NULL,   0},
  {"gid",              P_STRING,  P_LOCAL,  &sDefault.gid,         NULL,   0},
  {"hosts allow",      P_STRING,  P_LOCAL,  &sDefault.hosts_allow, NULL,   0},
  {"hosts deny",       P_STRING,  P_LOCAL,  &sDefault.hosts_deny,  NULL,   0},
  {"auth users",       P_STRING,  P_LOCAL,  &sDefault.auth_users,  NULL,   0},
  {"secrets file",     P_STRING,  P_LOCAL,  &sDefault.secrets_file,NULL,   0},
  {"strict modes",     P_BOOL,    P_LOCAL,  &sDefault.strict_modes,NULL,   0},
  {"exclude",          P_STRING,  P_LOCAL,  &sDefault.exclude,     NULL,   0},
  {"exclude from",     P_STRING,  P_LOCAL,  &sDefault.exclude_from,NULL,   0},
  {"include",          P_STRING,  P_LOCAL,  &sDefault.include,     NULL,   0},
  {"include from",     P_STRING,  P_LOCAL,  &sDefault.include_from,NULL,   0},
  {"transfer logging", P_BOOL,    P_LOCAL,  &sDefault.transfer_logging,NULL,0},
  {"ignore errors",    P_BOOL,    P_LOCAL,  &sDefault.ignore_errors,NULL,0},
  {"log format",       P_STRING,  P_LOCAL,  &sDefault.log_format,  NULL,   0},
  {"refuse options",   P_STRING,  P_LOCAL,  &sDefault.refuse_options,NULL, 0},
  {"dont compress",    P_STRING,  P_LOCAL,  &sDefault.dont_compress,NULL,  0},
  {NULL,               P_BOOL,    P_NONE,   NULL,                  NULL,   0}
};


/***************************************************************************
Initialise the global parameter structure.
***************************************************************************/
static void init_globals(void)
{
	memset(&Globals, 0, sizeof(Globals));
#ifdef LOG_DAEMON
	Globals.syslog_facility = LOG_DAEMON;
#endif
}

/***************************************************************************
Initialise the sDefault parameter structure.
***************************************************************************/
static void init_locals(void)
{
}


/*
   In this section all the functions that are used to access the 
   parameters from the rest of the program are defined 
*/

#define FN_GLOBAL_STRING(fn_name,ptr) \
 char *fn_name(void) {return(*(char **)(ptr) ? *(char **)(ptr) : "");}
#define FN_GLOBAL_BOOL(fn_name,ptr) \
 BOOL fn_name(void) {return(*(BOOL *)(ptr));}
#define FN_GLOBAL_CHAR(fn_name,ptr) \
 char fn_name(void) {return(*(char *)(ptr));}
#define FN_GLOBAL_INTEGER(fn_name,ptr) \
 int fn_name(void) {return(*(int *)(ptr));}

#define FN_LOCAL_STRING(fn_name,val) \
 char *fn_name(int i) {return((LP_SNUM_OK(i)&&pSERVICE(i)->val)?pSERVICE(i)->val : (sDefault.val?sDefault.val:""));}
#define FN_LOCAL_BOOL(fn_name,val) \
 BOOL fn_name(int i) {return(LP_SNUM_OK(i)? pSERVICE(i)->val : sDefault.val);}
#define FN_LOCAL_CHAR(fn_name,val) \
 char fn_name(int i) {return(LP_SNUM_OK(i)? pSERVICE(i)->val : sDefault.val);}
#define FN_LOCAL_INTEGER(fn_name,val) \
 int fn_name(int i) {return(LP_SNUM_OK(i)? pSERVICE(i)->val : sDefault.val);}


FN_GLOBAL_STRING(lp_motd_file, &Globals.motd_file)
FN_GLOBAL_STRING(lp_log_file, &Globals.log_file)
FN_GLOBAL_STRING(lp_pid_file, &Globals.pid_file)
FN_GLOBAL_STRING(lp_socket_options, &Globals.socket_options)
FN_GLOBAL_INTEGER(lp_syslog_facility, &Globals.syslog_facility)

FN_LOCAL_STRING(lp_name, name)
FN_LOCAL_STRING(lp_comment, comment)
FN_LOCAL_STRING(lp_path, path)
FN_LOCAL_STRING(lp_lock_file, lock_file)
FN_LOCAL_BOOL(lp_read_only, read_only)
FN_LOCAL_BOOL(lp_list, list)
FN_LOCAL_BOOL(lp_use_chroot, use_chroot)
FN_LOCAL_BOOL(lp_transfer_logging, transfer_logging)
FN_LOCAL_BOOL(lp_ignore_errors, ignore_errors)
FN_LOCAL_BOOL(lp_ignore_nonreadable, ignore_nonreadable)
FN_LOCAL_STRING(lp_uid, uid)
FN_LOCAL_STRING(lp_gid, gid)
FN_LOCAL_STRING(lp_hosts_allow, hosts_allow)
FN_LOCAL_STRING(lp_hosts_deny, hosts_deny)
FN_LOCAL_STRING(lp_auth_users, auth_users)
FN_LOCAL_STRING(lp_secrets_file, secrets_file)
FN_LOCAL_BOOL(lp_strict_modes, strict_modes)
FN_LOCAL_STRING(lp_exclude, exclude)
FN_LOCAL_STRING(lp_exclude_from, exclude_from)
FN_LOCAL_STRING(lp_include, include)
FN_LOCAL_STRING(lp_include_from, include_from)
FN_LOCAL_STRING(lp_log_format, log_format)
FN_LOCAL_STRING(lp_refuse_options, refuse_options)
FN_LOCAL_STRING(lp_dont_compress, dont_compress)
FN_LOCAL_INTEGER(lp_timeout, timeout)
FN_LOCAL_INTEGER(lp_max_connections, max_connections)

/* local prototypes */
static int    strwicmp( char *psz1, char *psz2 );
static int    map_parameter( char *parmname);
static BOOL   set_boolean( BOOL *pb, char *parmvalue );
static int    getservicebyname(char *name, service *pserviceDest);
static void   copy_service( service *pserviceDest, 
                            service *pserviceSource);
static BOOL   do_parameter(char *parmname, char *parmvalue);
static BOOL   do_section(char *sectionname);


/***************************************************************************
initialise a service to the defaults
***************************************************************************/
static void init_service(service *pservice)
{
	memset((char *)pservice,0,sizeof(service));
	copy_service(pservice,&sDefault);
}


/**
 * Assign a copy of @p v to @p *s.  Handles NULL strings.  @p *v must
 * be initialized when this is called, either to NULL or a malloc'd
 * string.
 *
 * @fixme There is a small leak here in that sometimes the existing
 * value will be dynamically allocated, and the old copy is lost.
 * However, we can't always deallocate the old value, because in the
 * case of sDefault, it points to a static string.  It would be nice
 * to have either all-strdup'd values, or to never need to free
 * memory.
 **/
static void string_set(char **s, const char *v)
{
	if (!v) {
		*s = NULL;
		return;
	}
	*s = strdup(v);
	if (!*s)
		exit_cleanup(RERR_MALLOC);
}


/***************************************************************************
add a new service to the services array initialising it with the given 
service
***************************************************************************/
static int add_a_service(service *pservice, char *name)
{
  int i;
  service tservice;
  int num_to_alloc = iNumServices+1;

  tservice = *pservice;

  /* it might already exist */
  if (name) 
    {
      i = getservicebyname(name,NULL);
      if (i >= 0)
	return(i);
    }

  i = iNumServices;

  ServicePtrs = realloc_array(ServicePtrs, service *, num_to_alloc);

  if (ServicePtrs)
	  pSERVICE(iNumServices) = new(service);

  if (!ServicePtrs || !pSERVICE(iNumServices))
	  return(-1);

  iNumServices++;

  init_service(pSERVICE(i));
  copy_service(pSERVICE(i),&tservice);
  if (name)
    string_set(&iSERVICE(i).name,name);  

  return(i);
}

/***************************************************************************
Do a case-insensitive, whitespace-ignoring string compare.
***************************************************************************/
static int strwicmp(char *psz1, char *psz2)
{
   /* if BOTH strings are NULL, return TRUE, if ONE is NULL return */
   /* appropriate value. */
   if (psz1 == psz2)
      return (0);
   else
      if (psz1 == NULL)
         return (-1);
      else
          if (psz2 == NULL)
              return (1);

   /* sync the strings on first non-whitespace */
   while (1)
   {
      while (isspace(* (unsigned char *) psz1))
         psz1++;
      while (isspace(* (unsigned char *) psz2))
         psz2++;
      if (toupper(* (unsigned char *) psz1) != toupper(* (unsigned char *) psz2)
	  || *psz1 == '\0' || *psz2 == '\0')
         break;
      psz1++;
      psz2++;
   }
   return (*psz1 - *psz2);
}

/***************************************************************************
Map a parameter's string representation to something we can use. 
Returns False if the parameter string is not recognised, else TRUE.
***************************************************************************/
static int map_parameter(char *parmname)
{
   int iIndex;

   if (*parmname == '-')
     return(-1);

   for (iIndex = 0; parm_table[iIndex].label; iIndex++) 
      if (strwicmp(parm_table[iIndex].label, parmname) == 0)
         return(iIndex);

   rprintf(FERROR, "Unknown Parameter encountered: \"%s\"\n", parmname);
   return(-1);
}


/***************************************************************************
Set a boolean variable from the text value stored in the passed string.
Returns True in success, False if the passed string does not correctly 
represent a boolean.
***************************************************************************/
static BOOL set_boolean(BOOL *pb, char *parmvalue)
{
   BOOL bRetval;

   bRetval = True;
   if (strwicmp(parmvalue, "yes") == 0 ||
       strwicmp(parmvalue, "true") == 0 ||
       strwicmp(parmvalue, "1") == 0)
      *pb = True;
   else
      if (strwicmp(parmvalue, "no") == 0 ||
          strwicmp(parmvalue, "False") == 0 ||
          strwicmp(parmvalue, "0") == 0)
         *pb = False;
      else
      {
         rprintf(FERROR, "Badly formed boolean in configuration file: \"%s\".\n",
               parmvalue);
         bRetval = False;
      }
   return (bRetval);
}

/***************************************************************************
Find a service by name. Otherwise works like get_service.
***************************************************************************/
static int getservicebyname(char *name, service *pserviceDest)
{
   int iService;

   for (iService = iNumServices - 1; iService >= 0; iService--)
      if (strwicmp(iSERVICE(iService).name, name) == 0) 
      {
         if (pserviceDest != NULL)
	   copy_service(pserviceDest, pSERVICE(iService));
         break;
      }

   return (iService);
}



/***************************************************************************
Copy a service structure to another

***************************************************************************/
static void copy_service(service *pserviceDest, 
                         service *pserviceSource)
{
  int i;

  for (i=0;parm_table[i].label;i++)
    if (parm_table[i].ptr && parm_table[i].class == P_LOCAL) {
	void *def_ptr = parm_table[i].ptr;
	void *src_ptr = 
	  ((char *)pserviceSource) + PTR_DIFF(def_ptr,&sDefault);
	void *dest_ptr = 
	  ((char *)pserviceDest) + PTR_DIFF(def_ptr,&sDefault);

	switch (parm_table[i].type)
	  {
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

	  case P_STRING:
	    string_set(dest_ptr,*(char **)src_ptr);
	    break;

	  default:
	    break;
	  }
      }
}


/***************************************************************************
Process a parameter for a particular service number. If snum < 0
then assume we are in the globals
***************************************************************************/
static BOOL lp_do_parameter(int snum, char *parmname, char *parmvalue)
{
   int parmnum, i;
   void *parm_ptr=NULL; /* where we are going to store the result */
   void *def_ptr=NULL;

   parmnum = map_parameter(parmname);

   if (parmnum < 0)
     {
       rprintf(FERROR, "IGNORING unknown parameter \"%s\"\n", parmname);
       return(True);
     }

   def_ptr = parm_table[parmnum].ptr;

   /* we might point at a service, the default service or a global */
   if (snum < 0) {
     parm_ptr = def_ptr;
   } else {
       if (parm_table[parmnum].class == P_GLOBAL) {
	   rprintf(FERROR, "Global parameter %s found in service section!\n",parmname);
	   return(True);
	 }
       parm_ptr = ((char *)pSERVICE(snum)) + PTR_DIFF(def_ptr,&sDefault);
   }

   /* now switch on the type of variable it is */
   switch (parm_table[parmnum].type)
     {
     case P_BOOL:
       set_boolean(parm_ptr,parmvalue);
       break;

     case P_BOOLREV:
       set_boolean(parm_ptr,parmvalue);
       *(BOOL *)parm_ptr = ! *(BOOL *)parm_ptr;
       break;

     case P_INTEGER:
       *(int *)parm_ptr = atoi(parmvalue);
       break;

     case P_CHAR:
       *(char *)parm_ptr = *parmvalue;
       break;

     case P_OCTAL:
       sscanf(parmvalue,"%o",(int *)parm_ptr);
       break;

     case P_STRING:
       string_set(parm_ptr,parmvalue);
       break;

     case P_GSTRING:
       strlcpy((char *)parm_ptr,parmvalue,sizeof(pstring));
       break;

     case P_ENUM:
	     for (i=0;parm_table[parmnum].enum_list[i].name;i++) {
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
     case P_SEP:
	     break;
     }

   return(True);
}

/***************************************************************************
Process a parameter.
***************************************************************************/
static BOOL do_parameter(char *parmname, char *parmvalue)
{
   return lp_do_parameter(bInGlobalSection?-2:iServiceIndex, parmname, parmvalue);
}

/***************************************************************************
Process a new section (service). At this stage all sections are services.
Later we'll have special sections that permit server parameters to be set.
Returns True on success, False on failure.
***************************************************************************/
static BOOL do_section(char *sectionname)
{
   BOOL bRetval;
   BOOL isglobal = (strwicmp(sectionname, GLOBAL_NAME) == 0);
   bRetval = False;

   /* if we were in a global section then do the local inits */
   if (bInGlobalSection && !isglobal)
     init_locals();

   /* if we've just struck a global section, note the fact. */
   bInGlobalSection = isglobal;   

   /* check for multiple global sections */
   if (bInGlobalSection)
   {
     return(True);
   }

   /* if we have a current service, tidy it up before moving on */
   bRetval = True;

   if (iServiceIndex >= 0)
     bRetval = True;

   /* if all is still well, move to the next record in the services array */
   if (bRetval)
     {
       /* We put this here to avoid an odd message order if messages are */
       /* issued by the post-processing of a previous section. */

       if ((iServiceIndex=add_a_service(&sDefault,sectionname)) < 0)
	 {
	   rprintf(FERROR,"Failed to add a new service\n");
	   return(False);
	 }
     }

   return (bRetval);
}


/***************************************************************************
Load the services array from the services file. Return True on success, 
False on failure.
***************************************************************************/
BOOL lp_load(char *pszFname, int globals_only)
{
	extern int am_server;
	extern int am_daemon;
	extern int am_root;
	pstring n2;
	BOOL bRetval;
 
	bRetval = False;

	bInGlobalSection = True;
  
	init_globals();

	if (pszFname)
	    pstrcpy(n2,pszFname);
	else if (am_server && am_daemon && !am_root)
	    pstrcpy(n2,RSYNCD_USERCONF);
	else
	    pstrcpy(n2,RSYNCD_SYSCONF);

	/* We get sections first, so have to start 'behind' to make up */
	iServiceIndex = -1;
	bRetval = pm_process(n2, globals_only?NULL:do_section, do_parameter);
  
	return (bRetval);
}


/***************************************************************************
return the max number of services
***************************************************************************/
int lp_numservices(void)
{
  return(iNumServices);
}

/***************************************************************************
Return the number of the service with the given name, or -1 if it doesn't
exist. Note that this is a DIFFERENT ANIMAL from the internal function
getservicebyname()! This works ONLY if all services have been loaded, and
does not copy the found service.
***************************************************************************/
int lp_number(char *name)
{
   int iService;

   for (iService = iNumServices - 1; iService >= 0; iService--)
      if (strequal(lp_name(iService), name)) 
         break;

   return (iService);
}

