#!/usr/bin/env python3
"""Daemon coverage: auth users, secrets file, strict modes.

A module with auth users + a secrets file must accept the right password,
reject a wrong one, and (with the default strict modes) refuse a
world-readable secrets file. Authentication happens in the daemon protocol, so
it works over the default secure stdio-pipe transport.
"""

import os
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    verify_dirs, write_daemon_conf,
)

DAEMON_PORT = 12888

# When a daemon module needs auth and no password is available, rsync falls back
# to an interactive getpass() prompt that reads /dev/tty directly -- which the
# test harness cannot redirect, so it would hang `make coverage` (or any run
# with a controlling terminal). Give every client a fallback password via the
# environment so it never prompts: the --password-file cases below override it,
# and the invalid-credentials case uses it and is correctly rejected.
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


def pwfile(name, text):
    p = SCRATCHDIR / name
    p.write_text(text)
    p.chmod(0o600)
    return p


def push(pw, **kw):
    rmtree(authdir)
    makepath(authdir)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pw}', f'{src}/', f'{userurl}auth/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, **kw)


# --- correct password succeeds ----------------------------------------------
ok = pwfile('pw.ok', 'secretpass\n')
proc = push(ok)
if proc.returncode not in (0, 23):
    test_fail(f"auth with the correct password failed: {proc.stderr}")
verify_dirs(src, authdir, label="auth success")

# --- wrong password is rejected ---------------------------------------------
bad = pwfile('pw.bad', 'wrongpass\n')
proc = push(bad)
if proc.returncode == 0:
    test_fail("auth with the wrong password unexpectedly succeeded")

# --- a request with invalid credentials is rejected ------------------------
# Local user (not an auth user) with the wrong env-supplied password; rejected
# without ever prompting on the tty.
proc = subprocess.run(
    rsync_argv('-a', f'{src}/', f'{url}auth/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    stdin=subprocess.DEVNULL)
if proc.returncode == 0:
    test_fail("a request with invalid credentials succeeded against an "
              "auth-users module")

# --- strict modes rejects a world-readable secrets file ---------------------
secrets.chmod(0o644)
proc = push(ok)
if proc.returncode == 0:
    test_fail("strict modes did not reject a world-readable secrets file")
secrets.chmod(0o600)

print("daemon-auth: auth users / secrets file / strict modes verified")
