#!/usr/bin/env python3

import os
import shlex
import shutil
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, SRCDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped

if not (SRCDIR / 'flist.c').is_file() or not (SRCDIR / 'Makefile').is_file():
    test_skipped("malicious-sender-delete-scope: needs a writable rsync "
                 f"source tree at SRCDIR={SRCDIR} with Makefile + flist.c; "
                 "the patched-sender build relies on it.")
if not shutil.which('make'):
    test_skipped("malicious-sender-delete-scope: make(1) not on PATH")
if not shutil.which('gcc') and not shutil.which('cc'):
    test_skipped("malicious-sender-delete-scope: no C compiler on PATH")

mal_src = SCRATCHDIR / 'mal-rsync'
rmtree(mal_src)
try:
    shutil.copytree(SRCDIR, mal_src, symlinks=True,
                    ignore=shutil.ignore_patterns('testtmp', '.git', 'auto-build-save'))
except shutil.Error as e:
    if any(not (os.path.islink(src) and os.path.islink(dst))
           for src, dst, _why in e.args[0]):
        raise

flist_c = mal_src / 'flist.c'
PATCH_OLD = "xflags = XMIT_TOP_DIR | XMIT_NO_CONTENT_DIR;"
PATCH_NEW = "xflags = XMIT_TOP_DIR; /* malicious-sender PoC: drop NO_CONTENT_DIR */"
text = flist_c.read_text()
if PATCH_OLD not in text:
    test_skipped(
        "malicious-sender-delete-scope: could not find the implied-parent "
        "encoding literal in flist.c -- upstream rename?  Looking for: "
        f"{PATCH_OLD!r}.  Update PATCH_OLD in this test if the assignment "
        "changed shape.")
flist_c.write_text(text.replace(PATCH_OLD, PATCH_NEW, 1))

build = subprocess.run(['make', '-j2', 'rsync'], cwd=str(mal_src),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
mal_rsync = mal_src / 'rsync'
if build.returncode != 0 or not mal_rsync.is_file() or not os.access(mal_rsync, os.X_OK):
    test_skipped(
        "malicious-sender-delete-scope: malicious-sender build failed.  "
        f"`make rsync` rc={build.returncode}.  Tail of build output:\n"
        + '\n'.join(build.stdout.splitlines()[-20:]))

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
