#!/usr/bin/env python3
# Python rewrite of testsuite/protected-regular.test.
#
# Modern Linux kernels can set fs.protected_regular = {1,2}, which
# blocks O_CREAT|O_WRONLY opens of files in world-writable sticky
# directories that the opener doesn't own. rsync --inplace must still
# be able to write into these files; this test guards that path.

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rsyncfns import TMPDIR, run_rsync, test_skipped


pr_path = Path('/proc/sys/fs/protected_regular')
if not pr_path.is_file():
    test_skipped("Can't find protected_regular setting (only available on Linux)")

try:
    pr_lvl = pr_path.read_text().strip()
except OSError:
    test_skipped("Can't check if fs.protected_regular is enabled")
if pr_lvl == '0':
    test_skipped("fs.protected_regular is not enabled")

workdir = TMPDIR / 'files'
workdir.mkdir(parents=True, exist_ok=True)
os.chmod(workdir, 0o1777)

(workdir / 'src').write_text("Source\n")
(workdir / 'dst').write_text("")


def _chown_5001(path: Path) -> bool:
    """Try to chown(2) `path` to uid 5001. Returns True on success."""
    try:
        os.chown(path, 5001, -1)
        return True
    except PermissionError:
        return False


if not _chown_5001(workdir / 'dst'):
    # Not root: fall back to re-running ourselves under unshare with a
    # uid mapping (Linux user-namespace trick). Only attempt once.
    if not os.environ.get('RSYNC_UNSHARED'):
        unshare = shutil.which('unshare')
        if unshare is not None:
            probe = subprocess.run(
                [unshare, '--user', '--map-root-user',
                 '--map-users', '5001:100000:1', 'true'],
                capture_output=True,
            )
            if probe.returncode == 0:
                print("Re-running under unshare with UID mapping...")
                env = os.environ.copy()
                env['RSYNC_UNSHARED'] = '1'
                os.execvpe(
                    unshare,
                    [unshare, '--user', '--map-root-user',
                     '--map-users', '5001:100000:1',
                     sys.executable, __file__],
                    env,
                )
    test_skipped("Can't chown (need root or unshare with uidmap)")

print(f"Contents of {workdir}:")
subprocess.run(['ls', '-al', str(workdir)])

run_rsync('--inplace', str(workdir / 'src'), str(workdir / 'dst'))
