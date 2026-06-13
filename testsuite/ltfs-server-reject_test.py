#!/usr/bin/env python3
# Test that a server built without SUPPORT_LTFS rejects --ltfs with a clear
# error instead of silently ignoring it or corrupting the transfer.
#
# rsync_noltfs is an otherwise-identical rsync binary with only options.c
# recompiled without SUPPORT_XATTRS, which suppresses SUPPORT_LTFS and
# activates the server-side abort guard in options.c.  The connection uses a
# local pipe (RSYNC_CONNECT_PROG) so no TCP socket or real LTFS volume is
# needed, and the test runs on all platforms regardless of xattr support.

import shutil
import subprocess

from rsyncfns import (
    FROMDIR, TODIR, TOOLDIR,
    build_rsyncd_conf, makepath, rsync_argv,
    start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12896

noltfs_bin = shutil.which('rsync_noltfs', path=str(TOOLDIR))
if noltfs_bin is None:
    test_skipped(f"rsync_noltfs binary not found in TOOLDIR ({TOOLDIR})")

makepath(FROMDIR, TODIR)
(FROMDIR / 'probe.txt').write_text('hello from the ltfs-server-reject test\n')

conf = build_rsyncd_conf()
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=noltfs_bin)

res = subprocess.run(
    rsync_argv('-r', '--ltfs', f'{FROMDIR}/', f'{url}test-to/'),
    capture_output=True, text=True,
)

if res.returncode == 0:
    test_fail(
        "--ltfs succeeded against a no-LTFS server; expected a non-zero exit"
    )

combined = res.stderr + res.stdout
if '--ltfs is not supported on this server' not in combined:
    test_fail(
        f"--ltfs against a no-LTFS server exited {res.returncode} but did "
        f"not produce the expected error message.\n"
        f"stdout: {res.stdout!r}\nstderr: {res.stderr!r}"
    )

print("ltfs-server-reject: no-LTFS server correctly refused --ltfs "
      "with a clear error message")
