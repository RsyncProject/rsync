/*
**  wildmatch test suite.
*/

/*#define COMPARE_WITH_FNMATCH*/

#define WILD_TEST_ITERATIONS
#include "lib/wildmatch.c"

#include "popt.h"

#ifdef COMPARE_WITH_FNMATCH
#include <fnmatch.h>
#endif

typedef char bool;

#define false 0
#define true 1

int output_iterations = 0;

static struct poptOption long_options[] = {
  /* longName, shortName, argInfo, argPtr, value, descrip, argDesc */
  {"count",          'c', POPT_ARG_NONE,   &output_iterations, 0, 0, 0},
  {0,0,0,0, 0, 0, 0}
};

/* match just at the start of string (anchored tests) */
static void
ok(int n, const char *text, const char *pattern, bool matches, bool same_as_fnmatch)
{
    bool matched;
#ifdef COMPARE_WITH_FNMATCH
    bool fn_matched;
    int flags = strstr(pattern, "**")? 0 : FNM_PATHNAME;
#else
    same_as_fnmatch = 0; /* Get rid of unused-variable compiler warning. */
#endif

    matched = wildmatch(pattern, text);
#ifdef COMPARE_WITH_FNMATCH
    fn_matched = !fnmatch(pattern, text, flags);
#endif
    if (matched != matches) {
	printf("wildmatch failure on #%d:\n  %s\n  %s\n  expected %d\n",
	       n, text, pattern, matches);
    }
#ifdef COMPARE_WITH_FNMATCH
    if (fn_matched != (matches ^ !same_as_fnmatch)) {
	printf("fnmatch disagreement on #%d:\n  %s\n  %s\n  expected %d\n",
	       n, text, pattern, matches ^ !same_as_fnmatch);
    }
#endif
    if (output_iterations)
	printf("[%s] iterations = %d\n", pattern, wildmatch_iteration_count);
}

int
main(int argc, char **argv)
{
    int opt;
    poptContext pc = poptGetContext("wildtest", argc, (const char**)argv,
				    long_options, 0);

    while ((opt = poptGetNextOpt(pc)) != -1) {
	switch (opt) {
	  default:
	    fprintf(stderr, "Unknown option: `%c'\n", opt);
	    exit(1);
	}
    }

    /* Basic wildmat features. */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    ok(100, "foo",		"foo",			true,	true);
    ok(101, "foo",		"bar",			false,	true);
    ok(102, "",			"",			true,	true);
    ok(103, "foo",		"???",			true,	true);
    ok(104, "foo",		"??",			false,	true);
    ok(105, "foo",		"*",			true,	true);
    ok(106, "foo",		"f*",			true,	true);
    ok(107, "foo",		"*f",			false,	true);
    ok(108, "foo",		"*foo*",		true,	true);
    ok(109, "foobar",		"*ob*a*r*",		true,	true);
    ok(110, "aaaaaaabababab",	"*ab",			true,	true);
    ok(111, "foo*",		"foo\\*",		true,	true);
    ok(112, "foobar",		"foo\\*bar",		false,	true);
    ok(113, "f\\oo",		"f\\\\oo",		true,	true);
    ok(114, "ball",		"*[al]?",		true,	true);
    ok(115, "ten",		"[ten]",		false,	true);
    ok(116, "ten",		"**[!te]",		true,	true);
    ok(117, "ten",		"**[!ten]",		false,	true);
    ok(118, "ten",		"t[a-g]n",		true,	true);
    ok(119, "ten",		"t[!a-g]n",		false,	true);
    ok(120, "ton",		"t[!a-g]n",		true,	true);
    ok(121, "]",		"]",			true,	true);
    ok(122, "a]b",		"a[]]b",		true,	true);
    ok(123, "a-b",		"a[]-]b",		true,	true);
    ok(124, "a]b",		"a[]-]b",		true,	true);
    ok(125, "aab",		"a[]-]b",		false,	true);
    ok(126, "aab",		"a[]a-]b",		true,	true);

    /* Extended slash-matching features */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    ok(200, "foo/baz/bar",	"foo*bar",		false,	true);
    ok(201, "foo/baz/bar",	"foo**bar",		true,	true);
    ok(202, "foo/bar",		"foo?bar",		false,	true);
    ok(203, "foo/bar",		"foo[/]bar",		true,	false);
    ok(204, "foo",		"**/foo",		false,	true);
    ok(205, "/foo",		"**/foo",		true,	true);
    ok(206, "bar/baz/foo",	"**/foo",		true,	true);
    ok(207, "bar/baz/foo",	"*/foo",		false,	true);
    ok(208, "foo/bar/baz",	"**/bar*",		false,	false);
    ok(209, "foo/bar/baz",	"**/bar**",		true,	true);

    /* Various additional tests. */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    ok(300, "acrt",		"a[c-c]st",		false,	true);
    ok(301, "]",		"[!]-]",		false,	true);
    ok(302, "a",		"[!]-]",		true,	true);
    ok(303, "",			"\\",			false,	true);
    ok(304, "\\",		"\\",			false,	true);
    ok(305, "foo",		"foo",			true,	true);
    ok(306, "@foo",		"@foo",			true,	true);
    ok(307, "foo",		"@foo",			false,	true);
    ok(308, "[ab]",		"\\[ab]",		true,	true);
    ok(309, "?a?b",		"\\??\\?b",		true,	true);
    ok(310, "abc",		"\\a\\b\\c",		true,	true);
    ok(311, "foo",		"",			false,	true);
    ok(312, "foo/bar/baz/to",	"**/t[o]",		true,	true);

    /* Additional tests, including some malformed wildmats. */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    ok(500, "]",		"[\\-_]",		true,	true);
    ok(501, "[",		"[\\-_]",		false,	true);
    ok(502, ".",		"[\\\\-_]",		false,	true);
    ok(503, "^",		"[\\\\-_]",		true,	true);
    ok(504, "Z",		"[\\\\-_]",		false,	true);
    ok(505, "\\",		"[\\]]",		false,	true);
    ok(506, "ab",		"a[]b",			false,	true);
    ok(507, "a[]b",		"a[]b",			false,	true);
    ok(508, "ab[",		"ab[",			false,	true);
    ok(509, "ab",		"[!",			false,	true);
    ok(510, "ab",		"[-",			false,	true);
    ok(511, "-",		"[-]",			true,	true);
    ok(512, "-",		"[a-",			false,	true);
    ok(513, "-",		"[!a-",			false,	true);
    ok(514, "-",		"[--A]",		true,	true);
    ok(515, "5",		"[--A]",		true,	true);
    ok(516, "\303\206",		"[--A]",		false,	true);
    ok(517, " ",		"[ --]",		true,	true);
    ok(518, "$",		"[ --]",		true,	true);
    ok(519, "-",		"[ --]",		true,	true);
    ok(520, "0",		"[ --]",		false,	true);
    ok(521, "-",		"[---]",		true,	true);
    ok(522, "-",		"[------]",		true,	true);
    ok(523, "j",		"[a-e-n]",		false,	true);
    ok(524, "-",		"[a-e-n]",		true,	true);
    ok(525, "a",		"[!------]",		true,	true);
    ok(526, "[",		"[]-a]",		false,	true);
    ok(527, "^",		"[]-a]",		true,	true);
    ok(528, "^",		"[!]-a]",		false,	true);
    ok(529, "[",		"[!]-a]",		true,	true);
    ok(530, "^",		"[a^bc]",		true,	true);
    ok(531, "-b]",		"[a-]b]",		true,	true);
    ok(532, "\\]",		"[\\]]",		true,	true);
    ok(533, "\\",		"[\\]",			true,	true);
    ok(534, "\\",		"[!\\]",		false,	true);
    ok(535, "G",		"[A-\\]",		true,	true);
    ok(536, "aaabbb",		"b*a",			false,	true);
    ok(537, "aabcaa",		"*ba*",			false,	true);
    ok(538, ",",		"[,]",			true,	true);
    ok(539, ",",		"[\\,]",		true,	true);
    ok(540, "\\",		"[\\,]",		true,	true);
    ok(541, "-",		"[,-.]",		true,	true);
    ok(542, "+",		"[,-.]",		false,	true);
    ok(543, "-.]",		"[,-.]",		false,	true);

    /* Test recursive calls and the ABORT code. */
    ok(600, "-adobe-courier-bold-o-normal--12-120-75-75-m-70-iso8859-1", "-*-*-*-*-*-*-12-*-*-*-m-*-*-*", true, true);
    ok(601, "-adobe-courier-bold-o-normal--12-120-75-75-X-70-iso8859-1", "-*-*-*-*-*-*-12-*-*-*-m-*-*-*", false, true);
    ok(601, "-adobe-courier-bold-o-normal--12-120-75-75-/-70-iso8859-1", "-*-*-*-*-*-*-12-*-*-*-m-*-*-*", false, true);
    ok(602, "/adobe/courier/bold/o/normal//12/120/75/75/m/70/iso8859/1", "/*/*/*/*/*/*/12/*/*/*/m/*/*/*", true, true);
    ok(603, "/adobe/courier/bold/o/normal//12/120/75/75/X/70/iso8859/1", "/*/*/*/*/*/*/12/*/*/*/m/*/*/*", false, true);
    ok(604, "abcd/abcdefg/abcdefghijk/abcdefghijklmnop.txt", "**/*a*b*g*n*t", true, true);

    return 0;
}
