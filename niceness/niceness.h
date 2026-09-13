/*
 * Renice or ionice the rsync process to reduce its impact on the system
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

#ifndef NICENESS_H // Ensure that this header file is never included more than once, thus avoiding errors with duplicate definitions
#define NICENESS_H 1

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
extern const char *Ionice_ValueStrings[];
/*
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
*/

/*
 * Constants to be used when no specific argument is given
 */
#define NICENESS_RENICE_DEFAULT_PRIO 19;
#define NICENESS_IONICE_DEFAULT_PRIO IDLE;

/*
 * Struct to hold state for niceness
 */
struct niceness {
	/* If set to other than 0, be nice on the local or remote site
	* Contains the nice priority to be set on the process, or 0=feature is turned off, no priority will be set.
	* I have to admit, that it is not possible to set a nice value of 0 with this code,
	* but in most cases 0 should be the priority of the newly started rsync process already anyway.
	*/
	int nice_local;
	int nice_remote;

	/* If set to other than 0, be ionice on the local or remote site
	* Currently only idle is supported for ionice when turned on.
	*/
	int ionice_local;
	int ionice_remote;
};

extern struct niceness niceness;


/*
 * Some methods to simply turn niceness on or off or configure with an argument
 */
void niceness_turn_off();
void niceness_turn_on();
int niceness_parse_argument(const char * arg);

/*
 * Apply nice and ionice on this local process
 */
void niceness_renice_and_ionice_me();
void niceness_renice_me(int prio);
void niceness_ionice_me(int ionice_value);

/*
 * Utility function to get the string representation of an ionice value
 */
const char *intToIoniceValueString(int ionice_value);

#endif // ifndef NICENESS_H
