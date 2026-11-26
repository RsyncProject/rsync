#!/usr/bin/env python3

import os
import subprocess

from harness.daemon import claim_free_port, start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.mutation import build_patched_rsync
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness import metadata

metadata(features={'daemon', 'native_build', 'symlink'}, transports={'pipe', 'tcp'},
         mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'security'})

SYMLINK_PREFIX = '/rsyncd-munged/'
PATCH_OLD = '''static inline int link_or_rename(const char *from, const char *to,
\t\t\t\t BOOL prefer_rename, STRUCT_STAT *stp)
{
'''
PATCH_NEW = PATCH_OLD + '''\tif (S_ISLNK(stp->st_mode))
\t\treturn 0;
'''

fallback_rsync = build_patched_rsync(
    'backup-munge-fallback-rsync', [('backup.c', PATCH_OLD, PATCH_NEW)])

base = SCRATCHDIR / 'backup-munge-symlinks'
src = base / 'src'
module = base / 'module'
dest = module / 'dest'
backup = module / 'backup'
rmtree(base)
for path in (src, dest, backup):
    path.mkdir(parents=True)

conf = write_daemon_conf([
    ('m', {
        'path': module,
        'read only': 'no',
        'use chroot': 'no',
        'munge symlinks': 'yes',
    }),
], name='backup-munge-symlinks.conf')
url = start_test_daemon(conf, claim_free_port(13334), rsync_cmd=str(fallback_rsync))

def check_case(label, existing_target, expected_backup):
    for path in (src, dest, backup):
        rmtree(path)
        path.mkdir(parents=True)
    (src / 'link').symlink_to('replacement')
    (dest / 'link').symlink_to(existing_target)

    result = subprocess.run(
        rsync_argv('-I', '-l', '-b', '--backup-dir=/backup/',
                   str(src / 'link'), f'{url}m/dest/'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        test_fail(f'{label}: transfer failed: {result.stderr!r}')

    stored = os.readlink(dest / 'link')
    if stored != SYMLINK_PREFIX + 'replacement':
        test_fail(f'{label}: destination target is {stored!r}')
    backed_up = os.readlink(backup / 'link')
    if backed_up != expected_backup:
        test_fail(f'{label}: backup target is {backed_up!r}, expected {expected_backup!r}')

check_case('unmunged', 'secret', SYMLINK_PREFIX + 'secret')
check_case('already munged', SYMLINK_PREFIX + 'secret', SYMLINK_PREFIX + 'secret')
check_case('exact prefix', SYMLINK_PREFIX, SYMLINK_PREFIX)

print('backup-munge-symlinks: fallback symlink targets are munged exactly once')
