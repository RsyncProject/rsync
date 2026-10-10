#!/usr/bin/env python3

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, checkit, make_tree, rmtree, start_test_daemon, test_fail, test_skipped,
    walk_files,
)

DEPTH = 70
DAEMON_PORT = 12905

base = SCRATCHDIR / 'deep-path'
src = base / 'src'
rmtree(base)

try:
    make_tree(src, depth=DEPTH, data=True)
except OSError as e:
    test_skipped(f"cannot build a {DEPTH}-deep tree ({e})")

deepest = max(walk_files(src), key=lambda p: len(p.relative_to(src).parts))
parent_components = len(deepest.relative_to(src).parts) - 1
if parent_components <= 64:
    test_fail(f"deep tree only {parent_components} components deep -- "
              "does not cross DPC_MAXDEPTH (64); test is ineffective")

dest = base / 'dest'
checkit(['-a', f'{src}/', str(dest)], src, dest)

mod = base / 'module'
mod.mkdir(parents=True, exist_ok=True)
conf = write_daemon_conf([
    ('deep', {'path': str(mod), 'use chroot': 'no', 'read only': 'no'}),
])
daemon_url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')
checkit(['-a', f'{src}/', f'{daemon_url}/deep/'], src, mod, allowed_codes=(0, 23))

print(f"deep-path: {DEPTH}-deep tree round-trips past the dir-fd cache cap "
      "(local + daemon)")
