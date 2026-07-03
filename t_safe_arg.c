/* Unit test for KI-54: safe_arg() in filename mode (opt==NULL) must not leak an
 * uninitialized heap byte.  The escape counter and the writer disagreed on a
 * backslash before a wildcard / a trailing backslash, leaving a gap that
 * strlen() walks into.  We poison the heap first so the leaked byte is a
 * deterministic non-NUL; then any extra byte makes the output differ from the
 * expected exact quoting.  Exits 0 if all outputs are exact, 1 otherwise. */

#define main rsync_main
#include "rsync.h"
#undef main

#include <stdio.h>

extern char *safe_arg(const char *opt, const char *arg);
extern int protect_args, old_style_args, am_sender, relative_paths;
int trust_sender_args = 0;   /* defined outside options.c; provide it here */

/* Minimal stubs pulled in via new_array()'s error path (can't link t_stub.o:
 * its globals collide with the full options.o we link for safe_arg). */
void rprintf(UNUSED(enum logcode code), const char *format, ...)
{
	va_list ap;
	va_start(ap, format);
	vfprintf(stderr, format, ap);
	va_end(ap);
}
void _exit_cleanup(int code, const char *file, int line)
{
	fprintf(stderr, "exit(%d): %s(%d)\n", code, file, line);
	exit(code);
}
const char *who_am_i(void) { return "tester"; }
int csum_len_for_type(int cst, int flg) { return cst || !flg ? 16 : 1; }
int canonical_checksum(int cst) { return cst ? 0 : 0; }

static const struct { const char *arg, *exp; } cases[] = {
	{ "\\*",    "\\*" },      /* backslash+wildcard: NOT doubled */
	{ "\\?",    "\\?" },
	{ "\\[",    "\\[" },
	{ "\\",     "\\\\" },     /* trailing backslash: doubled (NUL-footgun case) */
	{ "\\*\\?", "\\*\\?" },   /* two suppressed backslashes */
	{ "a\\*b",  "a\\*b" },
	{ "\\a",    "\\\\a" },    /* backslash+non-wildcard: doubled */
};

int main(int argc, char *argv[])
{
	(void)argc; (void)argv;
	int i, fails = 0, n = (int)(sizeof cases / sizeof cases[0]);

	protect_args = 0; old_style_args = 0; am_sender = 1;
	relative_paths = 0; trust_sender_args = 1;

	/* Poison the heap so an uninitialized byte reads as 0xbe, not a lucky NUL. */
	for (i = 0; i < 256; i++) {
		void *p = malloc(64);
		if (p) { memset(p, 0xbe, 64); free(p); }
	}

	for (i = 0; i < n; i++) {
		char *r = safe_arg(NULL, cases[i].arg);
		if (!r || strcmp(r, cases[i].exp) != 0) {
			printf("FAIL: safe_arg(NULL, \"%s\") = \"%s\" (len %zu), expected \"%s\"\n",
			       cases[i].arg, r ? r : "(null)", r ? strlen(r) : 0, cases[i].exp);
			fails++;
		}
	}
	if (fails) {
		printf("safe_arg: %d case(s) wrong -- uninitialized-byte leak / miscount\n", fails);
		return 1;
	}
	printf("safe_arg: filename-mode quoting is exact (no uninit byte)\n");
	return 0;
}
