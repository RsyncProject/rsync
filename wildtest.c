/*
**  wildmatch test suite.
*/

#include "rsync.h"
#include "lib/wildmatch.h"

/*#define COMPARE_WITH_FNMATCH*/

#ifdef COMPARE_WITH_FNMATCH
#include <fnmatch.h>
#endif

typedef char bool;

#define false 0
#define true 1

/* match just at the start of string (anchored tests) */
static void
beg(int n, const char *text, const char *pattern, bool matches, bool same_as_fnmatch)
{
    bool matched;
#ifdef COMPARE_WITH_FNMATCH
    bool fn_matched;
    int flags = strstr(pattern, "**")? 0 : FNM_PATHNAME;
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
}

/* match after any slash (non-anchored tests) */
static void
end(int n, const char *text, const char *pattern, bool matches, bool same_as_fnmatch)
{
    bool matched = false;
#ifdef COMPARE_WITH_FNMATCH
    bool fn_matched = false;
    int flags = strstr(pattern, "**")? 0 : FNM_PATHNAME;
#endif

    if (strncmp(pattern, "**", 2) == 0) {
	matched = wildmatch(pattern, text);
#ifdef COMPARE_WITH_FNMATCH
	fn_matched = !fnmatch(pattern, text, flags);
#endif
    }
    else {
	const char *t = text;
	while (1) {
#ifdef COMPARE_WITH_FNMATCH
	    if (!fn_matched)
		fn_matched = !fnmatch(pattern, t, flags);
#endif
	    if (wildmatch(pattern, t)) {
		matched = true;
		break;
	    }
#ifdef COMPARE_WITH_FNMATCH
	    if (fn_matched)
		fn_matched = -1;
#endif
	    if (!(t = strchr(t, '/')))
		break;
	    t++;
	}
    }
    if (matched != matches) {
	printf("wildmatch failure on #%d:\n  %s\n  %s\n  expected %d\n",
	       n, text, pattern, matches);
    }
#ifdef COMPARE_WITH_FNMATCH
    if (fn_matched < 0 || fn_matched != (matches ^ !same_as_fnmatch)) {
	printf("fnmatch disagreement on #%d:\n  %s\n  %s\n  expected %d\n",
	       n, text, pattern, matches ^ !same_as_fnmatch);
    }
#endif
}

int
main(int argc, char **argv)
{
    /* Use our args to avoid a compiler warning. */
    if (argc)
	argv++;

    /* Basic wildmat features. */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    beg(100, "foo",		"foo",			true,	true);
    beg(101, "foo",		"bar",			false,	true);
    beg(102, "",		"",			true,	true);
    beg(103, "foo",		"???",			true,	true);
    beg(104, "foo",		"??",			false,	true);
    beg(105, "foo",		"*",			true,	true);
    beg(106, "foo",		"f*",			true,	true);
    beg(107, "foo",		"*f",			false,	true);
    beg(108, "foo",		"*foo*",		true,	true);
    beg(109, "foobar",		"*ob*a*r*",		true,	true);
    beg(110, "aaaaaaabababab",	"*ab",			true,	true);
    beg(111, "foo*",		"foo\\*",		true,	true);
    beg(112, "foobar",		"foo\\*bar",		false,	true);
    beg(113, "f\\oo",		"f\\\\oo",		true,	true);
    beg(114, "ball",		"*[al]?",		true,	true);
    beg(115, "ten",		"[ten]",		false,	true);
    beg(116, "ten",		"**[!te]",		true,	true);
    beg(117, "ten",		"**[!ten]",		false,	true);
    beg(118, "ten",		"t[a-g]n",		true,	true);
    beg(119, "ten",		"t[!a-g]n",		false,	true);
    beg(120, "ton",		"t[!a-g]n",		true,	true);
    beg(121, "]",		"]",			true,	true);
    beg(122, "a]b",		"a[]]b",		true,	true);
    beg(123, "a-b",		"a[]-]b",		true,	true);
    beg(124, "a]b",		"a[]-]b",		true,	true);
    beg(125, "aab",		"a[]-]b",		false,	true);
    beg(126, "aab",		"a[]a-]b",		true,	true);

    /* Extended slash-matching features */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    beg(200, "foo/baz/bar",	"foo*bar",		false,	true);
    beg(201, "foo/baz/bar",	"foo**bar",		true,	true);
    beg(202, "foo/bar",		"foo?bar",		false,	true);
    beg(203, "foo/bar",		"foo[/]bar",		true,	false);
    beg(204, "foo",		"**/foo",		false,	true);
    beg(205, "/foo",		"**/foo",		true,	true);
    beg(206, "bar/baz/foo",	"**/foo",		true,	true);
    beg(207, "bar/baz/foo",	"*/foo",		false,	true);
    beg(208, "foo/bar/baz",	"**/bar*",		false,	false);
    beg(209, "foo/bar/baz",	"**/bar**",		true,	true);

    /* Various additional tests. */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    beg(300, "acrt",		"a[c-c]st",		false,	true);
    beg(301, "]",		"[!]-]",		false,	true);
    beg(302, "a",		"[!]-]",		true,	true);
    beg(303, "",		"\\",			false,	true);
    beg(304, "\\",		"\\",			false,	true);
    beg(305, "foo",		"foo",			true,	true);
    beg(306, "@foo",		"@foo",			true,	true);
    beg(307, "foo",		"@foo",			false,	true);
    beg(308, "[ab]",		"\\[ab]",		true,	true);
    beg(309, "?a?b",		"\\??\\?b",		true,	true);
    beg(310, "abc",		"\\a\\b\\c",		true,	true);
    beg(311, "foo",		"",			false,	true);

    /* Tail-match tests */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    end(400, "foo/bar/baz",	"baz",			true,	true);
    end(401, "foo/bar/baz",	"bar/baz",		true,	true);
    end(402, "foo/bar/baz",	"ar/baz",		false,	true);
    end(403, "foo/bar/baz",	"/bar/baz",		false,	true);
    end(404, "foo/bar/baz",	"bar",			false,	true);
    end(405, "foo/bar/baz/to",	"t[o]",			true,	true);

    /* Additional tests, including some malformed wildmats. */
    /* TEST, "text",		"pattern",		MATCH?, SAME-AS-FNMATCH? */
    beg(500, "]",		"[\\-_]",		true,	false);
    beg(501, "[",		"[\\-_]",		false,	true);
    beg(502, ".",		"[\\\\-_]",		false,	true);
    beg(503, "^",		"[\\\\-_]",		true,	false);
    beg(504, "Z",		"[\\\\-_]",		false,	true);
    beg(505, "\\",		"[\\]]",		false,	true);
    beg(506, "ab",		"a[]b",			false,	true);
    beg(507, "a[]b",		"a[]b",			false,	true);
    beg(508, "ab[",		"ab[",			false,	true);
    beg(509, "ab",		"[!",			false,	true);
    beg(510, "ab",		"[-",			false,	true);
    beg(511, "-",		"[-]",			true,	true);
    beg(512, "-",		"[a-",			false,	true);
    beg(513, "-",		"[!a-",			false,	true);
    beg(514, "-",		"[--A]",		true,	true);
    beg(515, "5",		"[--A]",		true,	true);
    beg(516, "\303\206",	"[--A]",		false,	true);
    beg(517, " ",		"[ --]",		true,	true);
    beg(518, "$",		"[ --]",		true,	true);
    beg(519, "-",		"[ --]",		true,	true);
    beg(520, "0",		"[ --]",		false,	true);
    beg(521, "-",		"[---]",		true,	true);
    beg(522, "-",		"[------]",		true,	true);
    beg(523, "j",		"[a-e-n]",		false,	true);
    beg(524, "-",		"[a-e-n]",		true,	true);
    beg(525, "a",		"[!------]",		true,	true);
    beg(526, "[",		"[]-a]",		false,	true);
    beg(527, "^",		"[]-a]",		true,	true);
    beg(528, "^",		"[!]-a]",		false,	true);
    beg(529, "[",		"[!]-a]",		true,	true);
    beg(530, "^",		"[a^bc]",		true,	true);
    beg(531, "-b]",		"[a-]b]",		true,	true);
    beg(532, "\\]",		"[\\]]",		true,	false);
    beg(533, "\\",		"[\\]",			true,	false);
    beg(534, "\\",		"[!\\]",		false,	false); /*FN?*/
    beg(535, "G",		"[A-\\]",		true,	false);
    beg(536, "aaabbb",		"b*a",			false,	true);
    beg(537, "aabcaa",		"*ba*",			false,	true);
    beg(538, ",",		"[,]",			true,	true);
    beg(539, ",",		"[\\,]",		true,	true);
    beg(540, "\\",		"[\\,]",		true,	false);
    beg(541, "-",		"[,-.]",		true,	true);
    beg(542, "+",		"[,-.]",		false,	true);
    beg(543, "-.]",		"[,-.]",		false,	true);

    return 0;
}
