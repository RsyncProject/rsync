/*
 * End-of-run cleanup helper code used by cleanup.c.
 *
 * Copyright (C) 2006 Wayne Davison
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

/* This is included by cleanup.c multiple times, once for every segement in
 * the _exit_cleanup() code.  This produces the next "case N:" statement in
 * sequence and increments the cleanup_step variable by 1.  This ensures that
 * our case statements never get out of whack due to added/removed steps. */

#if !defined EXIT_CLEANUP_CASE_0
#define EXIT_CLEANUP_CASE_0
	case 0:
#elif !defined EXIT_CLEANUP_CASE_1
#define EXIT_CLEANUP_CASE_1
	case 1:
#elif !defined EXIT_CLEANUP_CASE_2
#define EXIT_CLEANUP_CASE_2
	case 2:
#elif !defined EXIT_CLEANUP_CASE_3
#define EXIT_CLEANUP_CASE_3
	case 3:
#elif !defined EXIT_CLEANUP_CASE_4
#define EXIT_CLEANUP_CASE_4
	case 4:
#elif !defined EXIT_CLEANUP_CASE_5
#define EXIT_CLEANUP_CASE_5
	case 5:
#elif !defined EXIT_CLEANUP_CASE_6
#define EXIT_CLEANUP_CASE_6
	case 6:
#elif !defined EXIT_CLEANUP_CASE_7
#define EXIT_CLEANUP_CASE_7
	case 7:
#elif !defined EXIT_CLEANUP_CASE_8
#define EXIT_CLEANUP_CASE_8
	case 8:
#elif !defined EXIT_CLEANUP_CASE_9
#define EXIT_CLEANUP_CASE_9
	case 9:
#elif !defined EXIT_CLEANUP_CASE_10
#define EXIT_CLEANUP_CASE_10
	case 10:
#elif !defined EXIT_CLEANUP_CASE_11
#define EXIT_CLEANUP_CASE_11
	case 11:
#elif !defined EXIT_CLEANUP_CASE_12
#define EXIT_CLEANUP_CASE_12
	case 12:
#elif !defined EXIT_CLEANUP_CASE_13
#define EXIT_CLEANUP_CASE_13
	case 13:
#elif !defined EXIT_CLEANUP_CASE_14
#define EXIT_CLEANUP_CASE_14
	case 14:
#elif !defined EXIT_CLEANUP_CASE_15
#define EXIT_CLEANUP_CASE_15
	case 15:
#elif !defined EXIT_CLEANUP_CASE_16
#define EXIT_CLEANUP_CASE_16
	case 16:
#else
#error Need to add more case statements!
#endif
		cleanup_step++;
