#!/usr/bin/env python3
"""Daemon coverage: auth users, secrets file, strict modes, and PAM.

A module with auth users + a secrets file must accept the right password,
reject a wrong one, and (with the default strict modes) refuse a
world-readable secrets file. Authentication happens in the daemon protocol, so
it works over the default secure stdio-pipe transport.
"""

import os
import subprocess
import getpass
import sys

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
real_user = getpass.getuser()

# Add both the fake user and the real user to the secrets file
secrets.write_text(f'tuser:secretpass\n{real_user}:realpass\n')
secrets.chmod(0o600)

conf = write_daemon_conf([
    ('auth', {'path': authdir, 'read only': 'no',
              'auth users': 'tuser', 'secrets file': secrets}),
])
url = start_test_daemon(conf, DAEMON_PORT)
host_port_path = url.replace('rsync://', '')


def pwfile(name, text):
    p = SCRATCHDIR / name
    p.write_text(text)
    p.chmod(0o600)
    return p


def push(pw, target_module='auth', user='tuser', **kw):
    rmtree(authdir)
    makepath(authdir)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pw}', f'{src}/', f'rsync://{user}@{host_port_path}{target_module}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, **kw)


# --- correct password succeeds ----------------------------------------------
ok = pwfile('pw.ok', 'secretpass\n')
real_ok = pwfile('pw.real_ok', 'realpass\n')
bad = pwfile('pw.bad', 'wrongpass\n')

proc = push(ok)
if proc.returncode not in (0, 23):
    test_fail(f"auth with the correct password failed: {proc.stderr}")
verify_dirs(src, authdir, label="auth success")

# --- wrong password is rejected ---------------------------------------------
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

# ============================================================================
# --- PAM Implementation Tests -----------------------------------------------
# ============================================================================

is_root = (os.geteuid() == 0)
if not is_root:
    print("PAM test is skipped. Test not running as root")
    sys.exit(0)

daemon_log = SCRATCHDIR / 'rsyncd.log'
if daemon_log.exists():
    daemon_log.unlink()
conf = SCRATCHDIR / 'rsyncd.conf'

# FIX 1: uid and gid added back to prevent 'Permission Denied'
conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    f"uid = {real_user}\n"
    f"gid = {real_user}\n"
    f"log file = {daemon_log}\n"
    f"\n[pam_auth]\n"
    f"\tpath = {authdir}\n"
    "\tread only = no\n"
    f"\tauth users = tuser, {real_user}\n"
    f"\tsecrets file = {secrets}\n"
    "\tuse pam = yes\n"
)

url = start_test_daemon(conf, DAEMON_PORT)
host_port_path = url.replace('rsync://', '')

# FIX 2: Must use 'ok' password here so MD5 succeeds and triggers the PAM code
# 1. Fake User (Correct Password) - Acts as our PAM environment probe
proc = push(ok, target_module='pam_auth', user='tuser')
log_content = daemon_log.read_text() if daemon_log.exists() else ""

# Check exactly why the daemon rejected the connection
if "PAM enabled but rsync compiled without PAM support" in log_content:
    print("daemon-auth: PAM not compiled in. Skipping remaining PAM tests.")
    sys.exit(0)

if "PAM enabled but daemon not running as root" in log_content:
    print("daemon-auth: Not running as root. Skipping remaining PAM tests.")
    sys.exit(0)

# If we get here, PAM is compiled and running as root.
# We MUST enforce the expected PAM account management failures.
if proc.returncode == 0:
    test_fail("PAM module unexpectedly authenticated non-existent system user 'tuser'!")
if proc.returncode != 5:
    test_fail(f"Fake user failed with unexpected exit code (expected 5, got {proc.returncode}): {proc.stderr}")

# 2. Fake User (Wrong Password)
# Fails at the initial MD5 hash check, never reaches PAM account management.
proc = push(bad, target_module='pam_auth', user='tuser')
if proc.returncode == 0:
    test_fail("PAM module unexpectedly succeeded with the wrong password (fake user)")

# 3. Real System User (Correct Password)
# Passes MD5 check and pam_acct_mgmt() confirms the account is valid.
proc = push(real_ok, target_module='pam_auth', user=real_user)
if proc.returncode not in (0, 23):
    test_fail(f"PAM module rejected valid system user '{real_user}': {proc.stderr} (rc={proc.returncode})")

# If pam is not compiled, all this will pass normally so we need to check the log file
# to make sure that the user account is validated through PAM
log_content = daemon_log.read_text() if daemon_log.exists() else ""
if "PAM: Account validation successful for user" not in log_content:
    test_fail("The test is running on an older rsync release which is not supporting PAM for account validation.")

verify_dirs(src, authdir, label="PAM real user auth success")
print("daemon-auth: auth users / secrets file / strict modes / PAM verified")
