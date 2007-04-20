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
read_varint30(int f)
{
	if (protocol_version < 30)
		return read_int(f);
	return read_varint(f);
}

static inline int64
read_varlong30(int f, uchar min_bytes)
{
	if (protocol_version < 30)
		return read_longint(f);
	return read_varlong(f, min_bytes);
}

static inline void
write_varint30(int f, int32 x)
{
	if (protocol_version < 30)
		write_int(f, x);
	else
		write_varint(f, x);
}

static inline void
write_varlong30(int f, int64 x, uchar min_bytes)
{
	if (protocol_version < 30)
		write_longint(f, x);
	else
		write_varlong(f, x, min_bytes);
}
