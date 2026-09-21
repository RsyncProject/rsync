/*
 * Renice or ionice the rsync process to reduce its impact on the system
 * ionice_string.c - string conversion utilities for ionice
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

#include <strings.h>
#include "niceness.h"

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

