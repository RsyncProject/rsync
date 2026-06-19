/*
 * Renice or ionice the current process to reduce its impact on the system
 *
 * Copyright (C) 2026 Michael Mess
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

/*
 * Enum for ionice values. 
 * Constants with negative numbers need root permission, others can be set by any user.
 * RT_X is realtime priority
 * BE_X is best effort with the given level
 * IDLE means the process gets served only when no other processes are using disk io.
 * NONE means best effort with level calculated by the formula (cpu_nice + 20) / 5
 *      and is the default that does not need to be set.
 */
enum Ionice_Values {
    RT_0 =-8,
    RT_1 =-7,
    RT_2 =-6,
    RT_3 =-5,
    RT_4 =-4,
    RT_5 =-3,
    RT_6 =-2,
    RT_7 =-1,
    NONE = 0,
    BE_0 = 1,
    BE_1 = 2,
    BE_2 = 3,
    BE_3 = 4,
    BE_4 = 5,
    BE_5 = 6,
    BE_6 = 7,
    BE_7 = 8,
    IDLE = 9
};

/*
 * String representation of ionice. All strings have to be 4 characters long. 
 */
const char *Ionice_ValueStrings[] = {
	"rt_0",
	"rt_1",
	"rt_2",
	"rt_3",
	"rt_4",
	"rt_5",
	"rt_6",
	"rt_7",
	"none",
	"be_0",
	"be_1",
	"be_2",
	"be_3",
	"be_4",
	"be_5",
	"be_6",
	"be_7",
	"idle",
	0
};

/*
 * Get the string representation for the given ionice_value
 */
const char *intToIoniceValueString(int ionice_value) 
{
	if (ionice_value<-8) return 0;
	if (ionice_value>9) return 0;
	return Ionice_ValueStrings[ionice_value+8];
}

/*
 * Parse string into ionice_value. Returns 1 on success, 0 otherwise. 
 */
int ioniceStringToInt(char * string, int *ionice_value)
{
	for (int i=0; Ionice_ValueStrings[i]; i++)
	{
		if (strncasecmp(string, Ionice_ValueStrings[i], 4) == 0) // All values are 4 chars long 
		{
			*ionice_value=i-8;
			return 1;
		}
	}
	return 0;
}

/*
 * Try to parse the location and set the variables isLocal and isRemote.
 * Return the pointer to the string after the found string. 
 */
char *parse_location(char *str, int *isLocal, int *isRemote) 
{
	if (strncasecmp(str, "local", 5) == 0) 
	{
		*isLocal=1;
		*isRemote=0;
		return str+5;
	}
	if (strncasecmp(str, "remote", 6) == 0) 
	{
		*isLocal=0;
		*isRemote=1;
		return str+6;
	}
	if (strncasecmp(str, "all", 3) == 0) 
	{
		*isLocal=1;
		*isRemote=1;
		return str+3;
	}
		*isLocal=0;
		*isRemote=0;
	return str;
}

char *parse_nice_value(char *str, int *nice_value)
{
	if (!str || !str[0]) return 0; // ERROR
	char *pointer = str;
	long nice_long = strtol(str, &pointer, 10);
	if (nice_long<-20 || nice_long>20) return 0;
	*nice_value=(int)nice_long;
	return pointer;
}

char *parse_ionice_value(char *str, int *ionice_value)
{
	if (ioniceStringToInt(str, ionice_value)) {
		return str+4;
	}
	*ionice_value=0;
	return str;
}

char *parse_nice_and_ionice_values(char *str, int *nice_value, int *ionice_value)
{
	char *pointer = parse_ionice_value(str, ionice_value);
	if (pointer != str) // ionice value has been given standalone, no nice
	{
		*nice_value=0;
		return pointer;
	}
	pointer = parse_nice_value(pointer, nice_value);
	if (pointer == 0) return 0; // ERROR
	if (pointer[0] == '/') // nice/ionice value
	{
		pointer++;
		pointer = parse_ionice_value(pointer, ionice_value);
	}
	return pointer;
}

/*
 * parse the configuration string and return a pointer to the still not processed remainder of the configuration string
 */ 
char *parse_setting(char *config_str, int *nice_local, int *ionice_local, int *nice_remote, int *ionice_remote) 
{
	int isLocal=0;
	int isRemote=0;
	char *pointer=parse_location(config_str, &isLocal, &isRemote);
	if (isLocal || isRemote) 
	{   /* Location "local" or "remote" or "all" found */
		if (pointer[0] == ':') 
		{   /* Location followed by specific setting */
			pointer++; /* Skip colon */
			/* Read nice/ionice value */ 
			int nice_value=0;
			int ionice_value=0;
			pointer = parse_nice_and_ionice_values(pointer, &nice_value, &ionice_value);
			if (isLocal) {
				*nice_local=nice_value;
				*ionice_local=ionice_value;
			}
			if (isRemote) {
				*nice_remote=nice_value;
				*ionice_remote=ionice_value;
			}
			return pointer;
		} else { 
			if (!pointer[0] || pointer[0] == ',') 
			{   /* End of string or comma */
				/* Location without specific setting, use default */
                int nice_default=get_renice_default_prio();
                int ionice_default=get_ionice_default_prio();
				if (isLocal) {
					*nice_local=nice_default;
					*ionice_local=ionice_default;
				}
				if (isRemote) {
					*nice_remote=nice_default;
					*ionice_remote=ionice_default;
				}
				return pointer;
			} else 
			{ /* Unexpected characters */
				return 0; // ERROR
			}
		}
	} else {
		/* Read nice/ionice value */ 
			int nice_value=0;
			int ionice_value=0;
			pointer = parse_nice_and_ionice_values(pointer, &nice_value, &ionice_value);
			*nice_local=nice_value;
			*ionice_local=ionice_value;
			*nice_remote=nice_value;
			*ionice_remote=ionice_value;
			return pointer;
	}
	return 0; // ERROR
}

int parse_nice(const char *config_str, int *nice_local, int *ionice_local, int *nice_remote, int *ionice_remote) 
{
	char *str = (char *) config_str;
	while (str && str[0]) { // string is valid and not empty
		str=parse_setting(str, nice_local, ionice_local, nice_remote, ionice_remote);
		if (!str) return 0; // ERROR returned by parser
		if (!str[0]) return 1; // End of string - everything has been parsed - finished successfully!
		if (str[0] != ',') return 0; // ERROR: Unexpected character, we expect a comma here as separator.
		str++;
	}
	return 0; // string was empty or not valid (maybe even after a comma)
}

int get_renice_default_prio()
{
	return 19; // lowest CPU Priority
}

int get_ionice_default_prio()
{
	return IDLE; // lowest IO Priority
}

void renice_me(int prio)
{
#ifdef SUPPORT_RENICE
	int which = PRIO_PROCESS; // who specifies a Process ID
	int who = 0; // 0 means current process
	int result = setpriority(which, who, prio);
	if ( result < 0 ) {
		// Failed to set priority, inform user, but can be ignored (it's just not so nice).
		rprintf(FWARNING, "renice %s to new priority %d failed (%s version %s): %s\n",
			am_server ? "server" : "client", prio, RSYNC_NAME, rsync_version(), strerror(errno));
	} else {
		if (DEBUG_GTE(CMD, 1))
				rprintf(FINFO, "successfully reniced %s to new priority %d\n", am_server ? "server" : "client", prio);
	}
#else
	rprintf(FWARNING, "renice %s to new priority %d failed (%s version %s): renice not supported for %s\n",
		am_server ? "server" : "client", prio, RSYNC_NAME, rsync_version(), COMPILE_TARGET);
#endif
}

void ionice_me(int ionice_value) 
{
	const char *ionice_string = intToIoniceValueString(ionice_value);
#ifdef SUPPORT_IONICE
	int which = IOPRIO_WHO_PROCESS; // who specifies a Process ID
	int who = 0; // 0 means current process
	int class;
	int data; // Ignored when using the IOPRIO_CLASS_IDLE or IOPRIO_CLASS_NONE class
	switch (ionice_string[0])
	{
		case 'r': // Realtime
			class = IOPRIO_CLASS_RT;
			data = ionice_string[3]-'0';  // rt_X: X -> data
			break;
		case 'b': // Best effort
			class = IOPRIO_CLASS_BE;
			data = ionice_string[3]-'0';  // be_X: X -> data
			break;
		case 'i': // Idle
			class = IOPRIO_CLASS_IDLE;
			data = 0;
			break;
		case 'n': // None
		default:
			class = IOPRIO_CLASS_NONE;
			data = 0;
			break;
	}
	int ioprio = IOPRIO_PRIO_VALUE(class, data);
	int result = syscall(SYS_ioprio_set, which, who, ioprio);
	if ( result < 0 ) {
		// Failed to set priority, inform user, but can be ignored (it's just not so ionice).
		rprintf(FWARNING, "ionice %s to new priority %s failed (%s version %s): %s\n",
			am_server ? "server" : "client", ionice_string, RSYNC_NAME, rsync_version(), strerror(errno));
	} else {
		if (DEBUG_GTE(CMD, 1))
			rprintf(FINFO, "successfully ioniced %s to new priority %s\n", am_server ? "server" : "client", ionice_string);
	}
#else
	rprintf(FWARNING, "ionice %s to new priority %s failed (%s version %s): ionice not supported for %s\n",
			am_server ? "server" : "client", ionice_string, RSYNC_NAME, rsync_version(), COMPILE_TARGET);
#endif
}
