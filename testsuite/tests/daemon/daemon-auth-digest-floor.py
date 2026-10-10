#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    FROMDIR, RSYNC, SCRATCHDIR, SRCDIR,
    make_tree, makepath, rmtree, run_rsync, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf, split_rsync_cmd,
)

DAEMON_PORT = 12953

vv = run_rsync('-VV', check=True, capture_output=True).stdout
if '"sha256"' not in vv or '"sha512"' not in vv:
    test_skipped("rsync built without sha256/sha512 daemon-auth digests "
                 "(no openssl); cannot require a strong auth digest")

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
        argv = [rsync_cmd] + list(argv[len(split_rsync_cmd(RSYNC)):])
    return subprocess.run(argv, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)

proc = push('floor', floordir)
if proc.returncode not in (0, 23):
    test_fail("a default client (negotiates sha512) was refused by the "
              f"'auth digest = sha256' floor: {proc.stderr}")

proc = push('badfloor', badfloordir)
if proc.returncode == 0 or 'auth failed' not in proc.stderr:
    test_fail("a module whose 'auth digest' names an unsupported digest must "
              "refuse auth (fail-closed) with an auth-failure error, not just a "
              f"transfer failure: rc={proc.returncode} stderr={proc.stderr!r}")

old_rsync = SRCDIR / 'old_versions' / 'rsync_3.1.3'
if not old_rsync.is_file() or not os.access(old_rsync, os.X_OK):
    print("daemon auth-digest floor: strong client accepted, bad-floor refused; "
          "skipped the md5-downgrade case (no old_versions/rsync_3.1.3 here)")
    raise SystemExit(0)

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

proc = push('nofloor', nofloordir, rsync_cmd=str(old_rsync))
if proc.returncode not in (0, 23):
    test_fail("control failed: a pre-3.2.0 (md5-auth) client could not "
              f"authenticate even without an auth-digest floor: {proc.stderr}")

proc = push('floor', floordir, rsync_cmd=str(old_rsync))
if proc.returncode == 0 or 'auth failed' not in proc.stderr:
    test_fail("the 'auth digest = sha256' floor did NOT refuse (with an "
              "auth-failure error) a pre-3.2.0 client whose auth digest fell "
              f"back to md5: rc={proc.returncode} stderr={proc.stderr!r}")

print("daemon auth-digest floor: strong client accepted, bad-floor refused, "
      "md5-downgraded old client refused")
