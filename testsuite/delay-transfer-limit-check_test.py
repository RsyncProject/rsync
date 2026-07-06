#!/usr/bin/env python3
"""Coverage of --delay-transfer-limit-check.

--data-transfer-limit specifies a maximum amount of data to transfer. This does not take delta-sync into consideration;
only "whole file" transfer size is counted. If the next file to be transferred would exceed the limit specified for this
execution, rsync will exit with code 26 and no further transfers will occur. However, if --delay-transfer-limit-check is
specified, transfers will continue until the limit has been exceeded. If the limit has not yet been reached, the next
file will be transferred even if doing so would exceed the limit specified for this execution. The next file after that
will not be transferred and rsync will exit with code 26.
"""

from rsyncfns import (
    FROMDIR, TODIR,
    make_data_file, makepath, rmtree, run_rsync, test_fail,
)

src = FROMDIR
dest = TODIR
TESTFILE1 = 'testfile1'
TESTFILE2 = 'testfile2'
TESTFILE3 = 'testfile3'
FILE1_SIZE = 400 * 1024  # 400KB
FILE2_SIZE = 200 * 1024  # 200KB
FILE3_SIZE = 100 * 1024  # 100KB
LIMIT_KB = '500K'

rmtree(src)
rmtree(dest)
makepath(src)
makepath(dest)
make_data_file(src / TESTFILE1, FILE1_SIZE)
make_data_file(src / TESTFILE2, FILE2_SIZE)
make_data_file(src / TESTFILE3, FILE3_SIZE)

# Test --delay-transfer-limit-check allows transfers to continue as long as the transfer limit has not yet been reached.
# Transfers will stop only after the limit has been reached.
proc = run_rsync(
    '-a',
    '--whole-file',
    f'--data-transfer-limit={LIMIT_KB}',
    f'--delay-transfer-limit-check',
    f'{src}/',
    f'{dest}/',
    check=False, # Don't fail on non-zero exit code
    capture_output=True
)

if proc.returncode != 26:
    test_fail(f'Exit status 26 expected; got: {proc.returncode} instead.')

# The first file should be transferred
dest_file1 = dest / TESTFILE1
if not dest_file1.exists() or dest_file1.stat().st_size != FILE1_SIZE:
    test_fail('The first file was not transferred.')

# The second file should be transferred
dest_file2 = dest / TESTFILE2
if not dest_file2.exists():
    test_fail('The second file was not transferred')

# The third file should not be transferred and rsync should exit with code 26
dest_file3 = dest / TESTFILE3
if dest_file3.exists():
    test_fail('The third file was transferred, but it should have been skipped.')

print('transfer_limit_test: --delay-transfer-limit-check verified')
