#!/usr/bin/env python3

import os
import subprocess

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv

metadata(features={'hardlinks'}, transports={'pipe'})

base = SCRATCHDIR / 'time-only-hard-links'
src = base / 'source'
dest = base / 'destination'
rmtree(base)
makepath(src, dest)
(src / 'first').write_bytes(b'complete source data\n')
os.link(src / 'first', src / 'second')
(dest / 'first').write_bytes(b'')
mtime = 1_700_000_000
os.utime(src / 'first', (mtime, mtime))
os.utime(dest / 'first', (mtime, mtime))

result = subprocess.run(rsync_argv('-aH', '--time-only', f'{src}/', f'{dest}/'),
                        capture_output=True, text=True)
if result.returncode == 0 or '--time-only cannot be combined with --hard-links' not in result.stderr:
    test_fail(f'hard-link group was accepted: {result.returncode}, {result.stderr!r}')
if (dest / 'first').read_bytes() != b'' or (dest / 'second').exists():
    test_fail('a refused hard-link transfer changed the destination')

print('time-only refuses hard-link groups before transfer')
