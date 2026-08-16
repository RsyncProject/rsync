#!/usr/bin/env python3
# --write-batch symlink policy matrix (batch.c open site, shared with
# --read-batch / --only-write-batch).  Already refuses a foreign-owned symlink
# but does not honor --insecure-links -- RED on the insecure cells.

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
        rsync_argv('-a', f'--write-batch={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Followed == the batch file was written through the symlink, out-of-tree.
    return victim.is_file()


run_symlink_matrix('--write-batch', case)
print("--write-batch symlink policy matrix: enforced")
