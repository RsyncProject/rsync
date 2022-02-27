#define RSYNC_WILDTEST_NO_MAIN 1
#include "../wildtest.c"

#include <check.h>
#include <fnmatch.h>

START_TEST(wildtest_legacyfile) {
    FILE *fp = NULL;

    char *file = "./check/wildtest.txt";

    char buf[2048], *string[2], *end[2];
    int flag[2];

    if ((fp = fopen(file, "r")) == NULL) {
        ck_assert_msg(fp != NULL, "Unable to open %s", file);
        return;
    }

    int line = 0;
    while (fgets(buf, sizeof buf, fp)) {
        line++;

        if (parse_line(&file, line, buf, flag, end, string) == 0)
            continue;

        char *text = string[0];
        char *pattern = string[1];
        bool matches = flag[0];

        bool matched = wildmatch(pattern, text);

        ck_assert_msg(
            matched == matches,
            "wildmatch failure on line %d:\n  %s\n  %s\n  expected %s match\n",
            line, text, pattern, matches ? "a" : "NO");
    }

    fclose(fp);
}

START_TEST(wildtest_insensitive) {
    FILE *fp = NULL;

    char *file = "./check/iwildtest.txt";

    char buf[2048], *string[2], *end[2];
    int flag[2];

    if ((fp = fopen(file, "r")) == NULL) {
        ck_assert_msg(fp != NULL, "Unable to open %s", file);
        return;
    }

    int line = 0;
    while (fgets(buf, sizeof buf, fp)) {
        line++;

        if (parse_line(&file, line, buf, flag, end, string) == 0)
            continue;

        char *text = string[0];
        char *pattern = string[1];
        bool matches = flag[0];

        bool matched = iwildmatch(pattern, text);

        ck_assert_msg(
            matched == matches,
            "wildmatch failure on line %d:\n  %s\n  %s\n  expected %s match\n",
            line, text, pattern, matches ? "a" : "NO");
    }

    ck_assert_int_eq(line, 165);
}

START_TEST(wildtest_fnmatch) {
    FILE *fp = NULL;

    char *file = "./check/wildtest_fnmatch.txt";

    char buf[2048], *string[2], *end[2];
    int flag[2];

    if ((fp = fopen(file, "r")) == NULL) {
        ck_assert_msg(fp != NULL, "Unable to open %s", file);
        return;
    }

    int line = 0;
    while (fgets(buf, sizeof buf, fp)) {
        line++;

        if (parse_line(&file, line, buf, flag, end, string) == 0)
            continue;

        char *text = string[0];
        char *pattern = string[1];
        bool matches = flag[0];
        bool same_as_fnmatch = flag[1];
        bool fn_matches = (matches ^ !same_as_fnmatch);

        int flags = strstr(pattern, "**") ? 0 : FNM_PATHNAME;

        bool fn_matched = !fnmatch(pattern, text, flags);

        ck_assert_msg(fn_matched == fn_matches,
                      "fnmatch disagreement on line %d:\n  %s\n  %s\n  "
                      "expected %s match\n",
                      line, text, pattern, fn_matches ? "a" : "NO");
    }

    fclose(fp);
}

struct mode {
    int explode_mod;
    int empty_at_start;
    int empty_at_end;
    int empties_mod;
};

/* modes extracted from testsuite/wildmatch.txt */
struct mode modes[] = {
    /* -x1           : */ {1, 0, 0, 1024},
    /* -x1 -e1       : */ {1, 0, 0, 1},
    /* -x1 -e1se     : */ {1, 1, 1, 1},
    /* -x2           : */ {2, 0, 0, 1024},
    /* -x2 -ese      : */ {2, 1, 1, 1024},
    /* -x3           : */ {3, 0, 0, 1024},
    /* -x3 -e1       : */ {3, 0, 0, 1},
    /* -x4           : */ {4, 0, 0, 1024},
    /* -x4 -e2e      : */ {4, 0, 1, 2},
    /* -x5           : */ {5, 0, 0, 1024},
    /* -x5 -es       : */ {5, 1, 0, 1024},
};
const int mode_count = sizeof(modes) / sizeof(modes[0]);

static void explode(const char *text, char *buf, char **texts,
                    struct mode *mode) {
    int pos = 0, cnt = 0, ndx = 0, len = strlen(text);

    int explode_mod = mode->explode_mod;
    int empty_at_start = mode->empty_at_start;
    int empty_at_end = mode->empty_at_end;
    int empties_mod = mode->empties_mod;

    if (empty_at_start)
        texts[ndx++] = "";
    /* An empty string must turn into at least one empty array item. */
    while (1) {
        texts[ndx] = buf + ndx * (explode_mod + 1);
        strlcpy(texts[ndx++], text + pos, explode_mod + 1);
        if (pos + explode_mod >= len)
            break;
        pos += explode_mod;
        if (!(++cnt % empties_mod))
            texts[ndx++] = "";
    }
    if (empty_at_end)
        texts[ndx++] = "";
    texts[ndx] = NULL;
}

START_TEST(wildtest_exploded) {
    FILE *fp = NULL;

    char *file = "./wildtest.txt";

    char buf[2048], *string[2], *end[2];
    int flag[2];

    if ((fp = fopen(file, "r")) == NULL) {
        ck_assert_msg(fp != NULL, "Unable to open %s", file);
        return;
    }

    int line = 0;
    while (fgets(buf, sizeof buf, fp)) {
        line++;

        if (parse_line(&file, line, buf, flag, end, string) == 0)
            continue;

        char *text = string[0];
        char *pattern = string[1];
        int matches = flag[0];

        int mode = 0;
        for (mode = 0; mode < mode_count; mode++) {

            char explodebuf[MAXPATHLEN * 2], *texts[MAXPATHLEN];
            explode(text, explodebuf, texts, &modes[mode]);

            bool matched = wildmatch_array(pattern, (const char **)texts, 0);

            ck_assert_msg(matched == matches,
                          "wildmatch (explode mode %d) failure on line %d:\n  "
                          "%s\n  %s\n  expected %s match\n",
                          mode, line, text, pattern, matches ? "a" : "NO");
        }
    }

    fclose(fp);
    ck_assert_int_eq(line, 165);
}

START_TEST(wildtest_doliteral) {
    char* nulls[] = { NULL, NULL, NULL, NULL };
    char* abc[] = { "ab", "", "c", NULL };
    char* abcd[] = { "ab", "", "c", "d", NULL };

    ck_assert_int_eq(doliteral((uchar *)"foo", (uchar *)"foo", (const uchar * const *)nulls), 1);
    ck_assert_int_eq(doliteral((uchar *)"foo", (uchar *)"roo", (const uchar * const *)nulls), 0);

    ck_assert_int_eq(doliteral((uchar *)"fooabc", (uchar *)"foo", (const uchar * const *)abc), 1);

    ck_assert_int_eq(doliteral((uchar *)"fooabcd", (uchar *)"foo", (const uchar * const *)abc), 0);
    ck_assert_int_eq(doliteral((uchar *)"fooabc", (uchar *)"foo", (const uchar * const *)abcd), 0);
}

START_TEST(wildtest_trailing_N_elements) {
    char path1[] = "foo/bar/baz/bletch";
    char * path1_bletch = path1+12;

    const char * const texts[] = { path1, NULL };
    const uchar * const * a = (const uchar*const*)texts;

    ck_assert_ptr_eq(trailing_N_elements(&a, 1), path1_bletch);

    char path2_a[] = "foobarbaz";
    char path2_b[] = "";

    const char * const texts_2[] = { path2_a, path2_b, NULL };
    a = (const uchar*const*)texts_2;

    const uchar *result = trailing_N_elements(&a, 1);
    ck_assert_str_eq((const char *)result, path2_a);

    char path3_a[] = "foobarbaz";
    char path3_b[] = "";

    const char * const texts_3[] = { path3_a, path3_b, NULL };
    a = (const uchar*const*)texts_3;

    result = trailing_N_elements(&a, 3);
    ck_assert_ptr_eq(result, NULL);
}

START_TEST(wildtest_array) {
    const char * const texts[] = { "foo", "bar", NULL };

    ck_assert_int_eq(wildmatch_array("foobar", texts, 0), 1);
    ck_assert_int_eq(wildmatch_array("foobaz", texts, 0), 0);
    ck_assert_int_eq(wildmatch_array("fobbar", texts, 0), 0);

    ck_assert_int_eq(wildmatch_array("foobar", texts, 2), 0);

    const char * const texts_2[] = { "foo/", "bar/", "baz/", "bletch", NULL };
    ck_assert_int_eq(wildmatch_array("baz/bletch", texts_2, 2), 1);
    ck_assert_int_eq(wildmatch_array("*/bletch", texts_2, 2), 1);

    const char * const texts_3[] = { "foo/", "bar/", "baz/", "bletch", NULL };
    ck_assert_int_eq(wildmatch_array("bar/baz/bletch", texts_3, -1), 1);
    ck_assert_int_eq(wildmatch_array("**/baz/*", texts_3, -1), 1);

    const char * const texts_4[] = { "foo/", "bar/", "", "", "", "baz/", "bletch", "", "", NULL };
    ck_assert_int_eq(wildmatch_array("bar/**/bletch", texts_4, -1), 1);
    ck_assert_int_eq(wildmatch_array("baz/bletch/**", texts_4, -1), 0);

    const char * const texts_5[] = { "foo", NULL };
    ck_assert_int_eq(wildmatch_array("bletch/**", texts_5, -1), 0);
}

START_TEST(test_litmatch_array) {
    const char * const texts[] = { "foo/", "bar", NULL };

    ck_assert_int_eq(litmatch_array("bar", texts, 1), 1);
    ck_assert_int_eq(litmatch_array("foo/bar", texts, 0), 1);
    ck_assert_int_eq(litmatch_array("foo/baz", texts, 0), 0);

    const char * const texts_2[] = { NULL };

    ck_assert_int_eq(litmatch_array("foo/bar", texts_2, -1), 0);
}

Suite *wildtest_suite() {
    Suite *s;
    TCase *tcase;

    s = suite_create("wildtest");
    tcase = tcase_create("wildtest - wildtest.txt");
    tcase_add_test(tcase, wildtest_legacyfile);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("wildtest - iwildtest.txt (insensitive)");
    tcase_add_test(tcase, wildtest_insensitive);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("wildtest (fnmatch) - wildtest_fnmatch.txt");
    tcase_add_test(tcase, wildtest_fnmatch);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("wildtest (exploded) - wildtest.txt");
    tcase_add_test(tcase, wildtest_exploded);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("wildtest doliteral");
    tcase_add_test(tcase, wildtest_doliteral);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("wildtest trailing_N_elements");
    tcase_add_test(tcase, wildtest_trailing_N_elements);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("wildtest_array");
    tcase_add_test(tcase, wildtest_array);
    suite_add_tcase(s, tcase);

    tcase = tcase_create("litmatch_array");
    tcase_add_test(tcase, test_litmatch_array);
    suite_add_tcase(s, tcase);

    return s;
}

int main(int _argc, char **_argv) {
    int number_failed;
    SRunner *sr;

    sr = srunner_create(NULL);
    srunner_add_suite(sr, wildtest_suite());

    srunner_run_all(sr, CK_ENV);
    number_failed = srunner_ntests_failed(sr);
    srunner_free(sr);
    return (number_failed != 0);
}
