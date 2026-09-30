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
import sys
import ctypes.util
import platform
import shutil

# Ensure root privileges
if os.getuid() != 0:
    print("daemon-auth: auth users / secrets file / strict modes verified (PAM tests skipped: requires root)")
    sys.exit(0)

# Skip Darwin: macOS SIP strips dynamic library injection across fork/exec
if platform.system() == 'Darwin':
    print("daemon-auth: auth users / secrets file / strict modes verified (PAM tests skipped on Darwin)")
    sys.exit(0)

# Verify mock PAM plugin is compiled
build_dir = os.environ.get('tooldir', os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
mock_so = os.path.join(build_dir, 'pam_mock.so')
if not os.path.exists(mock_so):
    print("daemon-auth: auth users / secrets file / strict modes verified (PAM tests skipped: missing pam_mock.so)")
    sys.exit(0)

# Locate pam_wrapper via pkg-config
pam_wrapper_so = None
pkg_config = shutil.which("pkg-config")
if pkg_config:
    try:
        res = subprocess.run([pkg_config, "--libs", "pam_wrapper"], capture_output=True, text=True, check=True)
        discovered_path = res.stdout.strip()
        if os.path.exists(discovered_path):
            pam_wrapper_so = discovered_path
    except Exception:
        pass

if not pam_wrapper_so:
    print("daemon-auth: auth users / secrets file / strict modes verified (PAM tests skipped: pam_wrapper not found)")
    sys.exit(0)

# Setup isolated PAM configuration
fake_pam_dir = SCRATCHDIR / 'pam.d'
if not fake_pam_dir.exists():
    fake_pam_dir.mkdir()

pam_conf = fake_pam_dir / 'rsync'
pam_conf.write_text(f"account required {mock_so}\n")

# Inject pam_wrapper into daemon environment
os.environ['LD_PRELOAD'] = pam_wrapper_so
os.environ['PAM_WRAPPER'] = '1'
os.environ['PAM_WRAPPER_SERVICE_DIR'] = str(fake_pam_dir)

daemon_log = SCRATCHDIR / 'rsyncd.log'
if daemon_log.exists():
    daemon_log.unlink()
conf = SCRATCHDIR / 'rsyncd.conf'

conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    f"log file = {daemon_log}\n"
    "uid = 0\n"
    "gid = 0\n"
    f"\n[pam_auth]\n"
    f"\tpath = {authdir}\n"
    "\tread only = no\n"
    f"\tauth users = tuser, {real_user}\n"
    f"\tsecrets file = {secrets}\n"
    "\tuse pam = yes\n"
)

url = start_test_daemon(conf, DAEMON_PORT)
host_port_path = url.replace('rsync://', '')

# 1. Fake user with valid secrets password: fails PAM account management (expected returncode 5)
proc = push(ok, target_module='pam_auth', user='tuser')
log_content = daemon_log.read_text() if daemon_log.exists() else ""

if "PAM enabled but rsync compiled without PAM support" in log_content:
    print("daemon-auth: auth users / secrets file / strict modes verified (PAM tests skipped: rsync built without PAM)")
    sys.exit(0)

if "PAM: Account validation successful for user" in log_content:
    test_fail("PAM module unexpectedly authenticated non-existent system user 'tuser'!")

if proc.returncode != 5:
    test_fail(f"Fake user failed with unexpected exit code (expected 5, got {proc.returncode}): {proc.stderr}")

# 2. Fake user with wrong password: fails MD5 challenge prior to PAM evaluation
proc = push(bad, target_module='pam_auth', user='tuser')
if proc.returncode == 0 or "PAM: Account validation successful for user" in log_content:
    test_fail("PAM module unexpectedly succeeded with the wrong password (fake user)")

# 3. Real system user with valid secrets password: passes both MD5 and PAM
proc = push(real_ok, target_module='pam_auth', user=real_user)
if proc.returncode not in (0, 23):
    test_fail(f"PAM module rejected valid system user '{real_user}': {proc.stderr} (rc={proc.returncode})")

log_content = daemon_log.read_text() if daemon_log.exists() else ""
if "PAM: Account validation successful for user" not in log_content:
    test_fail("The test is running on an older rsync release which is not supporting PAM for account validation.")

verify_dirs(src, authdir, label="PAM real user auth success")

# Remove injection wrapper from parent test runner environment immediately after spawn
del os.environ['LD_PRELOAD']
del os.environ['PAM_WRAPPER']
del os.environ['PAM_WRAPPER_SERVICE_DIR']

print("daemon-auth: auth users / secrets file / strict modes / PAM verified")
