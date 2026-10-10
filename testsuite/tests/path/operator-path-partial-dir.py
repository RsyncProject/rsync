#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import rsync_argv, run_symlink_matrix, plant_operator_symlink

PINNED = 1000000000

def _setup(ctx):
    src = ctx.base / 'src'
    dest = ctx.base / 'dest'
    src.mkdir()
    dest.mkdir()
    (src / 'f0').write_text("PAYLOAD-DATA\n")
    return plant_operator_symlink(ctx, dest)

def _run(ctx, opt):
    extra = ['--insecure-links'] if ctx.insecure else []
    subprocess.run(
        rsync_argv('-a', '--delay-updates', f'--partial-dir={opt}', *extra,
                   'src/', 'dest/'),
        cwd=str(ctx.base), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def case_create(ctx):
    opt, escape = _setup(ctx)
    _run(ctx, opt)
    return escape.exists()

def case_reuse(ctx):
    opt, escape = _setup(ctx)
    escape.mkdir(parents=True, exist_ok=True)
    os.utime(escape, (PINNED, PINNED))
    pinned = escape.stat().st_mtime
    _run(ctx, opt)
    return escape.stat().st_mtime != pinned

run_symlink_matrix('--partial-dir', case_create, paths=('abs',), wheres=('parent',),
                   label='create')
run_symlink_matrix('--partial-dir', case_reuse, paths=('abs',), wheres=('parent',),
                   label='reuse')
print("--partial-dir symlink policy (abs, parent; create + reuse): enforced")
