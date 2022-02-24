#include <check.h>

START_TEST(wildtest_legacyfile) { ck_assert_int_eq(0, 0); }

Suite *wildtest_suite() {
    Suite *s;
    TCase *tcase;

    s = suite_create("wildtest");
    tcase = tcase_create("wildtest - wildtest.txt");
    tcase_add_test(tcase, wildtest_legacyfile);
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
