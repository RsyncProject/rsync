#!/usr/bin/env python3
"""Regression test for issue #231: rsync must not hang when its I/O file
descriptors are numbered at/above FD_SETSIZE.

rsync's I/O loops in io.c used to call select() with fd_set bitmaps, which can
only represent descriptors below FD_SETSIZE (1024 with glibc).  If rsync is
started with many descriptors already open -- e.g. inherited from a parent that
leaked fds -- its own socket/pipe fds get allocated above 1024.  FD_SET() and
FD_ISSET() then index past the end of the fixed-size fd_set, which is undefined
behavior: select() reports the fd ready, but FD_ISSET() reads the out-of-bounds
bit as 0, so the read/write never happens and rsync spins at 100% CPU forever
("rsync hangs, 100% of one CPU, no progress").

We reproduce that deterministically by opening enough inheritable dummy fds to
push rsync's descriptors past FD_SETSIZE, then running an ordinary transfer
with close_fds=False so the child inherits them.  With the poll()-based io.c
the transfer finishes instantly; with the old select()-based code it never
finishes and is caught by the timeout.  The cross-over is binary (instant vs.
infinite), so the timeout is a robust signal rather than a timing race.
"""

import os
import resource
import subprocess

from rsyncfns import (
    FROMDIR, TODIR, rmtree, rsync_argv, test_fail, test_skipped,
)

# select()'s fd_set holds FD_SETSIZE bits; glibc and most libcs use 1024.
FD_SETSIZE = 1024
NDUMMY = FD_SETSIZE + 80      # force rsync's fds comfortably past the limit
TIMEOUT = 30                  # poll build: ~instant; select build: hangs forever

# We must be able to open enough descriptors to cross FD_SETSIZE.
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
want = NDUMMY + 64
if soft < want:
    if hard != resource.RLIM_INFINITY and hard < want:
        test_skipped(f"RLIMIT_NOFILE hard cap {hard} < {want}; cannot place "
                     "fds above FD_SETSIZE to exercise issue #231")
    resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))

# A small tree to transfer.
rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
payload = {f"f{i}": os.urandom(1000) for i in range(20)}
for name, data in payload.items():
    (FROMDIR / name).write_bytes(data)

# Occupy the low fd numbers with inheritable dummy descriptors so that the
# rsync child's socket/pipe fds are forced above FD_SETSIZE.  Python opens fds
# O_CLOEXEC by default, so mark each inheritable and use close_fds=False.
dummies = []
try:
    while True:
        fd = os.open(os.devnull, os.O_RDONLY)
        os.set_inheritable(fd, True)
        dummies.append(fd)
        if fd >= NDUMMY:
            break

    argv = rsync_argv('-a', f'{FROMDIR}/', f'{TODIR}/')
    try:
        proc = subprocess.run(argv, timeout=TIMEOUT, close_fds=False)
    except subprocess.TimeoutExpired:
        test_fail("rsync hung with high-numbered fds -- select()/fd_set "
                  "overflow (issue #231 regression)")
finally:
    for fd in dummies:
        os.close(fd)

if proc.returncode != 0:
    test_fail(f"rsync exited {proc.returncode}: {' '.join(argv)}")

# The cap only matters if it stayed correct: verify every file transferred.
for name, data in payload.items():
    got = (TODIR / name).read_bytes()
    if got != data:
        test_fail(f"{name} differs after a high-fd transfer")

print(f"issue #231: transfer with fds >= {NDUMMY} (above FD_SETSIZE) "
      "completed correctly")
