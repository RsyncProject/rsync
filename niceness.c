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

int get_renice_default_prio()
{
	return 19; // lowest CPU Priority
}

void renice_me(int prio)
{
#ifdef SUPPORT_RENICE
	int which = PRIO_PROCESS; // who specifies a Process ID
	int who = 0; // 0 means current process
	int result = setpriority(which, who, prio);
	if ( result < 0 ) {
		// Failed to set priority, inform user, but can be ignored (it's just not so nice).
		rprintf(FWARNING, "renice to %d rejected by OS (%s version %s): %s\n",
			prio, RSYNC_NAME, rsync_version(), strerror(errno));
	} else {
		if (DEBUG_GTE(CMD, 1))
				rprintf(FINFO, "successfully reniced %s to new priority %d\n", am_server ? "server" : "client", prio);
	}
#else
	rprintf(FWARNING, "renice not supported for %s (%s: %s version %s)\n",
			COMPILE_TARGET, am_server ? "server" : "client", RSYNC_NAME, rsync_version());
#endif
}

void ionice_me() 
{
#ifdef SUPPORT_IONICE
	int which = IOPRIO_WHO_PROCESS; // who specifies a Process ID
	int who = 0; // 0 means current process
	int class = IOPRIO_CLASS_IDLE;
	int data = 0; // Ignored when using the IOPRIO_CLASS_IDLE class
	int ioprio = IOPRIO_PRIO_VALUE(class, data);
	int result = syscall(SYS_ioprio_set, which, who, ioprio);
	if ( result < 0 ) {
		// Failed to set priority, inform user, but can be ignored (it's just not so ionice).
		rprintf(FWARNING, "ionice rejected by OS (%s version %s)\n",
			RSYNC_NAME, rsync_version());
	} else {
		if (DEBUG_GTE(CMD, 1))
			rprintf(FINFO, "successfully ioniced %s\n", am_server ? "server" : "client");
	}
#else
	rprintf(FWARNING, "ionice not supported for %s (%s: %s version %s)\n",
			COMPILE_TARGET, am_server ? "server" : "client", RSYNC_NAME, rsync_version());
#endif
}
