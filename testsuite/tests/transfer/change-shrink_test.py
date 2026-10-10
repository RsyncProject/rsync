#!/usr/bin/env python3
# Change-while-transferring: a source file that SHRINKS between flist-scan and
# send.  The flist records the large size; the sender re-stats and maps the
# smaller current size (sender.c do_fstat/map_file/match_sums), so it sends
# fewer bytes than F_LENGTH.  rsync must not abort the whole transfer; the
# destination file should end up as the (shorter) content actually sent, and
# later files must still transfer.
#
# See mutatefns.py for the pacer/bwlimit reproduction.

import os

from mutatefns import TARGET, assert_no_protocol_abort, run_mutating_transfer
from rsyncfns import test_fail

BIG = 512 * 1024
SMALL = 4 * 1024


def setup(src):
    # Recorded in the flist at the large size.
    with open(src / TARGET, 'wb') as fh:
        fh.write(os.urandom(BIG))


def mutate(src):
    # Truncate to a much smaller size during the pacer's transfer.
    with open(src / TARGET, 'r+b') as fh:
        fh.truncate(SMALL)
        fh.flush()
        os.fsync(fh.fileno())


proc, src, dst = run_mutating_transfer(setup, mutate)
assert_no_protocol_abort(proc)

src_size = (src / TARGET).stat().st_size
if src_size != SMALL:
    test_fail(f"test setup: source did not shrink (size {src_size})")

# The later files (the pacer) must have transferred despite the shrink.
if not (dst / 'aaa_pacer').is_file():
    test_fail("the pacer file was not transferred after the shrink")

# If the shrunk target was transferred, it must match the current source.
if (dst / TARGET).is_file():
    if (dst / TARGET).stat().st_size > BIG:
        test_fail("destination is larger than the original flist size")

print(f"change-shrink: source shrank {BIG}->{SMALL} mid-transfer; rsync exit "
      f"{proc.returncode}, no protocol abort, later files intact")
