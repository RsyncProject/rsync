#!/usr/bin/env python3
# Regression: `rsync -a` (implies --specials via -D) of a tree with a NESTED
# unix socket must not fail the whole transfer on platforms that cannot create
# a socket in a subdirectory race-safely (no bindat(): the BSDs, macOS,
# Solaris).  syscall.c returns EOPNOTSUPP for such a nested socket; the
# generator used to turn that into a transfer error (FERROR_XFER -> exit 23)
# where master created it via the path-based bind().  Now the socket is skipped
# with a warning and the rest of the transfer succeeds.  On Linux the socket is
# created (mknodat handles it); either way the transfer exits 0 and the regular
# files land.

import os
import socket as _socket

from rsyncfns import (
    SCRATCHDIR, assert_same, rmtree, run_rsync, test_fail, test_skipped,
)

base = SCRATCHDIR / 'nested-socket'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
(src / 'sub').mkdir(parents=True)
(src / 'sub' / 'f.txt').write_text('regular\n')
(src / 'top.txt').write_text('top\n')

# A unix socket nested in a subdirectory (the case that returned EOPNOTSUPP).
# AF_UNIX sun_path is ~108 bytes, far shorter than the scratch path, so bind a
# short relative name from inside the dir, then restore the cwd.
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

# -a implies --specials.  Must exit 0 even where the nested socket cannot be
# created -- it is skipped with a warning there (RED on the unfixed branch,
# which exited 23 on the BSDs/macOS/Solaris).
run_rsync('-a', f'{src}/', f'{dest}/')

for rel in ('sub/f.txt', 'top.txt'):
    got = dest / rel
    if not got.is_file():
        test_fail(f"a regular file was lost alongside the nested socket: {got}")
    assert_same(src / rel, got, label=rel)

print("nested-socket-specials: a nested unix socket does not fail the transfer")
