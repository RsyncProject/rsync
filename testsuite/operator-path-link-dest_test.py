#!/usr/bin/env python3
# --link-dest symlink policy matrix.  A followed --link-dest symlink lets the
# basis lookup read an out-of-tree directory and HARDLINK dest files to it.  The
# operator-path ownership walk must follow only uid0/euid-owned symlinks (abs and
# rel, leaf and parent); --insecure-links is the local opt-out.

import os
import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink

T = 1234567890  # shared mtime so the quick check treats the basis as a match


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("BASIS-MATCH\n")
    opt, escape = plant_operator_symlink(ctx, dest)  # rel --link-dest anchors at dest
    escape.mkdir(parents=True, exist_ok=True)
    (escape / 'f0').write_text("BASIS-MATCH\n")       # identical out-of-tree basis
    os.utime(src / 'f0', (T, T))
    os.utime(escape / 'f0', (T, T))
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--link-dest={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    df = dest / 'f0'
    if not df.exists():
        return False
    try:
        # Followed == dest/f0 is a hardlink to the out-of-tree basis.
        return os.stat(df).st_ino == os.stat(escape / 'f0').st_ino
    except OSError:
        return False


run_symlink_matrix('--link-dest', case)
print("--link-dest symlink policy matrix: enforced")
