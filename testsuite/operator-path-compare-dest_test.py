#!/usr/bin/env python3
# --compare-dest symlink policy matrix.  A followed --compare-dest symlink lets
# the basis lookup read an out-of-tree directory; a match there makes rsync treat
# the file as already present and SKIP creating it in dest (out-of-tree read).
# The ownership walk must follow only uid0/euid-owned symlinks; --insecure-links
# is the local opt-out.

import os
import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink

T = 1234567890


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("BASIS-MATCH\n")
    opt, escape = plant_operator_symlink(ctx, dest)
    escape.mkdir(parents=True, exist_ok=True)
    (escape / 'f0').write_text("BASIS-MATCH\n")
    os.utime(src / 'f0', (T, T))
    os.utime(escape / 'f0', (T, T))
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--compare-dest={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Followed == the out-of-tree basis matched, so f0 was skipped (not created).
    return not (dest / 'f0').exists()


run_symlink_matrix('--compare-dest', case)
print("--compare-dest symlink policy matrix: enforced")
