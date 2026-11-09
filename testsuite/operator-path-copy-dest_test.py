#!/usr/bin/env python3
# --copy-dest symlink policy matrix.  A followed --copy-dest symlink lets the
# basis lookup read an out-of-tree directory and COPY a matching file from it
# into dest.  The basis here has the same size+mtime but DISTINCT content, so a
# followed symlink yields the basis content (proving the out-of-tree read); a
# confined one transfers the real source content.  --insecure-links opts out.

import os
import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink

T = 1234567890


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("SRC1234\n")
    opt, escape = plant_operator_symlink(ctx, dest)
    escape.mkdir(parents=True, exist_ok=True)
    (escape / 'f0').write_text("OUT5678\n")   # same length, DISTINCT content
    os.utime(src / 'f0', (T, T))
    os.utime(escape / 'f0', (T, T))
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--copy-dest={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    df = dest / 'f0'
    # Followed == dest got the out-of-tree basis content (copied), not the source.
    return df.exists() and df.read_text() == "OUT5678\n"


run_symlink_matrix('--copy-dest', case)
print("--copy-dest symlink policy matrix: enforced")
