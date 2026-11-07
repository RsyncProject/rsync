#!/usr/bin/env python3

import os
import subprocess

from harness.mutation import plant_operator_symlink, run_symlink_matrix
from harness.rsync import rsync_argv

PINNED = 1000000000

def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("PAYLOAD-DATA\n")
    opt, escape = plant_operator_symlink(ctx, dest)
    escape.mkdir(parents=True, exist_ok=True)
    os.utime(escape, (PINNED, PINNED))
    pinned = escape.stat().st_mtime
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--temp-dir={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return escape.stat().st_mtime != pinned

run_symlink_matrix('--temp-dir', case)
print("--temp-dir symlink policy matrix: enforced")
