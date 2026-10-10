#!/usr/bin/env python3

import os
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    verify_dirs, write_text_file,
)
from harness import metadata

metadata(features={'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'authentication', 'daemon', 'version-mix'})

DAEMON_PORT = 12888

os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

src = FROMDIR
rmtree(src)
make_tree(src, depth=3)

authdir = SCRATCHDIR / 'authdest'
secrets = SCRATCHDIR / 'rsyncd.secrets'
secrets.write_text('tuser:secretpass\n')
secrets.chmod(0o600)

conf = write_daemon_conf([
    ('auth', {'path': authdir, 'read only': 'no',
              'auth users': 'tuser', 'secrets file': secrets}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', 'rsync://tuser@', 1)

def push(pw, **kw):
    rmtree(authdir)
    makepath(authdir)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pw}', f'{src}/', f'{userurl}auth/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, **kw)

ok = write_text_file(SCRATCHDIR / 'pw.ok', 'secretpass\n', 0o600)
proc = push(ok)
if proc.returncode not in (0, 23):
    test_fail(f"auth with the correct password failed: {proc.stderr}")
verify_dirs(src, authdir, label="auth success")

bad = write_text_file(SCRATCHDIR / 'pw.bad', 'wrongpass\n', 0o600)
proc = push(bad)
if proc.returncode == 0:
    test_fail("auth with the wrong password unexpectedly succeeded")

proc = subprocess.run(
    rsync_argv('-a', f'{src}/', f'{url}auth/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    stdin=subprocess.DEVNULL)
if proc.returncode == 0:
    test_fail("a request with invalid credentials succeeded against an "
              "auth-users module")

secrets.chmod(0o644)
proc = push(ok)
if proc.returncode == 0:
    test_fail("strict modes did not reject a world-readable secrets file")
secrets.chmod(0o600)

print("daemon-auth: auth users / secrets file / strict modes verified")
