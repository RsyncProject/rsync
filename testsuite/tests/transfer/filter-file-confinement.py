#!/usr/bin/env python3

import os
import subprocess

from harness.mutation import find_attacker_uid
from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, rsync_argv, start_test_daemon, test_fail, test_skipped
from harness import metadata

metadata(features={'cross_uid'}, transports={'pipe', 'tcp'})

PORT = 13333

if os.geteuid() != 0:
    test_skipped('requires root to create a foreign-owned source link', capability='cross_uid')
attacker = find_attacker_uid()
if attacker is None:
    test_skipped('no untrusted UID for the source link', capability='cross_uid')

root = SCRATCHDIR / 'files-from'
base = root / 'base'
source = base / 'src'
destination = base / 'dest'
backup = base / 'backup'
for path in (source / 'sub', destination / 'sub', backup):
    path.mkdir(parents=True)
secret = root / 'secret'
secret.write_text('NO_ONE_SHOULD_README\n')
for directory, target in ((source, 'test'), (destination, str(secret))):
    link = directory / 'sub' / 'f0'
    link.symlink_to(target)
    os.lchown(link, attacker, attacker)

config = write_daemon_conf([('m', {'path': str(base), 'read only': 'no', 'use chroot': 'no'})],
                           name='files-from-rsyncd.conf')
url = start_test_daemon(config, PORT)
result = subprocess.run(rsync_argv('-a', '-b', '--backup-dir=/backup/',
                                  f'{source}/', f'{url}m/dest/'), capture_output=True, text=True)
if result.returncode != 0:
    test_fail(f'backup control failed: {result.returncode}\n{result.stderr}')
link = backup / 'sub' / 'f0'
if not link.is_symlink() or os.readlink(link) != str(secret):
    test_fail('the destination link was not backed up')

# A raced backup can leave a root-owned link to a peer-controlled target
os.lchown(link, 0, 0)
for option, remote in ((f'--files-from=:backup/sub/f0', f'{url}m/'),
                       ('--filter=: f0', f'{url}m/backup/sub/')):
    result = subprocess.run(rsync_argv('-a', option, remote, f'{destination}/'),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if 'NO_ONE_SHOULD_README' in result.stdout:
        test_fail(f'{secret.name} leaked:\n{result.stdout}')
