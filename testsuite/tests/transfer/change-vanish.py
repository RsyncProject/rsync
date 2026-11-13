#!/usr/bin/env python3

import os

from harness.mutation import TARGET, assert_no_protocol_abort, run_mutating_transfer
from harness.filesystem import make_data_file
from harness.results import test_fail
from harness import metadata

metadata(transports={'pipe'})

def setup(src):
    make_data_file(src / TARGET, 256 * 1024)

def mutate(src):
    os.unlink(src / TARGET)

proc, src, dst = run_mutating_transfer(setup, mutate)
assert_no_protocol_abort(proc)

if (src / TARGET).exists():
    test_fail("test setup: source file did not vanish")

print(f"change-vanish: source file removed mid-transfer; rsync exit "
      f"{proc.returncode} (0/24 ok), no protocol abort, later files intact")
