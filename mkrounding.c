/*
 * A pre-compilation helper program to aid in the creation of rounding.h.
 *
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

#include "rsync.h"

struct test1 {
    union file_extras extras1[1];
    struct {
#	include "mkrounding.h"
    } file;
};

struct test2 {
    union file_extras extras2[2];
    struct {
#	include "mkrounding.h"
    } file;
};

struct test4 {
    union file_extras extras4[4];
    struct {
#	include "mkrounding.h"
    } file;
};

#define SIZE_TEST(n) (sizeof (struct test ## n) == EXTRA_LEN * n + sizeof (struct file_struct))

 int main(UNUSED(int argc), UNUSED(char *argv[]))
{
    int cnt;
    if (SIZE_TEST(1))
	cnt = 0;
    else if (SIZE_TEST(2))
	cnt = 1;
    else if (SIZE_TEST(4))
	cnt = 3;
    else {
	fprintf(stderr, "Unable to determine required file_extras rounding!\n");
	cnt = 3;
    }
    if (cnt)
	fprintf(stderr, "Rounding file_extras in multiples of %d", cnt + 1);
    else
	fprintf(stderr, "No rounding needed for file_extras");
    fprintf(stderr, " (EXTRA_LEN=%d, FILE_STRUCT_LEN=%d)\n",
	    (int)EXTRA_LEN, (int)FILE_STRUCT_LEN);
    printf("#define EXTRA_ROUNDING %d\n", cnt);
    return 0;
}
