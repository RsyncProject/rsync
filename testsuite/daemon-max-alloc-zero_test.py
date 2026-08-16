#!/usr/bin/env python3
"""Daemon-mode: the server must reject a wire-supplied --max-alloc=0.

max-alloc-zero-rejected_test.py only proves the *local* client refuses
--max-alloc=0. That alone doesn't protect a daemon: a modified or older client
still forwards --max-alloc=0 on the wire, and an unpatched daemon honours it and
disables its my_alloc() allocation cap (the defence behind CVE-2024-12084 and
friends). This test drives an older rsync client -- which lacks the reject-zero
check and so forwards the option -- against the current rsync daemon, and
asserts the *daemon* refuses it.

It uses the in-tree old_versions/rsync_3.2.7 as the client (3.2.7 predates the
reject-zero fix, so it forwards --max-alloc=0 on the wire). If that binary is
missing or can't run here (e.g. a non-Linux host that can't run the static
archive) the test skips.
"""

import subprocess
from pathlib import Path

from rsyncfns import (
    FROMDIR, RSYNC, SCRATCHDIR,
    makepath, rmtree, start_test_daemon, test_fail, test_skipped,
    write_daemon_conf,
)

DAEMON_PORT = 12932
REJECT_MSG = 'max-alloc must be greater than zero'

OLD_CLIENT = Path(__file__).resolve().parents[1] / 'old_versions' / 'rsync_3.2.7'

if not OLD_CLIENT.exists():
    test_skipped(f"{OLD_CLIENT} not present")

# Confirm the static binary actually runs as rsync on this OS/arch before we
# depend on it: exec of a foreign-arch/OS binary raises OSError, while one that
# loads but can't run won't print the rsync banner. (3.2.7 predates the
# reject-zero fix, so once it runs it forwards --max-alloc=0 on the wire.)
try:
    probe = subprocess.run([str(OLD_CLIENT), '--version'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
except OSError as e:
    test_skipped(f"cannot run {OLD_CLIENT.name} on this OS/arch: {e}")
if probe.returncode != 0 or 'version 3.2.7' not in probe.stdout:
    test_skipped(f"{OLD_CLIENT.name} does not run as rsync on this OS/arch")

# Module served by the *current* (patched) daemon.
src = FROMDIR
rmtree(src)
makepath(src)
(src / 'file.txt').write_text('hello\n')

conf = write_daemon_conf([('mod', {'path': str(src), 'read only': 'yes'})])
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)

dest = SCRATCHDIR / 'out.txt'


def run_client(*extra):
    argv = [str(OLD_CLIENT), *extra, f'{url}mod/file.txt', str(dest)]
    return subprocess.run(argv, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)


# Positive control: the old client and current daemon transfer fine without the
# option, so the failure below is specifically the daemon refusing the option.
dest.unlink(missing_ok=True)
ctrl = run_client()
if ctrl.returncode != 0:
    test_fail(f"old client could not talk to the current daemon:\n{ctrl.stderr}")

# The attack: a forwarded --max-alloc=0 must be refused by the daemon.
dest.unlink(missing_ok=True)
proc = run_client('--max-alloc=0')
if proc.returncode == 0:
    test_fail("daemon accepted a wire-supplied --max-alloc=0")
if REJECT_MSG not in proc.stderr:
    test_fail("daemon did not reject --max-alloc=0 with the expected message; "
              f"stderr:\n{proc.stderr}")

print("daemon-max-alloc-zero: daemon refuses a wire-supplied --max-alloc=0 "
      f"(client {OLD_CLIENT.name})")
