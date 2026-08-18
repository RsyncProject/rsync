/*
 * Renice or ionice the rsync process to reduce its impact on the system
 * niceness.c - core niceness state holder
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

#include "niceness.h"

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

struct niceness niceness = {
	/* If set to other than 0, be nice on the local or remote site
	 * Contains the nice priority to be set on the process, or 0=feature is turned off, no priority will be set.
	 * I have to admit, that it is not possible to set a nice value of 0 with this code,
	 * but in most cases 0 should be the priority of the newly started rsync process already anyway.
	 */
	.nice_local = 0,
	.nice_remote = 0,

	/* If set to other than 0, be ionice on the local or remote site
	 * Currently only idle is supported for ionice when turned on.
	 */
	.ionice_local = 0,
	.ionice_remote = 0,
};

/**
 * Turn niceness off (no nice and no ionice)
 */
void niceness_turn_off()
{
	niceness.nice_local = 0;
	niceness.nice_remote = 0;
	niceness.ionice_local = 0;
	niceness.ionice_remote = 0;
}

/**
 * Turn niceness on (nice and ionice set to default values for maximum niceness)
 */
void niceness_turn_on()
{
	niceness.nice_local = NICENESS_RENICE_DEFAULT_PRIO;
	niceness.nice_remote = NICENESS_RENICE_DEFAULT_PRIO;
	niceness.ionice_local = NICENESS_IONICE_DEFAULT_PRIO;
	niceness.ionice_remote = NICENESS_IONICE_DEFAULT_PRIO;
}

/**
 * Apply nice/ionice local to this rsync process
 */
void niceness_renice_and_ionice_me()
{
	if (niceness.nice_local) {
		niceness_renice_me(niceness.nice_local);
	}

	if (niceness.ionice_local) {
		niceness_ionice_me(niceness.ionice_local);
	}
}
