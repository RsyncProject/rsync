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

void renice_me() 
{
#ifdef SUPPORT_RENICE
	int which = PRIO_PROCESS; // who specifies a Process ID
	int who = 0; // 0 means current process
	int prio = 19; // lowest CPU Priority
	int result = setpriority(which, who, prio);
	if ( result < 0 ) {
		// Failed to set priority, TODO: log or inform user, but can be ignored (it's just not so nice).
	}
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
		// Failed to set priority, TODO: log or inform user, but can be ignored (it's just not so ionice).
	}
#endif
}
