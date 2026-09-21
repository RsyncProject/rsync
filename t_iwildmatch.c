/* Unit test for KI-53: iwildmatch() must be case-insensitive on BOTH the text
 * and the pattern.  The old code folded only the text, so an upper-case pattern
 * (e.g. a "hosts deny" token *.BADDOMAIN.COM) failed to match a lower-case host
 * -> access-control fail-open.  Exits 0 if all cases match, 1 otherwise. */

#include <stdio.h>

extern int iwildmatch(const char *pattern, const char *text);

static const struct { const char *p, *t; int exp; } cases[] = {
	{ "abc",           "ABC",              1 },  /* text folded (always worked) */
	{ "ABC",           "abc",              1 },  /* pattern folded (the fix)    */
	{ "ABC",           "abd",              0 },
	{ "*.EXAMPLE.COM", "foo.example.com",  1 },
	{ "*.example.com", "FOO.EXAMPLE.COM",  1 },
	{ "*.BADDOMAIN.COM", "x.baddomain.com", 1 }, /* the access-control case */
	{ "[A-Z]bc",       "abc",              1 },  /* upper-case range folds     */
	{ "[ABC]xy",       "bxy",              1 },  /* upper-case class-member folds */
	{ "x[A-Z]z",       "xyz",              1 },  /* range mid-pattern           */
	{ "[a-z]BC",       "abc",              1 },  /* mixed: class + literal fold */
	{ "[\\A]bc",       "abc",              1 },  /* escaped class member folds  */
	{ "[A-Z]bc",       "5bc",              0 },  /* negative: digit not in range */
};

int main(int argc, char *argv[])
{
	(void)argc; (void)argv;
	int i, fails = 0, n = (int)(sizeof cases / sizeof cases[0]);
	for (i = 0; i < n; i++) {
		int r = iwildmatch(cases[i].p, cases[i].t);
		if (r != cases[i].exp) {
			printf("FAIL: iwildmatch(%s, %s) = %d, expected %d\n",
			       cases[i].p, cases[i].t, r, cases[i].exp);
			fails++;
		}
	}
	if (fails) {
		printf("iwildmatch: %d case(s) wrong -- the pattern is not folded "
		       "(asymmetric case handling)\n", fails);
		return 1;
	}
	printf("iwildmatch: case-insensitive on both text and pattern\n");
	return 0;
}
