#!/usr/bin/env python3
"""Regression test for issue #231: high-numbered I/O descriptors.

rsync's I/O readiness loops used to call select() with fd_set bitmaps, which can
only represent descriptors below FD_SETSIZE.  If rsync is started with many
descriptors already open -- e.g. inherited from a parent that leaked fds -- its
own socket and pipe fds get allocated at or above that limit.  FD_SET() and
FD_ISSET() then index past the end of the fixed-size fd_set, which is undefined
behaviour: with a plain build select() reports the fd ready while FD_ISSET()
reads the out-of-bounds bit as 0, so the read/write never happens and rsync
spins at 100% CPU forever; with a fortified libc the process instead aborts
("bit out of range 0 - FD_SETSIZE in fd_set").  Either way the transfer never
completes.

We reproduce that by opening enough inheritable dummy fds to push rsync's
descriptors past FD_SETSIZE, then running an ordinary transfer with
close_fds=False so the child inherits them.  With the poll()-based I/O the
transfer finishes immediately; without it, it hangs or aborts.

FD_SETSIZE is *not* hardcoded: it is 1024 with glibc but 65536 on 64-bit
Solaris, and assuming 1024 there would open too few fds to cross the limit and
pass vacuously.  We ask the C library for the real value instead, and skip when
we cannot (no compiler, or the fd limit cannot be raised far enough).
"""

import os
import resource
import shlex
import shutil
import subprocess
import tempfile

from rsyncfns import (
    FROMDIR, TODIR, rmtree, rsync_argv, test_fail, test_skipped,
)

TIMEOUT = 30  # poll() build: ~instant; the bug: hangs (or aborts)


def probe_fd_setsize():
    """Ask the C library for FD_SETSIZE rather than assuming a value."""
    # CC is a command, not a filename: "ccache gcc" and "gcc -m32" are both
    # ordinary values, and passing either to subprocess as one argv[0] looks
    # for a program with a space in its name.
    cc = shlex.split(os.environ.get('CC') or '')
    if not cc:
        found = shutil.which('cc') or shutil.which('gcc')
        if not found:
            return None
        cc = [found]
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'fdss.c')
        exe = os.path.join(td, 'fdss')
        with open(src, 'w') as f:
            f.write('#include <stdio.h>\n'
                    '#include <sys/select.h>\n'
                    'int main(void){ printf("%d\\n", (int)FD_SETSIZE); return 0; }\n')
        try:
            if subprocess.run(cc + [src, '-o', exe],
                              capture_output=True).returncode != 0:
                return None
            proc = subprocess.run([exe], capture_output=True, text=True)
        except OSError:
            return None          # CC names something we cannot execute
        if proc.returncode != 0:
            return None
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return None


fd_setsize = probe_fd_setsize()
if not fd_setsize:
    test_skipped("could not determine FD_SETSIZE (no usable C compiler)")

# Push rsync's descriptors comfortably past the limit.
ndummy = fd_setsize + 80
want = ndummy + 64

soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if soft < want:
    if hard != resource.RLIM_INFINITY and hard < want:
        test_skipped(f"RLIMIT_NOFILE hard cap {hard} < {want}; cannot place fds "
                     f"above FD_SETSIZE ({fd_setsize}) to exercise issue #231")
    resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

payload = {f'f{i}': os.urandom(1000) for i in range(20)}
for name, data in payload.items():
    (FROMDIR / name).write_bytes(data)

# Occupy the low fd numbers with inheritable dummy descriptors so the rsync
# child's socket/pipe fds land above FD_SETSIZE.  Python opens fds O_CLOEXEC by
# default, so mark each inheritable and use close_fds=False.
dummies = []
try:
    while True:
        fd = os.open(os.devnull, os.O_RDONLY)
        os.set_inheritable(fd, True)
        dummies.append(fd)
        if fd >= ndummy:
            break

    argv = rsync_argv('-a', f'{FROMDIR}/', f'{TODIR}/')
    try:
        proc = subprocess.run(argv, timeout=TIMEOUT, close_fds=False)
    except subprocess.TimeoutExpired:
        test_fail(f"rsync did not finish within {TIMEOUT}s with fds above "
                  f"FD_SETSIZE ({fd_setsize}) -- select()/fd_set overflow "
                  "(issue #231 regression)")
finally:
    for fd in dummies:
        os.close(fd)

if proc.returncode != 0:
    test_fail(f"rsync exited {proc.returncode} with fds above FD_SETSIZE "
              f"({fd_setsize}); a fortified libc aborts on the fd_set overflow "
              "(issue #231 regression)")

for name, data in payload.items():
    if (TODIR / name).read_bytes() != data:
        test_fail(f"{name} differs after a high-fd transfer")

print(f"issue #231: transfer with fds above FD_SETSIZE ({fd_setsize}) "
      "completed correctly")
