#!/usr/bin/env python3

import os

from harness.mutation import TARGET, assert_no_protocol_abort, run_mutating_transfer
from harness.rsync import test_fail

def setup(src):
    with open(src / TARGET, 'wb') as fh:
        fh.write(os.urandom(256 * 1024))

def mutate(src):
    os.unlink(src / TARGET)

proc, src, dst = run_mutating_transfer(setup, mutate)
assert_no_protocol_abort(proc)

if (src / TARGET).exists():
    test_fail("test setup: source file did not vanish")

if not (dst / 'aaa_pacer').is_file():
    test_fail("the pacer file was not transferred after the vanish")

print(f"change-vanish: source file removed mid-transfer; rsync exit "
      f"{proc.returncode} (0/24 ok), no protocol abort, later files intact")
