#!/usr/bin/env python3

import os
import shutil
import subprocess
import sys
from pathlib import Path

from harness.rsync import TMPDIR, run_rsync, test_fail, test_skipped

pr_path = Path('/proc/sys/fs/protected_regular')
if not pr_path.is_file():
    test_skipped("Can't find protected_regular setting (only available on Linux)",
                 capability='protected_regular')

try:
    pr_lvl = pr_path.read_text().strip()
except OSError:
    test_skipped("Can't check if fs.protected_regular is enabled", capability='protected_regular')
if pr_lvl == '0':
    test_skipped("fs.protected_regular is not enabled", capability='protected_regular')

workdir = TMPDIR / 'files'
workdir.mkdir(parents=True, exist_ok=True)
os.chmod(workdir, 0o1777)

(workdir / 'src').write_text("Source\n")
(workdir / 'dst').write_text("")

def _chown_5001(path: Path) -> bool:
    try:
        os.chown(path, 5001, -1)
        return True
    except PermissionError:
        return False

if not _chown_5001(workdir / 'dst'):
    if not os.environ.get('RSYNC_UNSHARED'):
        unshare = shutil.which('unshare')
        if unshare is not None:
            try:
                probe = subprocess.run(
                    [unshare, '--user', '--map-root-user',
                     '--map-users', '5001:100000:1', 'true'],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                test_skipped("Can't chown (unshare probe timed out)", capability='protected_regular')
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
    test_skipped("Can't chown (need root or unshare with uidmap)", capability='protected_regular')

print(f"Contents of {workdir}:")
subprocess.run(['ls', '-al', str(workdir)])

run_rsync('--inplace', str(workdir / 'src'), str(workdir / 'dst'))

dst_content = (workdir / 'dst').read_text()
if dst_content != "Source\n":
    test_fail(f"--inplace did not write the source content into the protected "
              f"dst: got {dst_content!r}")
