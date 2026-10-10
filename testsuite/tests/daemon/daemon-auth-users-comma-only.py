#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

PORT = 12949
USER = 'authuser'
PASSWORD = 'known-password'
DATA = 'AUTH-USERS-PARSE\n'

base = SCRATCHDIR / 'auth-users-comma-only'
rmtree(base)
src = base / 'src'
mod = base / 'mod'
ctrl = base / 'ctrl'
grp = base / 'grp'
grpc = base / 'grpc'
makepath(src, mod, ctrl, grp, grpc)
(src / 'f1').write_text(DATA)

REALUSER = pwd.getpwuid(os.geteuid()).pw_name

secrets = base / 'auth-users.secrets'
secrets.write_text(f'{USER}:{PASSWORD}\n{REALUSER}:{PASSWORD}\n')
secrets.chmod(0o600)
pwfile = base / 'auth-users.password'
pwfile.write_text(PASSWORD + '\n')
pwfile.chmod(0o600)

common = {
    'read only': 'no',
    'use chroot': 'no',
    'secrets file': str(secrets),
}

conf = write_daemon_conf([
    ('spaced', dict(common, path=str(mod),
                    **{'auth users': f',@nosuchgroup {USER}:deny, {USER}:rw'})),
    ('plain', dict(common, path=str(ctrl),
                   **{'auth users': f'{USER}:rw'})),
    ('grpctl', dict(common, path=str(grpc),
                    **{'auth users': f'@*:rw'})),
    ('grpdeny', dict(common, path=str(grp),
                     **{'auth users': f',@[! []*:deny, {REALUSER}:rw'})),
], name='auth-users-comma-only.conf')
url = start_test_daemon(conf, PORT)

os.environ['RSYNC_PASSWORD'] = 'wrong-environment-fallback'

def push(module, dest_dir, as_user=USER):
    proc = subprocess.run(
        rsync_argv('-r', f'--password-file={pwfile}', f'{src}/',
                   url.replace('rsync://', f'rsync://{as_user}@', 1) + module + '/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    landed = dest_dir / 'f1'
    return proc, landed

proc, landed = push('plain', ctrl)
if proc.returncode != 0 or not landed.is_file() or landed.read_text() != DATA:
    test_fail('positive control failed: the credential could not push to a '
              f'module with a plain "auth users" (rc={proc.returncode}, '
              f'output={proc.stdout.strip()[:300]!r})')

proc, landed = push('spaced', mod)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
if not landed.is_file():
    test_fail(f'comma-separated auth rule was split on whitespace ({ctx})')
if landed.read_text() != DATA:
    test_fail(f'transfer was allowed but delivered the wrong content ({ctx})')
if proc.returncode != 0:
    test_fail(f'transfer was allowed but failed ({ctx})')

proc, landed = push('grpctl', grpc, as_user=REALUSER)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
if proc.returncode != 0 or not landed.is_file():
    test_fail(f'@*:rw rejected {REALUSER} ({ctx})')

proc, landed = push('grpdeny', grp, as_user=REALUSER)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
if landed.is_file() or proc.returncode == 0:
    test_fail(f'spaced group deny did not reject {REALUSER} ({ctx})')
logfile = SCRATCHDIR / 'rsyncd.log'
log = logfile.read_text(errors='replace') if logfile.is_file() else ''
if not [ln for ln in log.splitlines()
        if 'auth failed on module grpdeny' in ln
        and f'for {REALUSER}: denied by rule' in ln]:
    test_fail(f'grpdeny did not log a deny-rule match ({ctx}, log={log.strip()[-500:]!r})')

print('a leading comma in "auth users" splits on commas alone')
