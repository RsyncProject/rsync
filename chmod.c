/*
 * Implement the core of the --chmod option.
 *
 * Copyright (C) 2002 Scott Howard
 * Copyright (C) 2005-2020 Wayne Davison
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

#include "rsync.h"
#include "itypes.h"

extern mode_t orig_umask;

#define FLAG_X_KEEP (1<<0)
#define FLAG_DIRS_ONLY (1<<1)
#define FLAG_FILES_ONLY (1<<2)

struct chmod_mode_struct {
	struct chmod_mode_struct *next;
	int ModeAND, ModeOR, ModeCOPY_SRC, ModeCOPY_DST, ModeCOPY_AND, ModeOP;
	char flags;
};

#define CHMOD_ADD 1
#define CHMOD_SUB 2
#define CHMOD_EQ  3
#define CHMOD_SET 4

#define STATE_ERROR 0
#define STATE_1ST_HALF 1
#define STATE_2ND_HALF 2
#define STATE_OCTAL_NUM 3

static int mode_dest_special_bits(int where)
{
	int bits = 0;

	if (where & 0100)
		bits |= S_ISUID;
	if (where & 0010)
		bits |= S_ISGID;
	if (where & 0001)
		bits |= S_ISVTX;

	return bits;
}

/* Parse a chmod-style argument, and break it down into one or more AND/OR
 * pairs in a linked list.  We return a pointer to new items on success
 * (appending the items to the specified list), or NULL on error. */
struct chmod_mode_struct *parse_chmod(const char *modestr,
				      struct chmod_mode_struct **root_mode_ptr)
{
	int state = STATE_1ST_HALF;
	int where = 0, what = 0, op = 0, topbits = 0, topoct = 0, flags = 0, copybits = 0;
	struct chmod_mode_struct *first_mode = NULL, *curr_mode = NULL,
				 *prev_mode = NULL;

	while (state != STATE_ERROR) {
		if (!*modestr || *modestr == ',') {
			int bits, where_specified;

			if (!op) {
				state = STATE_ERROR;
				break;
			}
			prev_mode = curr_mode;
			curr_mode = new_array(struct chmod_mode_struct, 1);
			if (prev_mode)
				prev_mode->next = curr_mode;
			else
				first_mode = curr_mode;
			curr_mode->next = NULL;

			where_specified = where;
			if (where) {
				bits = where * what;
			} else {
				where = 0111;
				bits = (where * what) & ~orig_umask;
			}

			switch (op) {
			case CHMOD_ADD:
				curr_mode->ModeAND = CHMOD_BITS;
				curr_mode->ModeOR  = bits + topoct;
				curr_mode->ModeCOPY_SRC = copybits;
				curr_mode->ModeCOPY_DST = where;
				curr_mode->ModeCOPY_AND = where_specified ? CHMOD_BITS : ~orig_umask;
				curr_mode->ModeOP = op;
				break;
			case CHMOD_SUB:
				curr_mode->ModeAND = CHMOD_BITS - bits - topoct;
				curr_mode->ModeOR  = 0;
				curr_mode->ModeCOPY_SRC = copybits;
				curr_mode->ModeCOPY_DST = where;
				curr_mode->ModeCOPY_AND = where_specified ? CHMOD_BITS : ~orig_umask;
				curr_mode->ModeOP = op;
				break;
			case CHMOD_EQ:
				curr_mode->ModeAND = CHMOD_BITS - (where * 7) - (topoct ? topbits : 0)
						    - (copybits ? mode_dest_special_bits(where) : 0);
				curr_mode->ModeOR  = bits + topoct;
				curr_mode->ModeCOPY_SRC = copybits;
				curr_mode->ModeCOPY_DST = where;
				curr_mode->ModeCOPY_AND = where_specified ? CHMOD_BITS : ~orig_umask;
				curr_mode->ModeOP = op;
				break;
			case CHMOD_SET:
				curr_mode->ModeAND = 0;
				curr_mode->ModeOR  = bits;
				curr_mode->ModeCOPY_SRC = 0;
				curr_mode->ModeCOPY_DST = 0;
				curr_mode->ModeCOPY_AND = CHMOD_BITS;
				curr_mode->ModeOP = op;
				break;
			}

			curr_mode->flags = flags;

			if (!*modestr)
				break;
			modestr++;

			state = STATE_1ST_HALF;
			where = what = op = topoct = topbits = flags = copybits = 0;
		}

		switch (state) {
		case STATE_1ST_HALF:
			switch (*modestr) {
			case 'D':
				if (flags & FLAG_FILES_ONLY)
					state = STATE_ERROR;
				flags |= FLAG_DIRS_ONLY;
				break;
			case 'F':
				if (flags & FLAG_DIRS_ONLY)
					state = STATE_ERROR;
				flags |= FLAG_FILES_ONLY;
				break;
			case 'u':
				where |= 0100;
				topbits |= 04000;
				break;
			case 'g':
				where |= 0010;
				topbits |= 02000;
				break;
			case 'o':
				where |= 0001;
				break;
			case 'a':
				where |= 0111;
				break;
			case '+':
				op = CHMOD_ADD;
				state = STATE_2ND_HALF;
				break;
			case '-':
				op = CHMOD_SUB;
				state = STATE_2ND_HALF;
				break;
			case '=':
				op = CHMOD_EQ;
				state = STATE_2ND_HALF;
				break;
			default:
				if (isDigit(modestr) && *modestr < '8' && !where) {
					op = CHMOD_SET;
					state =  STATE_OCTAL_NUM;
					where = 1;
					what = *modestr - '0';
				} else
					state = STATE_ERROR;
				break;
			}
			break;
		case STATE_2ND_HALF:
			switch (*modestr) {
			case 'r':
				if (copybits)
					state = STATE_ERROR;
				what |= 4;
				break;
			case 'w':
				if (copybits)
					state = STATE_ERROR;
				what |= 2;
				break;
			case 'X':
				if (copybits)
					state = STATE_ERROR;
				flags |= FLAG_X_KEEP;
				/* FALL THROUGH */
			case 'x':
				if (copybits)
					state = STATE_ERROR;
				what |= 1;
				break;
			case 's':
				if (copybits)
					state = STATE_ERROR;
				if (topbits)
					topoct |= topbits;
				else
					topoct = 04000;
				break;
			case 't':
				if (copybits)
					state = STATE_ERROR;
				topoct |= 01000;
				break;
			case 'u':
				if (what || topoct || copybits)
					state = STATE_ERROR;
				copybits = 0100;
				break;
			case 'g':
				if (what || topoct || copybits)
					state = STATE_ERROR;
				copybits = 0010;
				break;
			case 'o':
				if (what || topoct || copybits)
					state = STATE_ERROR;
				copybits = 0001;
				break;
			default:
				state = STATE_ERROR;
				break;
			}
			break;
		case STATE_OCTAL_NUM:
			if (isDigit(modestr) && *modestr < '8') {
				what = what*8 + *modestr - '0';
				if (what > CHMOD_BITS)
					state = STATE_ERROR;
			} else
				state = STATE_ERROR;
			break;
		}
		modestr++;
	}

	if (state == STATE_ERROR) {
		free_chmod_mode(first_mode);
		return NULL;
	}

	if (!(curr_mode = *root_mode_ptr))
		*root_mode_ptr = first_mode;
	else {
		while (curr_mode->next)
			curr_mode = curr_mode->next;
		curr_mode->next = first_mode;
	}

	return first_mode;
}

static int mode_copy_bits(int mode, int copy_src, int copy_dst, int copy_and)
{
	int copy_bits = 0;

	if (copy_src & 0100)
		copy_bits |= (mode >> 6) & 7;
	if (copy_src & 0010)
		copy_bits |= (mode >> 3) & 7;
	if (copy_src & 0001)
		copy_bits |= mode & 7;

	return (copy_dst * copy_bits) & copy_and;
}


/* Takes an existing file permission and a list of AND/OR changes, and
 * create a new permissions. */
int tweak_mode(int mode, struct chmod_mode_struct *chmod_modes)
{
	int IsX = mode & 0111;
	int NonPerm = mode & ~CHMOD_BITS;
	int copy_bits;

	for ( ; chmod_modes; chmod_modes = chmod_modes->next) {
		if ((chmod_modes->flags & FLAG_DIRS_ONLY) && !S_ISDIR(NonPerm))
			continue;
		if ((chmod_modes->flags & FLAG_FILES_ONLY) && S_ISDIR(NonPerm))
			continue;
		copy_bits = mode_copy_bits(mode, chmod_modes->ModeCOPY_SRC,
					   chmod_modes->ModeCOPY_DST,
					   chmod_modes->ModeCOPY_AND);
		mode &= chmod_modes->ModeAND;
		if ((chmod_modes->flags & FLAG_X_KEEP) && !IsX && !S_ISDIR(NonPerm))
			mode |= chmod_modes->ModeOR & ~0111;
		else
			mode |= chmod_modes->ModeOR;
		if (chmod_modes->ModeOP == CHMOD_SUB)
			mode &= CHMOD_BITS - copy_bits;
		else
			mode |= copy_bits;
	}

	return mode | NonPerm;
}

/* Free the linked list created by parse_chmod. */
int free_chmod_mode(struct chmod_mode_struct *chmod_modes)
{
	struct chmod_mode_struct *next;

	while (chmod_modes) {
		next = chmod_modes->next;
		free(chmod_modes);
		chmod_modes = next;
	}
	return 0;
}
