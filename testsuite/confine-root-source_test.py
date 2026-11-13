#!/usr/bin/env python3
# Regression test for --confine-root sender source confinement.
# Verifies that a server sender running under --confine-root refuses to
# traverse out of the confined root via a pre-existing ("trusted") in-tree
# symlink or a relative '..' source argument during a pull transfer.

import os
import subprocess

from rsyncfns import (
    RSYNC,
    SCRATCHDIR,
    rmtree,
    rsync_argv,
    rsync_path_arg,
    test_fail,
)

dir_root = SCRATCHDIR / 'dir'
outside = SCRATCHDIR / 'outside'
dest_dir = SCRATCHDIR / 'dest'
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
    for d in (dir_root, outside, dest_dir):
        rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    sub.mkdir(parents=True, exist_ok=True)
    (sub / 'ok_file').write_text("ALLOWED\n")
    (outside / 'secret').write_text("SECRET\n")
    os.symlink(str(outside), sublink)

def pull_server(remote_src, *extra):
    rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        rsync_argv('-I', *extra, '-e', str(rsh),
                   f'h:{remote_src}', f'{dest_dir}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

# ---- 0. SANITY CHECK (Valid in-tree sender pull MUST succeed) --------------
build()

pull_server('sub/ok_file')
if not (dest_dir / 'ok_file').is_file() or (dest_dir / 'ok_file').read_text() != "ALLOWED\n":
    test_fail("sanity failed: pull from confined sender sub/ok_file failed")

# ---- 1. STATIC SOURCE SYMLINK & TRAVERSAL CHECKS ---------------------------
build()

# 1a. Pre-existing ("trusted") in-tree symlink pointing outside the root
# (pulling a file through the symlinked directory: sublink/secret)
pull_server('sublink/secret')
if (dest_dir / 'secret').exists():
    test_fail("sender confine-root failed to block pull through in-tree symlink sublink/secret")

# 1b. Pre-existing ("trusted") in-tree symlink as a directory source with
# trailing slash (forces change_dir into the symlink target: sublink/)
pull_server('sublink/', '-r')
if (dest_dir / 'secret').exists():
    test_fail("sender confine-root failed to block recursive pull of symlinked dir sublink/")

# 1c. Dereferencing in-tree symlink via --copy-links (-L)
pull_server('sublink/secret', '-L')
if (dest_dir / 'secret').exists():
    test_fail("sender confine-root -L failed to block pull through sublink/secret")

# 1d. Relative '..' source escape: ../outside/secret
pull_server('../outside/secret')
if (dest_dir / 'secret').exists():
    test_fail("sender confine-root failed to block relative source ../outside/secret")

# 1e. Nested '..' source escape: sub/../../outside/secret
pull_server('sub/../../outside/secret')
if (dest_dir / 'secret').exists():
    test_fail("sender confine-root failed to block nested relative source sub/../../outside/secret")
