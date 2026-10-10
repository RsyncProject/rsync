#!/usr/bin/env python3

import subprocess

from harness.mutation import plant_operator_symlink, run_symlink_matrix
from harness.rsync import rsync_argv

NFILES = 6

def check(label, inplace):
    def case(ctx):
        src = ctx.base / 'src'
        dest = ctx.base / 'dest'
        src.mkdir()
        dest.mkdir()
        for i in range(NFILES):
            (src / f'f{i}').write_text('new-content\n')
            (dest / f'f{i}').write_text('old\n')
        backup, _ = plant_operator_symlink(ctx, dest)
        args = ['-a', '--backup', f'--backup-dir={backup}']
        if inplace:
            args.append('--inplace')
        if ctx.insecure:
            args.append('--insecure-links')
        subprocess.run(rsync_argv(*args, 'src/', 'dest/'), cwd=ctx.base,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return any(path.is_file() for path in ctx.outside.rglob('*'))

    run_symlink_matrix(label, case)

check('--backup-dir', False)
check('--inplace --backup-dir', True)
print('--backup-dir symlink policy enforced')
