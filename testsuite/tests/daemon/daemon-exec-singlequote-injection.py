#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
)

PORT = 12939

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)
dest = SCRATCHDIR / 'exec-singlequote-dest'
rmtree(dest)
makepath(dest)

sentinel = Path.cwd() / 'exec-singlequote-pwned'
if sentinel.exists():
    sentinel.unlink()

user = f';touch${{IFS}}{sentinel.name};true'
password = 'known-password'

secrets = SCRATCHDIR / 'exec-singlequote.secrets'
secrets.write_text(f'{user}:{password}\n')
secrets.chmod(0o600)
pwfile = SCRATCHDIR / 'exec-singlequote.password'
pwfile.write_text(password + '\n')
pwfile.chmod(0o600)

conf = write_daemon_conf([
    ('hook', {
        'path': str(dest),
        'read only': 'no',
        'use chroot': 'no',
        'auth users': '*',
        'secrets file': str(secrets),
        'pre-xfer exec': "printf '%RSYNC_USER_NAME%' >/dev/null",
    }),
], name='exec-singlequote.conf')
url = start_test_daemon(conf, PORT)

os.environ['RSYNC_PASSWORD'] = 'wrong-environment-fallback'
user_url = url.replace('rsync://', f'rsync://{user}@', 1) + 'hook/'
proc = subprocess.run(
    rsync_argv('-r', f'--password-file={pwfile}', f'{src}/', user_url),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

if sentinel.exists():
    test_fail(
        "authenticated metacharacter username executed through a "
        f"single-quoted %RSYNC_USER_NAME% hook (rc={proc.returncode}):\n"
        f"{proc.stderr}"
    )
if proc.returncode == 0:
    test_fail(
        "daemon accepted an authenticated username holding shell syntax "
        f"(rc={proc.returncode}):\n{proc.stderr}"
    )

print("single-quoted daemon hook refused a username holding shell syntax")
