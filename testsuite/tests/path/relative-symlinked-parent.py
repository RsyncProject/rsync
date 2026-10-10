#!/usr/bin/env python3

import subprocess

from harness.rsync import SCRATCHDIR, assert_same, makepath, rmtree, rsync_argv, test_fail

for name, alias, target in (
    ('direct', 'link', 'real'),
    ('dotdot', 'deep/alias', '../real'),
):
    base = SCRATCHDIR / name
    source = base / 'src' / 'real' / 'sub' / 'file'
    link = base / 'src' / alias
    rmtree(base)
    makepath(source.parent, link.parent, base / 'dest')
    source.write_text('payload\n')
    link.symlink_to(target)
    argument = f'src/{alias}/sub/file'
    subprocess.run(rsync_argv('-aR', argument, 'dest/'), cwd=base, check=True)
    landed = base / 'dest' / argument
    if not landed.is_file():
        test_fail(f'{name}: -aR dropped a file below an in-tree symlink')
    assert_same(source, landed, label=name)

print('-aR follows in-tree symlinked parents')
