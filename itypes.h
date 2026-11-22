/* Inline functions for rsync.
 *
 * Copyright (C) 2007-2022 Wayne Davison
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

/* These predicates are for rsync syntax, not localised text. */
static inline int
ascii_is_digit(int ch)
{
	return ch >= '0' && ch <= '9';
}

static inline int
ascii_is_space(int ch)
{
	return ch == ' ' || (ch >= '\t' && ch <= '\r');
}

static inline int
ascii_lower(int ch)
{
	return ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch;
}

static inline int
ascii_strncasecmp(const char *left, const char *right, size_t len)
{
	while (len) {
		int a = ascii_lower((uchar)*left++);
		int b = ascii_lower((uchar)*right++);
		if (a != b || !a)
			return a - b;
		len--;
	}
	return 0;
}

static inline int
ascii_strcasecmp(const char *left, const char *right)
{
	return ascii_strncasecmp(left, right, SIZE_MAX);
}

static inline int
isDigit(const char *ptr)
{
	return isdigit(*(unsigned char *)ptr);
}

static inline int
isHexDigit(const char *ptr)
{
	return isxdigit(*(unsigned char *)ptr);
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
isAlNum(const char *ptr)
{
	return isalnum(*(unsigned char *)ptr);
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
