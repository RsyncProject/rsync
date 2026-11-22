/* Unit test for KI-50: clean_fname(name, CFN_COLLAPSE_DOT_DOT_DIRS) must
 * collapse ".." components.  An off-by-one left the collapse dead for all
 * multi-component and absolute paths.  Exits 0 if all cases collapse correctly,
 * 1 otherwise. */

#define main rsync_main
#include "rsync.h"
#undef main

#include <stdio.h>

/* Globals referenced by syscall.o/util1.o (mirrors t_unsafe.c). */
int dry_run = 0, am_root = 0, am_sender = 1, read_only = 0, list_only = 0;
int copy_links = 0, copy_unsafe_links = 0;
short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

static const struct { const char *in, *out; } cases[] = {
	{ "a/b/../c",     "a/c" },
	{ "/x/y/../z",    "/x/z" },
	{ "a/../b",       "b" },
	{ "p/q/r/../../s","p/s" },
	{ "d/e/..",       "d" },
};

int main(int argc, char *argv[])
{
	(void)argc; (void)argv;
	int i, fails = 0, n = (int)(sizeof cases / sizeof cases[0]);
	for (i = 0; i < n; i++) {
		char buf[MAXPATHLEN];
		strlcpy(buf, cases[i].in, sizeof buf);
		clean_fname(buf, CFN_COLLAPSE_DOT_DOT_DIRS);
		if (strcmp(buf, cases[i].out) != 0) {
			printf("FAIL: clean_fname(\"%s\") = \"%s\", expected \"%s\"\n",
			       cases[i].in, buf, cases[i].out);
			fails++;
		}
	}
	if (fails) {
		printf("clean_fname: %d case(s) not collapsed -- CFN_COLLAPSE_DOT_DOT_DIRS "
		       "off-by-one\n", fails);
		return 1;
	}
	printf("clean_fname: '..' collapse correct\n");
	return 0;
}
