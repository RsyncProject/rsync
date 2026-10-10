#!/usr/bin/env python3

import os
import socket as _socket

from harness.rsync import (
    SCRATCHDIR, assert_same, rmtree, run_rsync, test_fail, test_skipped,
)

base = SCRATCHDIR / 'nested-socket'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
(src / 'sub').mkdir(parents=True)
(src / 'sub' / 'f.txt').write_text('regular\n')
(src / 'top.txt').write_text('top\n')

s = _socket.socket(_socket.AF_UNIX)
prev = os.getcwd()
try:
    os.chdir(src / 'sub')
    s.bind('thesock')
except OSError as e:
    test_skipped(f"cannot create a unix socket fixture ({e})")
finally:
    try:
        os.chdir(prev)
    except OSError:
        pass
    s.close()

run_rsync('-a', f'{src}/', f'{dest}/')

for rel in ('sub/f.txt', 'top.txt'):
    got = dest / rel
    if not got.is_file():
        test_fail(f"a regular file was lost alongside the nested socket: {got}")
    assert_same(src / rel, got, label=rel)

print("nested-socket-specials: a nested unix socket does not fail the transfer")
