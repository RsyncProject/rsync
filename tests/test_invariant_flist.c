#include <check.h>
#include <stdlib.h>
#include <string.h>

/*
 * We test that the file list entry allocation and copy logic does not
 * allow a basename length to overflow the allocated buffer. Since flist.c
 * uses internal functions that are difficult to call in isolation, we
 * simulate the invariant check that MUST hold: the allocated buffer for
 * a file entry must be >= the basename length before any memcpy occurs.
 *
 * We exercise this by calling recv_file_list via a crafted protocol stream.
 * However, since setting up the full rsync protocol is complex, we instead
 * validate the bounds-checking helper that should gate the memcpy.
 *
 * Invariant: For any basename_len received from remote, the allocated
 * flist entry buffer (flist_expand + per-entry alloc) must be at least
 * basename_len bytes, or the entry must be rejected.
 */

/* Import the actual allocation size computation used in flist.c */
extern int flist_entry_alloc_size(int basename_len, int linkname_len, int sum_len);

START_TEST(test_flist_basename_bounds)
{
    /* Invariant: allocated size must always be >= sum of field lengths */
    struct {
        int basename_len;
        int linkname_len;
        int sum_len;
    } cases[] = {
        /* Exploit: oversized basename that would overflow a typical alloc */
        { 0x7FFFFFFF, 0, 16 },
        /* Boundary: just above maximum sane path length */
        { 65536, 0, 16 },
        /* Valid: normal file entry */
        { 64, 32, 16 },
        /* Exploit: combined overflow */
        { 0x40000000, 0x40000000, 16 },
    };
    int num_cases = sizeof(cases) / sizeof(cases[0]);

    for (int i = 0; i < num_cases; i++) {
        int alloc = flist_entry_alloc_size(
            cases[i].basename_len,
            cases[i].linkname_len,
            cases[i].sum_len);
        /* If allocation succeeds (returns > 0), it must cover all fields */
        if (alloc > 0) {
            int needed = cases[i].basename_len + cases[i].linkname_len + cases[i].sum_len;
            ck_assert_msg(alloc >= needed,
                "Allocated %d but needed %d for case %d", alloc, needed, i);
        }
        /* If alloc <= 0, the entry was rejected which is safe */
    }
}
END_TEST

Suite *security_suite(void)
{
    Suite *s;
    TCase *tc_core;

    s = suite_create("Security");
    tc_core = tcase_create("Core");

    tcase_add_test(tc_core, test_flist_basename_bounds);
    suite_add_tcase(s, tc_core);

    return s;
}

int main(void)
{
    int number_failed;
    Suite *s;
    SRunner *sr;

    s = security_suite();
    sr = srunner_create(s);

    srunner_run_all(sr, CK_NORMAL);
    number_failed = srunner_ntests_failed(sr);
    srunner_free(sr);

    return (number_failed == 0) ? EXIT_SUCCESS : EXIT_FAILURE;
}