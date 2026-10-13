#!/usr/bin/env python3
# --files-from from a module subdir must not send or remove a filter-excluded module-root file.

import shutil

from rsyncfns import (
    SCRATCHDIR, assert_exists, assert_not_exists, makepath, run_rsync,
    start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12912

module = SCRATCHDIR / 'module'
listed = module / 'subdir' / 'file.txt'
excluded = module / 'file.txt'
dst = SCRATCHDIR / 'dst'
makepath(listed.parent)
listed.write_text("listed\n")
excluded.write_text("hidden\n")
shutil.copystat(listed, excluded)
listing = SCRATCHDIR / 'files-from.list'
listing.write_text("file.txt\n")

conf = write_daemon_conf([
    ('mod', {'path': str(module), 'read only': 'no', 'filter': '+ /subdir/*** - *'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

run_rsync('-rt', '--remove-source-files', f'--files-from={listing}', f'{url}mod/subdir/', f'{dst}/')
if (dst / 'file.txt').read_text() != "listed\n":
    test_fail("the pull sent the excluded module-root file")
assert_not_exists(listed, label="--remove-source-files")
assert_exists(excluded, label="excluded module-root file")
