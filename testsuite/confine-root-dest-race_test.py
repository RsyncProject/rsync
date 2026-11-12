#!/usr/bin/env python3
# Regression test for --confine-root main-transfer destination confinement.

import os
import subprocess
import time

from rsyncfns import (
    RSYNC,
    SCRATCHDIR,
    race_budget,
    rmtree,
    rsync_argv,
    rsync_path_arg,
    start_c_flipper,
    stop_flipper,
    test_fail,
)

dir_root = SCRATCHDIR / 'dir'
outside = SCRATCHDIR / 'outside'
src_dir = SCRATCHDIR / 'src_files'
sub = dir_root / 'sub'
sublink = dir_root / 'sublink'
rsh = SCRATCHDIR / 'rsh.sh'

rsh.write_text(f"""#!/bin/sh
while [ $# -gt 0 ] && [ "$1" != "--server" ]; do
    shift
done
cd "{dir_root}" || exit 1
exec {rsync_path_arg(RSYNC)} --confine-root="{dir_root}" "$@"
""")
os.chmod(rsh, 0o755)

def build():
    for d in (dir_root, outside, src_dir):
        rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    sub.mkdir(parents=True, exist_ok=True)
    (src_dir / 'test').write_text("PAYLOAD\n")
    (outside / 'test').write_text("VICTIM\n")
    os.symlink(str(outside), sublink)

def push_server(remote_dest, *extra):
    subprocess.run(
        rsync_argv('-I', *extra, '-e', str(rsh),
                   str(src_dir / 'test'), f'h:{remote_dest}'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

# ---- 0. SANITY CHECK (Valid in-tree server transfer MUST succeed) ----------
build()

push_server('sub/ok_server')
if not (sub / 'ok_server').is_file() or (sub / 'ok_server').read_text() != "PAYLOAD\n":
    test_fail("sanity failed: server transfer into confined sub/ok_server failed")

# ---- 1. DETERMINISTIC CHECKS -----------------------------------------------
build()

# 1a. Server symlink escape: destination sublink/test
push_server('sublink/test')
if (outside / 'test').read_text() != "VICTIM\n":
    test_fail("server confine-root failed to block write through in-tree symlink sublink/test")

# 1b. Server '--inplace' symlink escape
push_server('sublink/test', '--inplace')
if (outside / 'test').read_text() != "VICTIM\n":
    test_fail("server confine-root --inplace failed to block write through sublink/test")

# 1c. Server relative '..' escape: ../outside/test
push_server('../outside/test')
if (outside / 'test').read_text() != "VICTIM\n":
    test_fail("server confine-root failed to block relative destination ../outside/test")

# 1d. Server new file creation outside DIR (O_CREAT path)
push_server('../outside/newfile')
if (outside / 'newfile').exists():
    test_fail("server confine-root failed to block creating ../outside/newfile")

# 1e. Server new directory creation outside DIR
push_server('../outside/newdir/test')
if (outside / 'newdir').exists():
    test_fail("server confine-root failed to block creating ../outside/newdir")

# ---- 2. THE LIVE RACE ------------------------------------------------------
deadline = time.monotonic() + race_budget(10.0)
flip = None
try:
    while time.monotonic() < deadline:
        if flip is not None:
            stop_flipper(flip)
            flip = None

        build()
        flip = start_c_flipper(sub, sublink)
        for _ in range(20):
            push_server('sub/test')
            if (outside / 'test').read_text() != "VICTIM\n":
                test_fail("confine-root race escape: wrote outside/test during parent flip")
finally:
    if flip is not None:
        stop_flipper(flip)
