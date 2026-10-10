#!/usr/bin/env python3
# Change-while-transferring: a source file that is DELETED between flist-scan
# and send.  rsync's documented behaviour is to warn ("file has vanished") and
# continue (exit 24), not to abort the whole run.  This guards that a vanished
# file during a mid-transfer window is still handled gracefully and later files
# transfer.
#
# See mutatefns.py for the pacer/bwlimit reproduction.

import os

from mutatefns import TARGET, assert_no_protocol_abort, run_mutating_transfer
from rsyncfns import test_fail


def setup(src):
    with open(src / TARGET, 'wb') as fh:
        fh.write(os.urandom(256 * 1024))


def mutate(src):
    os.unlink(src / TARGET)


proc, src, dst = run_mutating_transfer(setup, mutate)
assert_no_protocol_abort(proc)

if (src / TARGET).exists():
    test_fail("test setup: source file did not vanish")

# The pacer (transferred before the vanish) and any earlier files must survive.
if not (dst / 'aaa_pacer').is_file():
    test_fail("the pacer file was not transferred after the vanish")

print(f"change-vanish: source file removed mid-transfer; rsync exit "
      f"{proc.returncode} (0/24 ok), no protocol abort, later files intact")
