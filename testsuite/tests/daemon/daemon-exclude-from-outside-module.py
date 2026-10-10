#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

PORT = 12947

base = SCRATCHDIR / 'exclude-from-outside'
rmtree(base)
module = base / 'module'
etc = base / 'etc'
dest = base / 'dest'
makepath(module, etc, dest)

(module / 'keep.txt').write_text('KEEP\n')
(module / 'drop.txt').write_text('DROP\n')
(etc / 'excludes').write_text('drop.txt\n')

conf = write_daemon_conf([
    ('m', {
        'path': str(module),
        'read only': 'yes',
        'use chroot': 'no',
        'exclude from': str(etc / 'excludes'),
    }),
], name='exclude-from-outside.conf')
url = start_test_daemon(conf, PORT)

proc = subprocess.run(
    rsync_argv('-r', f'{url}m/', str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'

if proc.returncode != 0:
    test_fail(f'daemon refused an "exclude from" file outside the module root ({ctx})')

got = sorted(p.name for p in dest.rglob('*') if p.is_file())
if got != ['keep.txt']:
    test_fail(f'expected only keep.txt to transfer, got {got} ({ctx})')

print('an operator "exclude from" outside the module root is still honoured')
