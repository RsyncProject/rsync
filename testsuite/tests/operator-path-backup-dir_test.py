#!/usr/bin/env python3
# --backup-dir symlink policy matrix.  An operator --backup-dir must follow a
# symlink only when owned by uid 0 or the euid, refusing an attacker's plant --
# absolute AND relative, leaf and parent, with --insecure-links as the local
# opt-out.  See run_symlink_matrix() in rsyncfns.  RED on this branch: absolute
# cross-uid follows the attacker symlink (escape); relative refuses even the
# operator's own symlink (diverges from the uniform policy).

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
        rsync_argv('-a', '--backup', f'--backup-dir={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return any(p.is_file() for p in ctx.outside.rglob('*'))  # backup escaped?


run_symlink_matrix('--backup-dir', case)
print("--backup-dir symlink policy matrix: enforced")
