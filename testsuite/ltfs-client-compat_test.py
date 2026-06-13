#!/usr/bin/env python3
# Test that a client built without SUPPORT_LTFS can still perform normal
# transfers to and from an LTFS-capable server.
#
# The LTFS code path on the server is only activated when --ltfs is passed.
# A no-LTFS client connecting without that flag must be fully compatible:
# the server must not allocate LTFS file-list extras, must not attempt to
# send startblock data, and must complete the transfer correctly.
#
# rsync_noltfs is an otherwise-identical rsync binary compiled without
# SUPPORT_XATTRS (which also suppresses SUPPORT_LTFS).  It acts as the
# client here; the daemon runs the normal LTFS-capable build.

import shutil
import subprocess

from rsyncfns import (
    FROMDIR, TODIR, TOOLDIR,
    build_rsyncd_conf, checkit, makepath, rsync_argv,
    start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12897

noltfs_bin = shutil.which('rsync_noltfs', path=str(TOOLDIR))
if noltfs_bin is None:
    test_skipped(f"rsync_noltfs binary not found in TOOLDIR ({TOOLDIR})")

# --- setup -------------------------------------------------------------------

makepath(FROMDIR, TODIR)
files = {
    'alpha.txt': 'content alpha\n',
    'bravo.txt': 'content bravo\n',
    'sub/gamma.txt': 'content gamma\n',
}
for rel, content in files.items():
    f = FROMDIR / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)

# Daemon runs the LTFS-capable binary (default).
conf = build_rsyncd_conf()
url = start_test_daemon(conf, DAEMON_PORT)

# --- 1. push: no-LTFS client → LTFS-capable server --------------------------

res = subprocess.run(
    [noltfs_bin, '-r', f'{FROMDIR}/', f'{url}test-to/'],
    capture_output=True, text=True,
)
if res.returncode != 0:
    test_fail(
        f"no-LTFS client push to LTFS-capable server failed "
        f"(exit {res.returncode}):\n{res.stderr}"
    )

checkit(['-r', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

# --- 2. pull: no-LTFS client ← LTFS-capable server --------------------------

import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as tmp:
    dst = Path(tmp) / 'pull-dst'
    dst.mkdir()
    res = subprocess.run(
        [noltfs_bin, '-r', f'{url}test-to/', f'{dst}/'],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        test_fail(
            f"no-LTFS client pull from LTFS-capable server failed "
            f"(exit {res.returncode}):\n{res.stderr}"
        )
    checkit(['-r', f'{TODIR}/', f'{dst}/'], TODIR, dst)

print("ltfs-client-compat: no-LTFS client completed push and pull "
      "against LTFS-capable server without errors")
