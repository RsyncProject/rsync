#!/usr/bin/env python3
"""Coverage of --data-transfer-limit.

--data-transfer-limit specifies a maximum amount of data to transfer. This does not take delta-sync into consideration;
only "whole file" transfer size is counted. If the next file to be transferred would exceed the limit specified for this
execution, rsync will exit with code 26 and no further transfers will occur.
"""

from rsyncfns import (
    FROMDIR, TODIR,
    make_data_file, makepath, rmtree, run_rsync, test_fail,
)

src = FROMDIR
dest = TODIR
TESTFILE1 = "testfile1"
TESTFILE2 = "testfile2"
FILE1_SIZE = 400 * 1024  # 400KB
FILE2_SIZE = 200 * 1024  # 200KB
LIMIT_KB = '500K'

rmtree(src)
rmtree(dest)
makepath(src)
makepath(dest)
make_data_file(src / TESTFILE1, FILE1_SIZE)
make_data_file(src / TESTFILE2, FILE2_SIZE)

# Test --data-transfer-limit stops the transfer between files
proc = run_rsync(
    '-a',
    '--whole-file',
    f'--data-transfer-limit={LIMIT_KB}',
    f'{src}/',
    f'{dest}/',
    check=False, # Don't fail on non-zero exit code
    capture_output=True
)

if proc.returncode == 0:
    test_fail("--data-transfer-limit did not cause a non-zero exit code.")

# The first file should be transferred completely
dest_file1 = dest / TESTFILE1
if not dest_file1.exists() or dest_file1.stat().st_size != FILE1_SIZE:
    test_fail("The first file was not transferred completely.")

# The second file should not be transferred and rsync should exit with status 26
dest_file2 = dest / TESTFILE2
if dest_file2.exists():
    test_fail("The second file was transferred, but it should have been skipped.")

print("transfer_limit_test: --data-transfer-limit verified")
