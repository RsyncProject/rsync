/*
 * Renice or ionice the rsync process to reduce its impact on the system
 * niceness_syscall.c - renice/ionice system call
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

void niceness_renice_me(int prio)
{
#ifdef SUPPORT_RENICE
	int which = PRIO_PROCESS; // who specifies a Process ID
	int who = 0; // 0 means current process
	int result = setpriority(which, who, prio);
	if ( result < 0 ) {
		// Failed to set priority, inform user, but can be ignored (it's just not so nice).
		rprintf(FWARNING, "renice %s to new priority %d failed (%s version %s): %s\n",
			client_or_server_string(), prio, RSYNC_NAME, rsync_version(), strerror(errno));
	} else {
		if (DEBUG_GTE(CMD, 1))
				rprintf(FINFO, "successfully reniced %s to new priority %d\n", client_or_server_string(), prio);
	}
#else
	rprintf(FWARNING, "renice %s to new priority %d failed (%s version %s): renice not supported for %s\n",
		client_or_server_string(), prio, RSYNC_NAME, rsync_version(), COMPILE_TARGET);
#endif
}

void niceness_ionice_me(int ionice_value)
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
			client_or_server_string(), ionice_string, RSYNC_NAME, rsync_version(), strerror(errno));
	} else {
		if (DEBUG_GTE(CMD, 1))
			rprintf(FINFO, "successfully ioniced %s to new priority %s\n", client_or_server_string(), ionice_string);
	}
#else
	rprintf(FWARNING, "ionice %s to new priority %s failed (%s version %s): ionice not supported for %s\n",
			client_or_server_string(), ionice_string, RSYNC_NAME, rsync_version(), COMPILE_TARGET);
#endif
}
