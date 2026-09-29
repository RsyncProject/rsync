#!/usr/bin/env python3
"""Smoke tests: the binary starts, reports itself, and honours file sizes
and timestamps.

The size check matters because MSVC's off_t is 32 bits; the port takes the
stat64/off64_t path so that transfers past 2 GB are addressable.  A 3 GB
file would make this suite slow and disk-hungry, so the check is that rsync
reports 64-bit files and moves a modest large file exactly.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import os
import time

from wintest import (FROM, TO, check, ok, pseudo_random, rsync,
                     rsync_capabilities, write)

# --- it runs and identifies itself ----------------------------------------
version = rsync('--version').stdout
check('rsync' in version and 'protocol version' in version,
      f'--version output looks wrong:\n{version}')
rsync('--help')
rsync('--info=help')
rsync('--debug=help')

caps = rsync_capabilities()
print('capabilities: ' + ', '.join(sorted(caps)))
check('64-bit files' in caps,
      'rsync does not report 64-bit file support; large files would truncate')
check('64-bit inums' in caps,
      'rsync reports narrow inums; --hard-links would join unrelated files')

# --- sizes are preserved exactly ------------------------------------------
sizes = [0, 1, 1023, 4096, 65535, 1000000]
for i, size in enumerate(sizes):
    write(FROM / f'size{i}.bin', pseudo_random(size, seed=i + 1))

rsync('-rt', f'{FROM}/', f'{TO}/')

for i, size in enumerate(sizes):
    got = (TO / f'size{i}.bin').stat().st_size
    check(got == size, f'size{i}.bin arrived at {got} bytes, expected {size}')

# --- -t preserves mtime ---------------------------------------------------
stamp = int(time.time()) - 86400 * 30      # a month ago, whole seconds
target = FROM / 'dated.txt'
write(target, 'timestamped\n', mtime=stamp)
rsync('-rt', f'{FROM}/', f'{TO}/')

got = int(os.stat(TO / 'dated.txt').st_mtime)
check(abs(got - stamp) <= 1,
      f'mtime not preserved: got {got}, expected {stamp}')

# Without -t the destination gets its own time, and a later -t run fixes it.
notime = TO.parent / 'notime'
rsync('-r', f'{FROM}/', f'{notime}/')
rsync('-rt', f'{FROM}/', f'{notime}/')
got = int(os.stat(notime / 'dated.txt').st_mtime)
check(abs(got - stamp) <= 1, 'a later -t run did not correct the mtime')

ok('version, capabilities, sizes and timestamps all correct')
