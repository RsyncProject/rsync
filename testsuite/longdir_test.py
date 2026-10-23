#!/usr/bin/env python3
# Python rewrite of testsuite/longdir.test.
#
# Regression test for a 2.0.11 bug: rsync used to mishandle paths nested
# inside a stupidly-long directory name. We build a three-deep nest of
# 175-char directory names, drop a couple of files in the leaf, and
# verify that --delete -avH still produces an identical destination.

from rsyncfns import (FROMDIR, TODIR, checkit, hands_setup, make_text_file,
                      test_skipped)


hands_setup()

longname = ('This-is-a-directory-with-a-stupidly-long-name-created-in-an-'
            'attempt-to-provoke-an-error-found-in-2.0.11-that-should-'
            'hopefully-never-appear-again-if-this-test-does-its-job')
longdir = FROMDIR / longname / longname / longname

try:
    longdir.mkdir(parents=True)
except OSError:
    test_skipped("unable to create long directory")

try:
    (longdir / '1').touch()
except OSError:
    test_skipped("unable to create files in long directory")

# Drop predictable, self-contained content into the two leaf files.
make_text_file(longdir / '1', 50)
make_text_file(longdir / '2', 100)

checkit(['--delete', '-avH', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)
