/* Inline functions for rsync.
 *
 * Copyright (C) 2008-2015 Wayne Davison
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

static inline char *
big_num(int64 num)
{
	return do_big_num(num, 0, NULL);
}

static inline char *
comma_num(int64 num)
{
	extern int human_readable;
	return do_big_num(num, human_readable != 0, NULL);
}

static inline char *
human_num(int64 num)
{
	extern int human_readable;
	return do_big_num(num, human_readable, NULL);
}

static inline char *
big_dnum(double dnum, int decimal_digits)
{
	return do_big_dnum(dnum, 0, decimal_digits);
}

static inline char *
comma_dnum(double dnum, int decimal_digits)
{
	extern int human_readable;
	return do_big_dnum(dnum, human_readable != 0, decimal_digits);
}

static inline char *
human_dnum(double dnum, int decimal_digits)
{
	extern int human_readable;
	return do_big_dnum(dnum, human_readable, decimal_digits);
}
