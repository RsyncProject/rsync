#define RSYNC_WILDTEST_NO_MAIN 1
#include "../wildtest.c"

#include <check.h>
#include <fnmatch.h>

START_TEST(wildtest_legacyfile) {
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
        bool matches = flag[0];

        bool matched = wildmatch(pattern, text);

        ck_assert_msg(
            matched == matches,
            "wildmatch failure on line %d:\n  %s\n  %s\n  expected %s match\n",
            line, text, pattern, matches ? "a" : "NO");
    }

    fclose(fp);
    ck_assert_int_eq(line, 165);
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
    ck_assert_int_eq(line, 165);
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
