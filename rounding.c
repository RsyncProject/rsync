/*
 * A pre-compilation helper program to aid in the creation of rounding.h.
 *
 * Copyright (C) 2007-2009 Wayne Davison
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

#define ARRAY_LEN (EXTRA_ROUNDING+1)
#define SIZEOF(x) ((long int)sizeof (x))

struct test {
    union file_extras extras[ARRAY_LEN];
    struct file_struct file;
};

#define ACTUAL_SIZE	SIZEOF(struct test)
#define EXPECTED_SIZE	(SIZEOF(union file_extras) * ARRAY_LEN + SIZEOF(struct file_struct))

 int main(UNUSED(int argc), UNUSED(char *argv[]))
{
    static int test_array[1 - 2 * (ACTUAL_SIZE != EXPECTED_SIZE)];
    test_array[0] = 0;
    return 0;
}
