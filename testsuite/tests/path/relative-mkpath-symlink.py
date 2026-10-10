#!/usr/bin/env python3

import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail

for kind, relative in (('file', 'A/B/file'), ('directory', 'A/B/C/')):
    base = SCRATCHDIR / kind
    source = base / 'source'
    destination = base / 'destination'
    outside = base / 'outside'
    rmtree(base)
    (source / 'A' / 'B' / 'C').mkdir(parents=True)
    (source / 'A' / 'B' / 'file').write_text('payload\n')
    (source / 'A' / 'B' / 'C' / 'file').write_text('payload\n')
    destination.mkdir()
    outside.mkdir()
    (destination / 'A').symlink_to(outside)
    subprocess.run(rsync_argv('-aR', '--no-implied-dirs', f'{source}/./{relative}',
                              f'{destination}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    leaked = list(outside.iterdir())
    if leaked:
        test_fail(f'{kind}: --relative parent creation escaped through a symlink: {leaked}')
