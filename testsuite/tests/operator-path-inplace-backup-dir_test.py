#!/usr/bin/env python3
# --inplace --backup-dir symlink policy matrix.
#
# With --inplace the generator makes the backup itself (it bypasses
# make_backup(), creating/opening the backup file directly via copy_file() /
# do_open_at() on the --backup-dir path), a separate code path from the plain
# --backup case in operator-path-backup-dir.  The same operator-path policy must
# hold: a --backup-dir symlink is followed only when owned by uid 0 or the euid,
# an attacker's plant is refused -- absolute and relative, leaf and parent, with
# --insecure-links as the local opt-out.

import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink

NFILES = 6


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    for i in range(NFILES):
        (src / f'f{i}').write_text("NEW-PUSHED-CONTENT\n")  # distinct -> overwrite -> backup
        (dest / f'f{i}').write_text("OLD\n")
    opt, _escape = plant_operator_symlink(ctx, dest)  # rel --backup-dir anchors at dest
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', '--inplace', '--backup', f'--backup-dir={opt}', *extra,
                   'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return any(p.is_file() for p in ctx.outside.rglob('*'))  # backup escaped?


run_symlink_matrix('--inplace --backup-dir', case)
print("--inplace --backup-dir symlink policy matrix: enforced")
