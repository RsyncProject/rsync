#!/usr/bin/env python3
"""Daemon 'auth digest' floor: refuse an auth digest weaker than a configured minimum.

The daemon picks the auth challenge-response digest from the client's
negotiation, or -- for a peer that sends no digest list (any rsync older than
3.2.0, e.g. the macOS openrsync) -- falls back to md5 (proto >= 30) or md4.  So
an on-path downgrade, or simply an old client, can pin the auth exchange to
md5/md4, making a captured response far cheaper to brute-force offline.
'auth digest = NAME' lets the operator require at least NAME (strongest-first
order: sha512 sha256 sha1 md5 md4); a connection whose negotiated digest is
weaker -- or a misconfigured floor naming an unknown digest -- is refused before
the challenge.  Default off (no floor).

Coverage:
  * a default (modern) client negotiates sha512 and clears an 'auth digest =
    sha256' floor;
  * a floor naming a digest this build doesn't have is fail-closed (rejects even
    a strong client) -- portable, proves the floor actually enforces;
  * a genuine pre-3.2.0 client (no digest list -> md5 fallback) is accepted by a
    no-floor module but refused by the sha256 floor (the real downgrade case) --
    gated on a bundled old static rsync, which is export-ignored from the fleet
    git-archive, so it's only exercised where that binary is present.
"""

import os
import shlex
import subprocess

from rsyncfns import (
    FROMDIR, RSYNC, SCRATCHDIR, SRCDIR,
    make_tree, makepath, rmtree, run_rsync, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf, split_rsync_cmd,
)

DAEMON_PORT = 12953

# Requiring a strong auth digest needs a build that has one.
vv = run_rsync('-VV', check=True, capture_output=True).stdout
if '"sha256"' not in vv or '"sha512"' not in vv:
    test_skipped("rsync built without sha256/sha512 daemon-auth digests "
                 "(no openssl); cannot require a strong auth digest")

# Never fall back to an interactive getpass() on a missing password (it reads
# /dev/tty and would hang the harness) -- see daemon-auth_test.py.
os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

src = FROMDIR
rmtree(src)
make_tree(src, depth=3)

floordir = SCRATCHDIR / 'floordest'
nofloordir = SCRATCHDIR / 'nofloordest'
badfloordir = SCRATCHDIR / 'badfloordest'
secrets = SCRATCHDIR / 'rsyncd.secrets'
secrets.write_text('tuser:secretpass\n')
secrets.chmod(0o600)

pw = SCRATCHDIR / 'pw.ok'
pw.write_text('secretpass\n')
pw.chmod(0o600)

conf = write_daemon_conf([
    ('floor', {'path': floordir, 'read only': 'no',
               'auth users': 'tuser', 'secrets file': secrets,
               'auth digest': 'sha256'}),
    ('nofloor', {'path': nofloordir, 'read only': 'no',
                 'auth users': 'tuser', 'secrets file': secrets}),
    ('badfloor', {'path': badfloordir, 'read only': 'no',
                  'auth users': 'tuser', 'secrets file': secrets,
                  'auth digest': 'no-such-digest'}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', 'rsync://tuser@', 1)


def push(module, dest, rsync_cmd=None):
    rmtree(dest)
    makepath(dest)
    argv = rsync_argv('-a', f'--password-file={pw}', f'{src}/', f'{userurl}{module}/')
    if rsync_cmd:
        # Replace the whole RSYNC wrapper (which under --valgrind is
        # 'valgrind <opts> .../rsync', not a bare binary) with the old static
        # rsync; keep only the transfer args that rsync_argv appended.  The old
        # fixture binary is run directly, not under valgrind.
        argv = [rsync_cmd] + list(argv[len(split_rsync_cmd(RSYNC)):])
    return subprocess.run(argv, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)


# --- a strong (default) client clears the floor -----------------------------
proc = push('floor', floordir)
if proc.returncode not in (0, 23):
    test_fail("a default client (negotiates sha512) was refused by the "
              f"'auth digest = sha256' floor: {proc.stderr}")

# --- a floor naming an unknown digest is fail-closed (portable reject) -------
proc = push('badfloor', badfloordir)
if proc.returncode == 0 or 'auth failed' not in proc.stderr:
    test_fail("a module whose 'auth digest' names an unsupported digest must "
              "refuse auth (fail-closed) with an auth-failure error, not just a "
              f"transfer failure: rc={proc.returncode} stderr={proc.stderr!r}")

# --- real downgrade: a pre-3.2.0 client (md5 fallback) vs the floor ----------
old_rsync = SRCDIR / 'old_versions' / 'rsync_3.1.3'
if not old_rsync.is_file() or not os.access(old_rsync, os.X_OK):
    print("daemon auth-digest floor: strong client accepted, bad-floor refused; "
          "skipped the md5-downgrade case (no old_versions/rsync_3.1.3 here)")
    raise SystemExit(0)

# The static rsync_3.1.3 is a Linux binary; confirm it actually runs on this
# OS/arch before depending on it (a full checkout ships it even on BSD/macOS,
# where exec raises OSError or it won't print the rsync banner) -- skip the
# md5-downgrade case there rather than failing the whole test.
try:
    probe = subprocess.run([str(old_rsync), '--version'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    runs = probe.returncode == 0 and 'version 3.1.3' in probe.stdout
except OSError:
    runs = False
if not runs:
    print("daemon auth-digest floor: strong client accepted, bad-floor refused; "
          "skipped the md5-downgrade case (rsync_3.1.3 does not run on this OS/arch)")
    raise SystemExit(0)

# Control: the old (md5-auth) client authenticates fine against a no-floor module.
proc = push('nofloor', nofloordir, rsync_cmd=str(old_rsync))
if proc.returncode not in (0, 23):
    test_fail("control failed: a pre-3.2.0 (md5-auth) client could not "
              f"authenticate even without an auth-digest floor: {proc.stderr}")

# The floor refuses that same md5 client.
proc = push('floor', floordir, rsync_cmd=str(old_rsync))
if proc.returncode == 0 or 'auth failed' not in proc.stderr:
    test_fail("the 'auth digest = sha256' floor did NOT refuse (with an "
              "auth-failure error) a pre-3.2.0 client whose auth digest fell "
              f"back to md5: rc={proc.returncode} stderr={proc.stderr!r}")

print("daemon auth-digest floor: strong client accepted, bad-floor refused, "
      "md5-downgraded old client refused")
