#!/usr/bin/env python3
"""Pin the EXACT secrets-file permission contract enforced by `strict modes`.

The rsyncd.conf(5) `strict modes` entry says the secrets file "must not be
readable by any user ID other than the one that the rsync daemon is running
under".  That is not what the code does.  check_secret() (authenticate.c) only
rejects the file when:

    (st.st_mode & 06) != 0          -> other-READ or other-WRITE bit set
  OR (daemon running as root AND the file is not owned by root)

So group permissions are never consulted, and even an other-EXECUTE-only bit is
ignored.  A group-readable secrets file is ACCEPTED, directly contradicting the
"any user ID other than" wording.  This test measures accept/reject across a
mode sweep so the behaviour is unambiguous and the doc can be made to match it.

Auth runs inside the daemon protocol, so the default stdio-pipe transport is
fine (no TCP needed).  The owner-mismatch row needs the daemon to run as root
plus an unprivileged uid to chown to, so it is gated on both.

Cross-version: the `& 06` test is long-standing, so running this with
`--rsync-bin=old_versions/rsync_3.2.7` is expected to reproduce the same grid --
i.e. the doc has been imprecise for many releases, this is not a regression.
Set REPORT_MODES=1 to dump the observed grid (via test_fail) for eyeballing.
"""

import os
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    find_attacker_uid, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12910

# Never let a rejected secrets file fall through to an interactive getpass()
# on /dev/tty (which would hang an automated run): give a wrong env password so
# the client always has something to send and never prompts.
os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

authdir = SCRATCHDIR / 'strictdest'
secrets = SCRATCHDIR / 'rsyncd.strict.secrets'
secrets.write_text('tuser:secretpass\n')
secrets.chmod(0o600)

# Two modules: strict modes on (default) and off, same secrets file.
conf = write_daemon_conf([
    ('strict', {'path': str(authdir), 'read only': 'no',
                'auth users': 'tuser', 'secrets file': str(secrets),
                'strict modes': 'yes'}),
    ('loose',  {'path': str(authdir), 'read only': 'no',
                'auth users': 'tuser', 'secrets file': str(secrets),
                'strict modes': 'no'}),
], name='strict-modes.conf')
url = start_test_daemon(conf, DAEMON_PORT)


def pwfile():
    p = SCRATCHDIR / 'pw.strict'
    p.write_text('secretpass\n')
    p.chmod(0o600)
    return p


PW = pwfile()


def accepted(module):
    """Push with the correct password; True == the daemon honoured the secrets
    file (auth succeeded), False == it ignored/rejected it (auth failed)."""
    rmtree(authdir)
    makepath(authdir)
    userurl = url.replace('rsync://', 'rsync://tuser@', 1)
    proc = subprocess.run(
        rsync_argv('-a', f'--password-file={PW}', f'{src}/', f'{userurl}{module}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    return proc.returncode in (0, 23)


# (mode, expected-accepted-under-strict-modes, human note)
MODE_GRID = [
    (0o600, True,  'owner rw only'),
    (0o400, True,  'owner r only'),
    (0o640, True,  'group READ -> still accepted (group bits unchecked)'),
    (0o660, True,  'group rw -> accepted'),
    (0o610, True,  'group exec -> accepted'),
    (0o601, True,  'other EXEC only -> accepted (& 06 ignores the x bit)'),
    (0o604, False, 'other READ -> rejected'),
    (0o602, False, 'other WRITE -> rejected'),
    (0o606, False, 'other rw -> rejected'),
    (0o644, False, 'world-readable -> rejected'),
    (0o666, False, 'world rw -> rejected'),
]

grid, mism = [], []
for mode, want, note in MODE_GRID:
    secrets.chmod(mode)
    got = accepted('strict')
    grid.append(f"strict 0{mode:04o} accepted={int(got)} want={int(want)}  {note}")
    if got != want:
        mism.append(f"strict modes, 0{mode:04o}: accepted={got}, expected={want} ({note})")

# strict modes = no: even a world-rw secrets file is honoured.
secrets.chmod(0o666)
got = accepted('loose')
grid.append(f"loose  00666 accepted={int(got)} want=1  strict modes=no skips the check")
if not got:
    mism.append("strict modes=no still rejected a 0666 secrets file")

# Owner mismatch is only enforced when the daemon runs as root.
ATT = find_attacker_uid() if os.geteuid() == 0 else None
if ATT is not None:
    secrets.chmod(0o600)
    os.chown(secrets, ATT, ATT)        # 0600 but NOT owned by root
    got = accepted('strict')
    grid.append(f"strict 00600 owner={ATT} accepted={int(got)} want=0  "
                f"non-root owner rejected when daemon is root")
    if got:
        mism.append("strict modes accepted a non-root-owned secrets file under a root daemon")
    os.chown(secrets, 0, 0)
else:
    grid.append("strict owner-mismatch row SKIPPED (needs root daemon + unprivileged uid)")

secrets.chmod(0o600)

if os.environ.get('REPORT_MODES'):
    test_fail("REPORT-MODES grid:\n  " + "\n  ".join(grid))
if mism:
    test_fail("strict-modes contract deviated:\n  " + "\n  ".join(mism))

print("daemon-strict-modes-matrix: secrets-file accept/reject pinned -- only "
      "other-read/other-write (st_mode & 06) and (under a root daemon) a "
      "non-root owner are rejected; group bits and other-exec are NOT checked, "
      "so a group-readable secrets file is accepted")
