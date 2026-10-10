#!/usr/bin/env python3
# Exercise the DPC_MAXDEPTH (64) boundary of the held ancestor-dirfd cache.
#
# The receiver/generator (get_dir_fd) and the sender (held_dir_path_fd) resolve
# a path against a persistent ancestor-dirfd stack capped at DPC_MAXDEPTH=64
# path components (syscall.c).  A directory deeper than that is declined by the
# cache (dpc_dir_fd returns -1) and must fall back to the un-cached
# secure_relative_open() walk -- which handles arbitrary depth -- and still
# produce a byte-identical result with no overflow/crash at the array bound.
#
# We build a single chain with a file at EVERY level past the cap, so dir-depths
# both within the cache (<=64 components, fast path) and beyond it (>64, the
# fallback) are transferred in one run, exercising the fast-path -> fallback
# handoff on the sender AND the receiver (whose caches are independent).
# Plain local transfer + a no-chroot daemon push; no root required.

import os

from rsyncfns import (
    SCRATCHDIR, checkit, make_tree, rmtree, start_test_daemon, test_fail,
    test_skipped, walk_files, write_daemon_conf,
)

DEPTH = 70            # > DPC_MAXDEPTH (64): the deepest files use the fallback
DAEMON_PORT = 12905

base = SCRATCHDIR / 'deep-path'
src = base / 'src'
rmtree(base)

# One deep chain, a file at each level: src/f0, src/d1/f1, ..., src/d1/.../d70/f70.
try:
    make_tree(src, depth=DEPTH, data=True)
except OSError as e:
    test_skipped(f"cannot build a {DEPTH}-deep tree ({e})")

# Sanity: the deepest file's parent really has more components than the cache
# cap, so the fallback is genuinely exercised (not just the fast path).
deepest = max(walk_files(src), key=lambda p: len(p.relative_to(src).parts))
parent_components = len(deepest.relative_to(src).parts) - 1   # drop the filename
if parent_components <= 64:
    test_fail(f"deep tree only {parent_components} components deep -- "
              "does not cross DPC_MAXDEPTH (64); test is ineffective")

# --- local transfer: sender AND receiver caches both cross the boundary -----
dest = base / 'dest'
checkit(['-a', f'{src}/', str(dest)], src, dest)

# --- daemon push: the daemon receiver uses the confined resolver ------------
mod = base / 'module'
mod.mkdir(parents=True, exist_ok=True)
conf = write_daemon_conf([
    ('deep', {'path': str(mod), 'use chroot': 'no', 'read only': 'no'}),
])
daemon_url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')
checkit(['-a', f'{src}/', f'{daemon_url}/deep/'], src, mod, allowed_codes=(0, 23))

print(f"deep-path: {DEPTH}-deep tree round-trips past the dir-fd cache cap "
      "(local + daemon)")
