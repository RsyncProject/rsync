/* 
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/**
 * @file compat.c
 *
 * Compatibility routines for older rsync protocol versions.
 **/

#include "rsync.h"

extern int am_server;

extern int preserve_links;
extern int preserve_perms;
extern int preserve_devices;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_times;
extern int always_checksum;
extern int checksum_seed;


extern int remote_version;
extern int verbose;

extern int read_batch;  /* dw */
extern int write_batch;  /* dw */

void setup_protocol(int f_out,int f_in)
{
	if (remote_version == 0) {
		if (am_server) {
			remote_version = read_int(f_in);
			write_int(f_out,PROTOCOL_VERSION);
		} else {
			write_int(f_out,PROTOCOL_VERSION);
			remote_version = read_int(f_in);
		}
	}

	if (remote_version < MIN_PROTOCOL_VERSION ||
	    remote_version > MAX_PROTOCOL_VERSION) {
		rprintf(FERROR,"protocol version mismatch - is your shell clean?\n");
		rprintf(FERROR,"(see the rsync man page for an explanation)\n");
		exit_cleanup(RERR_PROTOCOL);
	}	
	
	if (remote_version >= 12) {
		if (am_server) {
		    if (read_batch || write_batch) /* dw */
			checksum_seed = 32761;
		    else
			checksum_seed = time(NULL);
			write_int(f_out,checksum_seed);
		} else {
			checksum_seed = read_int(f_in);
		}
	}
	
	checksum_init();
}

