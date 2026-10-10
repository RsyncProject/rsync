#!/usr/bin/env python3

import os

from harness.mutation import TARGET, assert_no_protocol_abort, run_mutating_transfer
from harness.rsync import test_fail

BIG = 512 * 1024
SMALL = 4 * 1024

def setup(src):
    with open(src / TARGET, 'wb') as fh:
        fh.write(os.urandom(BIG))

def mutate(src):
    with open(src / TARGET, 'r+b') as fh:
        fh.truncate(SMALL)
        fh.flush()
        os.fsync(fh.fileno())

proc, src, dst = run_mutating_transfer(setup, mutate)
assert_no_protocol_abort(proc)

src_size = (src / TARGET).stat().st_size
if src_size != SMALL:
    test_fail(f"test setup: source did not shrink (size {src_size})")

if not (dst / 'aaa_pacer').is_file():
    test_fail("the pacer file was not transferred after the shrink")

if (dst / TARGET).is_file():
    if (dst / TARGET).stat().st_size > BIG:
        test_fail("destination is larger than the original flist size")

print(f"change-shrink: source shrank {BIG}->{SMALL} mid-transfer; rsync exit "
      f"{proc.returncode}, no protocol abort, later files intact")
