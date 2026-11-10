#!/usr/bin/env python3

from fixtures.trees import hands_setup
from harness.comparison import checkit
from harness.environment import FROMDIR, TODIR
from harness.filesystem import make_text_file
from harness.results import test_skipped
from harness import metadata

metadata(transports={'pipe'})

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

make_text_file(longdir / '1', 50)
make_text_file(longdir / '2', 100)

checkit(['--delete', '-avH', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)
