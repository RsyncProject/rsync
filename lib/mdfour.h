/* 
 * Unix SMB/Netbios implementation.
 * Version 1.9.
 * An implementation of MD4 designed for use in the SMB authentication protocol.
 *
 * Copyright (C) 1997-1998 Andrew Tridgell
 * 
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
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

struct mdfour {
	uint32 A, B, C, D;
	uint32 totalN;          /* bit count, lower 32 bits */
	uint32 totalN2;         /* bit count, upper 32 bits */
};

void mdfour_begin(struct mdfour *md);
void mdfour_update(struct mdfour *md, unsigned char *in, uint32 n);
void mdfour_result(struct mdfour *md, unsigned char *out);
void mdfour(unsigned char *out, unsigned char *in, int n);
