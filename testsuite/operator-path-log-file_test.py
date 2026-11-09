#!/usr/bin/env python3
# --log-file symlink policy matrix.  --log-file already refuses a foreign-owned
# symlink (it uses the ownership walk), but it does NOT honor --insecure-links --
# so this matrix is RED on the insecure cells: the local opt-out must restore
# legacy following uniformly across operator paths.

import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("data\n")
    opt, victim = plant_operator_symlink(ctx, ctx.base, kind='file')
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', '-i', f'--log-file={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Followed == the log was written through the symlink to the out-of-tree file.
    return victim.is_file() and victim.stat().st_size > 0


run_symlink_matrix('--log-file', case)
print("--log-file symlink policy matrix: enforced")
