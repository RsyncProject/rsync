#!/usr/bin/env python3

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess

from harness import metadata
from harness.environment import FROMDIR, SCRATCHDIR
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv

metadata(transports={'pipe'})

FROMDIR.mkdir()
logfile = SCRATCHDIR / 'shared.log'
letters = '\u0131' * 58
names = [f'file-{letters}-{i:03d}' for i in range(96)]
source_bytes = os.fsencode(FROMDIR)
for name in names:
    with open(os.path.join(source_bytes, name.encode('utf-8')), 'wb') as entry:
        entry.write(b'contents\n')
if set(os.listdir(source_bytes)) != {name.encode('utf-8') for name in names}:
    test_skipped('filesystem does not preserve the UTF-8 filename bytes')

env = {**os.environ, 'LC_ALL': 'C'}
env.pop('RSYNC_ICONV', None)

def transfer(writer):
    dest = SCRATCHDIR / f'dest-{writer}'
    dest.mkdir()
    return subprocess.run(
        rsync_argv('-r', f'--log-file={logfile}', '--log-file-format=entry:' + '%n' * 8 + ':end',
                   f'{FROMDIR}/', f'{dest}/'),
        env=env, capture_output=True, timeout=30,
    )


writers = 10
with ThreadPoolExecutor(max_workers=writers) as pool:
    results = list(pool.map(transfer, range(writers)))
for writer, proc in enumerate(results):
    if proc.returncode:
        test_fail(f'writer {writer} failed ({proc.returncode}): {proc.stdout!r} {proc.stderr!r}')

expected = Counter({name.replace('\u0131', '\\#304\\#261').encode('ascii') * 8 + b':end': writers
                    for name in names})
entries, by_pid = Counter(), Counter()
for line in (logfile.read_bytes() if logfile.exists() else b'').split(b'\n'):
    prefix, sep, record = line.partition(b'] entry:')
    if sep:
        entries[record] += 1
        if record in expected:
            by_pid[prefix.rpartition(b' [')[2]] += 1
if entries != expected:
    lines = [f'shared log contains {sum((entries & expected).values())} '
             f'complete entries of {sum(expected.values())}',
             f'complete entries by PID: {dict(sorted(by_pid.items()))!r}']
    lines.extend(f'writer {writer}: stdout={proc.stdout[-2048:]!r} stderr={proc.stderr[-2048:]!r}'
                 for writer, proc in enumerate(results) if proc.stdout or proc.stderr)
    test_fail('\n'.join(lines))
