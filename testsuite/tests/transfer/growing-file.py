#!/usr/bin/env python3

import filecmp
import os
from harness.filesystem import make_data_file
from harness.mutation import TARGET, run_mutating_transfer
from harness.results import test_fail
from harness import metadata

metadata(transports={'pipe'})

original_size = 4096

def setup(source):
    make_data_file(source / TARGET, original_size)

def append(source):
    with (source / TARGET).open('ab') as fh:
        fh.write(b'appended data\n' * 20000)
        fh.flush()
        os.fsync(fh.fileno())

proc, src, dst = run_mutating_transfer(setup, append)
if proc.returncode != 0:
    test_fail(f"rsync exited {proc.returncode}\n{proc.stdout}{proc.stderr}")

if (src / TARGET).stat().st_size <= original_size:
    test_fail('source file did not grow')
if not (dst / TARGET).is_file() or not filecmp.cmp(src / TARGET, dst / TARGET, shallow=False):
    test_fail('destination does not contain the complete grown source')

print('source growth and the following file transfer intact')
