#!/usr/bin/env python3
"""A daemon's own "exclude from" file may live outside the module root.

"exclude from" / "include from" / "filter" name operator-configured paths, and
pointing them at something like /etc/rsync/excludes is the ordinary way to
write them -- rsyncd.conf(5) puts no constraint on where the file lives.

parse_filter_file() confines a PEER-driven merge file to the module root, since
a non-chrooted daemon writes --backup-dir entries as root and a raced backup
symlink is therefore root-owned, which the ownership walk trusts (see
filter-leak).  That confinement must not extend to the operator's own
parameters: applying it to these three refuses the file outright and takes the
whole connection down with it, for a config that has nothing to do with the
attack.
"""

import subprocess

from rsyncfns import (
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
# Deliberately a sibling of the module root, not inside it.
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
