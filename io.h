/*
 * Copyright (C) 2007 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

extern int protocol_version;

static inline int32
read_abbrevint30(int f)
{
	if (protocol_version < 30)
		return read_int(f);
	return read_abbrevint(f);
}

static inline void
write_abbrevint30(int f, int32 x)
{
	if (protocol_version < 30)
		write_int(f, x);
	else
		write_abbrevint(f, x);
}
