#!/usr/bin/env python3

import os
import subprocess

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_argv, rsync_path_arg, run_rsync

metadata(features={'remote-shell'}, transports={'pipe'})

base = SCRATCHDIR / 'time-only-remote'
src = base / 'source'
dest = base / 'destination'
push_dest = base / 'push-destination'
rmtree(base)
makepath(src, dest, push_dest)
(src / 'record').write_bytes(b'complete remote source\n')
(dest / 'record').write_bytes(b'')
mtime = 1_700_000_000
for path in (src / 'record', dest / 'record'):
    os.utime(path, (mtime, mtime))

shell = rsh_cmd()
remote_path = f'--rsync-path={rsync_path_arg()}'
run_rsync('-a', '--time-only', '-e', shell, remote_path,
          f'localhost:{src}/', f'{dest}/')
if (dest / 'record').read_bytes() != b'':
    test_fail('remote pull replaced a same-time destination stub')

result = subprocess.run(rsync_argv('-a', '--time-only', '-e', shell, remote_path,
                                    f'{src}/', f'localhost:{push_dest}/'),
                        capture_output=True, text=True)
if result.returncode == 0 or '--time-only requires a local receiver' not in result.stderr:
    test_fail(f'remote push did not refuse --time-only: {result.returncode}, {result.stderr!r}')
if any(push_dest.iterdir()):
    test_fail('refused remote push created a destination file')

print('time-only remote pull and push direction verified')
