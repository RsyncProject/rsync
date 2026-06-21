#!/usr/bin/env python3
# --temp-dir symlink policy matrix.  A followed --temp-dir symlink makes the
# receiver create its scratch temp file in an out-of-tree directory (the data is
# written there, then renamed to dest).  The temp is renamed away, so we detect
# the escape by the target directory's mtime advancing (a file was created in
# it).  The ownership walk must follow only uid0/euid-owned symlinks; a relative
# --temp-dir anchors at the cwd.  --insecure-links is the local opt-out.

import subprocess
import time

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink


def case(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("PAYLOAD-DATA\n")
    # A relative --temp-dir is resolved by the receiver, whose cwd is the
    # destination directory -- so a relative plant anchors at dest, not the cwd.
    opt, escape = plant_operator_symlink(ctx, dest)
    escape.mkdir(parents=True, exist_ok=True)            # --temp-dir must exist
    before = escape.stat().st_mtime_ns
    time.sleep(0.01)
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', f'--temp-dir={opt}', *extra, 'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Followed == the receiver created its temp in the out-of-tree dir.
    return escape.stat().st_mtime_ns != before


run_symlink_matrix('--temp-dir', case)
print("--temp-dir symlink policy matrix: enforced")
