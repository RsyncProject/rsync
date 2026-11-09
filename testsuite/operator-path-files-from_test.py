#!/usr/bin/env python3
# --files-from symlink policy matrix (also covers the shared open site used by
# --include-from / --exclude-from / --filter merge in exclude.c / options.c).
# Already refuses a foreign-owned symlink but does not honor --insecure-links --
# RED on the insecure cells.

import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'sentinel').write_text("X\n")
    opt, victim = plant_operator_symlink(ctx, ctx.base, kind='file')
    victim.write_text("sentinel\n")   # attacker-controlled transfer list
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--files-from={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Followed == rsync read the out-of-tree list and transferred its named file.
    return (dest / 'sentinel').exists()


run_symlink_matrix('--files-from', case)
print("--files-from symlink policy matrix: enforced")
