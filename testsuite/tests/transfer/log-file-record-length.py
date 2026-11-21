#!/usr/bin/env python3

import os
import subprocess

from harness import metadata
from harness.environment import FROMDIR, SCRATCHDIR
from harness.results import test_fail
from harness.rsync import rsync_argv

metadata(transports={'pipe'})

FROMDIR.mkdir()
names = ['a' * 159 + letter for letter in 'ab']
for name in names:
    (FROMDIR / name).write_bytes(b'data\n')

prefix = 'item:'
suffix = ':end'
lengths = [1024, 1025, 2046, 2047]
# Some systems build the formatter with MAXPATHLEN=1024 and cannot
# emit records beyond the old logfile copy limit.
if os.pathconf(FROMDIR, 'PC_PATH_MAX') > 1024:
    lengths.extend((2048, 2049, 2500))

for length in lengths:
    dest = SCRATCHDIR / f'dest-{length}'
    dest.mkdir()
    logfile = SCRATCHDIR / f'log-{length}'
    count, padding = divmod(length - len(prefix) - len(suffix) - 1, len(names[0]))
    log_format = prefix + '%n' * count + '.' * padding + suffix
    proc = subprocess.run(
        rsync_argv('-r', f'--log-file={logfile}', f'--log-file-format={log_format}',
                   *(str(FROMDIR / name) for name in names), f'{dest}/'),
        env={**os.environ, 'LC_ALL': 'C'}, capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        test_fail(f'{length}-byte record: transfer failed ({proc.returncode}): {proc.stderr!r}')
    if sorted(file.name for file in dest.iterdir()) != names:
        test_fail(f'{length}-byte record: transfer lost an input file')

    data = logfile.read_bytes()
    items = [line.partition(b'] ')[2] for line in data.split(b'\n')
             if b'] item:' in line]
    expected = [(prefix + name * count + '.' * padding + suffix).encode('ascii')
                for name in names]
    if sorted(items) != expected or not data.endswith(b'\n'):
        test_fail(f'{length}-byte record: log contains {len(items)} item lines '
                  f'with payload lengths {[len(item) for item in items]}')

print(f'complete logfile records at lengths: {lengths}')
