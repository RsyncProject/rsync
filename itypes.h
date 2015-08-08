/* Inline functions for rsync.
 *
 * Copyright (C) 2007-2015 Wayne Davison
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

static inline int
isDigit(const char *ptr)
{
	return isdigit(*(unsigned char *)ptr);
}

static inline int
isPrint(const char *ptr)
{
	return isprint(*(unsigned char *)ptr);
}

static inline int
isSpace(const char *ptr)
{
	return isspace(*(unsigned char *)ptr);
}

static inline int
isLower(const char *ptr)
{
	return islower(*(unsigned char *)ptr);
}

static inline int
isUpper(const char *ptr)
{
	return isupper(*(unsigned char *)ptr);
}

static inline int
toLower(const char *ptr)
{
	return tolower(*(unsigned char *)ptr);
}

static inline int
toUpper(const char *ptr)
{
	return toupper(*(unsigned char *)ptr);
}
