#!/usr/bin/env python3

import os
import subprocess
import time

from harness.mutation import race_budget, start_path_flipper, stop_flipper
from harness.rsync import (
    SCRATCHDIR, patched_rrsync, proc_self_fd_pins, rmtree, test_fail, test_skipped,
)

if not proc_self_fd_pins():
    test_skipped("rrsync's realpath-vs-exec inode-pin needs /proc/self/fd "
                 "(Linux); unhardened fallback elsewhere by design", capability='proc_fd')

MARKER = b"AUDIT_RRSYNC_RACE_LEAK_MARKER_DO_NOT_DISCLOSE"

base = SCRATCHDIR / 'rrsync_race'
restricted = base / 'restricted'
outside = base / 'outside'
rmtree(base)
restricted.mkdir(parents=True)
outside.mkdir(parents=True)

(outside / 'target').write_bytes(MARKER)

real_dir = restricted / 'dir'
real_dir.mkdir()
(real_dir / 'target').write_bytes(b"benign_in_tree_content\n")
evil_path = restricted / 'evil'
os.symlink(str(outside), evil_path)

fake_rsync = base / 'fake_rsync'
fake_rsync.write_text(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "try:\n"
    "    with open(sys.argv[-1], 'rb') as f:\n"
    "        sys.stdout.buffer.write(f.read())\n"
    "except OSError:\n"
    "    pass\n"
)
fake_rsync.chmod(0o755)

test_rrsync = patched_rrsync(base, rsync_path=str(fake_rsync))

def race(rrsync_flags, ssh_cmd):
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': ssh_cmd}
    deadline = time.monotonic() + race_budget()
    while time.monotonic() < deadline:
        proc = subprocess.run(
            [str(test_rrsync), *rrsync_flags, '-no-lock', str(restricted)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        if MARKER in proc.stdout:
            return True
    return False

flip = start_path_flipper(real_dir, evil_path)
try:
    leaked = race(['-ro'], "rsync --server --sender -lt . dir/target")
finally:
    stop_flipper(flip)

if leaked:
    test_fail(
        f'rrsync emitted {MARKER!r} after a parent path changed to an outside symlink')

print('rrsync pins intermediate sender path components against the realpath-vs-exec race')
