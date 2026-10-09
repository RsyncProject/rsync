#!/usr/bin/env python3
# --partial-dir symlink policy (absolute, parent-component), create and reuse.
#
# With --delay-updates the receiver stages every file in the --partial-dir before
# the final rename, so handle_partial_dir() must resolve (and, on first use,
# create) the partial dir for each file.  An ABSOLUTE --partial-dir whose parent
# component is a symlink was followed by the bare do_*_at() path syscalls (an
# absolute path is operator-trusted), so a foreign-owned parent symlink
# redirected the staging dir -- and the staged file -- out of the tree.
#
# The fix routes handle_partial_dir() through the operator-path ownership walk:
# it refuses a foreign-owned parent symlink for both the do_mkdir_at() that
# creates the partial dir and the do_lstat_at() that reuses an existing one, so
# nothing is staged out of tree.  --insecure-links opts out locally.  Restricted
# to {abs} x {parent}: a relative partial dir is already confined by
# robust_rename()'s strict resolver, and the leaf form is replaced by a real
# in-tree dir (the leaf is opened O_NOFOLLOW), so neither models this escape.
#
# escape is the partial dir itself: opt's parent component (opd) is the planted
# symlink, so opt=<plant>/opd/sub resolves to <outside>/sub.  Two cells, because
# the gate guards two distinct syscalls on that path:
#   create -- the partial dir does not exist yet, so the receiver must mkdir it
#             through the parent symlink; followed == the out-of-tree leaf appears.
#   reuse  -- the partial dir already exists (the common case for a reserved
#             absolute partial-dir reused across runs), so the receiver lstats and
#             stages into it; followed == the staged file advances the leaf's mtime.

import os
import subprocess

from rsyncfns import rsync_argv, run_symlink_matrix, plant_operator_symlink

PINNED = 1000000000   # 2001-09-09; any later mtime means it was touched


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
    # Leaf absent: the receiver must mkdir the partial dir through the parent
    # symlink.  Followed == that mkdir landed out of tree (an absolute partial-dir
    # is not rmdir'd at the end, so the leaf survives to be observed).
    opt, escape = _setup(ctx)
    _run(ctx, opt)
    return escape.exists()


def case_reuse(ctx):
    # Leaf present: the receiver lstats the existing partial dir and stages into
    # it.  Followed == the staged file advances the out-of-tree leaf's mtime.
    opt, escape = _setup(ctx)
    escape.mkdir(parents=True, exist_ok=True)
    # Pinned epoch rather than a sub-second delta: on a 1-second-granularity
    # filesystem such as HFS+ a same-second change is invisible, and the delta
    # test then reports a followed symlink as refused.
    os.utime(escape, (PINNED, PINNED))
    pinned = escape.stat().st_mtime   # what the fs actually stored
    _run(ctx, opt)
    return escape.stat().st_mtime != pinned


run_symlink_matrix('--partial-dir', case_create, paths=('abs',), wheres=('parent',),
                   label='create')
run_symlink_matrix('--partial-dir', case_reuse, paths=('abs',), wheres=('parent',),
                   label='reuse')
print("--partial-dir symlink policy (abs, parent; create + reuse): enforced")
