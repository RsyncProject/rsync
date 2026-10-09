#!/usr/bin/env python3
"""Daemon upload delete stats report deleted files."""

import subprocess

from rsyncfns import (
    FROMDIR, TODIR,
    build_rsyncd_conf, forced_protocol, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail,
)


DAEMON_PORT = 12899

src = FROMDIR
dst = TODIR

rmtree(src)
rmtree(dst)
makepath(src, dst)

(src / 'keep.txt').write_text("keep\n")
(dst / 'keep.txt').write_text("keep\n")
(dst / 'delete.txt').write_text("delete\n")

url = start_test_daemon(build_rsyncd_conf(), DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-a', '--delete', '-i', '--stats', f'{src}/', f'{url}test-to/'),
    capture_output=True,
    text=True,
)
out = proc.stdout + proc.stderr
print(out)

if proc.returncode != 0:
    test_fail(f"daemon upload delete run exited {proc.returncode}")

if '*deleting   delete.txt' not in out:
    test_fail(f"daemon upload did not itemize the deleted file:\n{out}")

# The delete-stats summary line is only sent to the client at protocol >= 31
# (the NDX_DEL_STATS message); an older client can't receive the count, so
# only assert it when the protocol isn't pinned below 31.
pv = forced_protocol()
if pv is None or pv >= 31:
    if 'Number of deleted files: 1 (reg: 1)' not in out:
        test_fail(f"daemon upload did not report the deleted file in stats:\n{out}")
