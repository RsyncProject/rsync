#!/usr/bin/python3

import os
import subprocess
from harness.rsync import (
    FROMDIR,
    SCRATCHDIR,
    makepath,
    rmtree,
    rsync_argv,
    test_fail,
    rsync_path_arg,
    rsh_cmd,
)

LSH = rsh_cmd()
os.environ['RSYNC_RSH'] = LSH

src = FROMDIR
rmtree(src)
makepath(src)
(src / 'f').write_text('payload\n')

def run(label, extra, local=False):
    dest = SCRATCHDIR / ('dest-' + label)
    rmtree(dest)
    makepath(dest)
    if local:
        argv = rsync_argv('-a', *extra, f'{src}/', f'{dest}/')
    else:
        argv = rsync_argv('-a', *extra, '-e', LSH,
                          f'--rsync-path={rsync_path_arg()}', f'{src}/', f'localhost:{dest}/')
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode < 0 or r.returncode >= 128:
        test_fail(f'{label}: rsync crashed (rc={r.returncode}): {r.stderr.strip()[:200]}')
    if not (dest / 'f').exists() or (dest / 'f').read_text() != 'payload\n':
        test_fail(f'{label}: transfer did not complete (rc={r.returncode}): {r.stderr.strip()[:200]}')

run('verbose-flood', ['-v'] * 60)

run('info-overflow', ['--info=BACKUP99999999999999999999'])

run('skip-compress-long', ['-z', '--skip-compress=' + 'a' * 5000], local=True)

print('argument bounds verified')
