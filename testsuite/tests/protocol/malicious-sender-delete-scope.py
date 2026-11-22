#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.environment import SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.mutation import build_patched_rsync
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness import metadata

metadata(features={'native_build'}, transports={'pipe', 'tcp'})

PATCH_OLD = "xflags = XMIT_TOP_DIR | XMIT_NO_CONTENT_DIR;"
PATCH_NEW = "xflags = XMIT_TOP_DIR; /* malicious-sender PoC: drop NO_CONTENT_DIR */"
mal_rsync = build_patched_rsync(
    'malicious-sender-delete-scope', [('flist.c', PATCH_OLD, PATCH_NEW)])

base = SCRATCHDIR / 'mal-test'
rmtree(base)
src = base / 'src'
dest = base / 'dest'
makepath(src / 'dir')
makepath(dest / 'dir')
(src / 'dir' / 'file').write_text("from server\n")
sentinel = dest / 'dir' / 'keep.txt'
sentinel.write_text("MUST_STAY\n")

conf = write_daemon_conf(
    [('m', {'path': str(src),
            'read only': 'yes',
            'use chroot': 'no'})],
    name='mal-rsyncd.conf')
os.environ['RSYNC_CONNECT_PROG'] = f'{shlex.quote(str(mal_rsync))} --config={shlex.quote(str(conf))} --daemon'

url = 'rsync://localhost/m/dir/file'
proc = subprocess.run(
    rsync_argv('-rR', '--delete', '--debug=del2', url, str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

if not sentinel.is_file():
    debug_tail = '\n'.join(proc.stdout.splitlines()[-30:])
    test_fail(
        f'malicious sender expanded --delete scope and removed {sentinel}\n{debug_tail}')

delivered = dest / 'dir' / 'file'
if not delivered.is_file():
    test_fail(
        f"sentinel survived but the malicious daemon didn't deliver "
        f"{delivered} either -- the test is vacuous.  Receiver rc="
        f"{proc.returncode}.  Output tail:\n"
        + '\n'.join(proc.stdout.splitlines()[-20:]))

print(
    f'{sentinel.name} survived malicious --delete scope; '
    f'{delivered.relative_to(dest)} was delivered')
