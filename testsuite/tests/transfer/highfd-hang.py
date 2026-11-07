#!/usr/bin/env python3

import os
import resource
import shlex
import shutil
import subprocess
import tempfile

from harness.rsync import (
    FROMDIR, TODIR, rmtree, rsync_argv, test_fail, test_skipped,
)

TIMEOUT = 30

def probe_fd_setsize():
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
            return None
        if proc.returncode != 0:
            return None
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return None

fd_setsize = probe_fd_setsize()
if not fd_setsize:
    test_skipped("could not determine FD_SETSIZE (no usable C compiler)", capability='high_fd')

ndummy = fd_setsize + 80
want = ndummy + 64

soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if soft < want:
    if hard != resource.RLIM_INFINITY and hard < want:
        test_skipped(f"RLIMIT_NOFILE hard cap {hard} < {want}; cannot place fds "
                     f"above FD_SETSIZE ({fd_setsize}) to exercise issue #231",
                     capability='high_fd')
    resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

payload = {f'f{i}': os.urandom(1000) for i in range(20)}
for name, data in payload.items():
    (FROMDIR / name).write_bytes(data)

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
