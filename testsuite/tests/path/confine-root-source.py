#!/usr/bin/env python3
"""Regression test for --confine-root sender source confinement.

Verifies that a server sender running under --confine-root refuses to
traverse out of the confined root via a pre-existing ("trusted") in-tree
symlink or a relative '..' source argument during a pull transfer.
"""

import os
import subprocess

from harness.process import quote_command
from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness import metadata

metadata(transports={'pipe'})

base = SCRATCHDIR / 'confine-root-source'
rmtree(base)
base.mkdir(parents=True, exist_ok=True)

dir_root = base / 'dir'
outside = base / 'outside'
dest_dir = base / 'dest'
sub = dir_root / 'sub'
sublink = dir_root / 'sublink'
rsh = base / 'rsh.sh'

rsh.write_text(f"""#!/bin/sh
while [ $# -gt 0 ] && [ "$1" != "--server" ]; do
    shift
done
cd "{dir_root}" || exit 1
exec {quote_command(rsync_argv())} --confine-root="{dir_root}" "$@"
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


# 0. Sanity check: valid in-tree sender pull must succeed.
build()

pull_server('sub/ok_file')
if not (dest_dir / 'ok_file').is_file() or (dest_dir / 'ok_file').read_text() != "ALLOWED\n":
    test_fail("sanity failed: pull from confined sender sub/ok_file failed")

# 1. Static source symlink and traversal checks.
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
