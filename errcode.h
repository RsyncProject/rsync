/* -*- c-file-style: "linux"; -*-
   
   Copyright (C) 1998-2000 by Andrew Tridgell
   
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

/*
 * error codes returned by rsync.  If you change these, please also update the
 * string mappings in log.c
 */

#define RERR_SYNTAX     1       /* syntax or usage error */
#define RERR_PROTOCOL   2       /* protocol incompatibility */
#define RERR_FILESELECT 3       /* errors selecting input/output files, dirs */
#define RERR_UNSUPPORTED 4       /* requested action not supported */

#define RERR_SOCKETIO   10      /* error in socket IO */
#define RERR_FILEIO     11      /* error in file IO */
#define RERR_STREAMIO   12      /* error in rsync protocol data stream */
#define RERR_MESSAGEIO  13      /* errors with program diagnostics */
#define RERR_IPC        14      /* error in IPC code */

#define RERR_SIGNAL     20      /* status returned when sent SIGUSR1, SIGINT */
#define RERR_WAITCHILD  21      /* some error returned by waitpid() */
#define RERR_MALLOC     22      /* error allocating core memory buffers */

#define RERR_TIMEOUT    30      /* timeout in data send/receive */
