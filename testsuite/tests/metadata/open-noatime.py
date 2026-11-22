#!/usr/bin/env python3

import datetime
import os
import platform
import shlex
import subprocess

from harness import environment
from harness.environment import FROMDIR, TMPDIR, TODIR, TOOLDIR
from harness.filesystem import assert_same
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness import metadata

metadata(features={'noatime'}, transports={'pipe'})

vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' not in vv.stdout:
    test_skipped("Rsync is configured without atimes support", capability='noatime')

if platform.system() != 'Linux':
    test_skipped("O_NOATIME is only supported on Linux", capability='noatime')

FROMDIR.mkdir(parents=True, exist_ok=True)
foo = FROMDIR / 'foo'
foo.write_text("content\n")

atime = datetime.datetime(2001, 2, 3, 17, 17, 42).timestamp()
mtime_ns = foo.stat().st_mtime_ns
os.utime(foo, ns=(int(atime * 1_000_000_000), mtime_ns))
original_atime = foo.stat().st_atime_ns
foo.read_bytes()
if foo.stat().st_atime_ns == original_atime:
    test_skipped("filesystem does not update access times", capability='noatime')
os.utime(foo, ns=(original_atime, mtime_ns))

environment.TLS_ARGS = '--atimes'

def _tls_listing(path: str) -> str:
    cmd = [str(TOOLDIR / 'tls')] + shlex.split(environment.TLS_ARGS) + [str(path)]
    return subprocess.check_output(cmd, text=True)

before = _tls_listing(foo)
(TMPDIR / 'atime-from-before').write_text(before)

run_rsync('--open-noatime', '--archive', '--recursive', '--times',
          '--atimes', '-vvv', f'{FROMDIR}/', f'{TODIR}/')

after = _tls_listing(foo)
(TMPDIR / 'atime-from-after').write_text(after)

if before != after:
    diff = subprocess.run(
        ['diff', '-u',
         str(TMPDIR / 'atime-from-before'),
         str(TMPDIR / 'atime-from-after')],
        capture_output=True, text=True,
    )
    print(diff.stdout)
    test_fail("source atime changed across rsync --open-noatime run")
assert_same(TODIR / foo.name, foo, label='--open-noatime transfer')
