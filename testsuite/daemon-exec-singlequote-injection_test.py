#!/usr/bin/env python3
"""PoC: shell-escaped %RSYNC_USER_NAME% is unsafe inside single quotes.

expand_vars_shell_escape() produces a shell word of the form 'value'.  That is
safe when substituted into an unquoted command, but a common hook such as

    pre-xfer exec = printf '%RSYNC_USER_NAME%' >/dev/null

already supplies single quotes.  The generated quotes cancel those quotes and
leave an authenticated client's username in shell syntax context.
"""

import os
import subprocess
from pathlib import Path

from rsyncfns import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

PORT = 12939

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)
dest = SCRATCHDIR / 'exec-singlequote-dest'
rmtree(dest)
makepath(dest)

# A slash would be interpreted as the URL's module separator before the client
# sends the username, so create the marker in the daemon's inherited cwd.
sentinel = Path.cwd() / 'exec-singlequote-pwned'
if sentinel.exists():
    sentinel.unlink()

# No spaces or ':' are needed in the wire username.  ${IFS} becomes a shell
# separator only after the broken quote composition exposes the value.
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
# The username carries shell syntax, so the daemon refuses it rather than
# trying to quote it; the transfer must fail closed.
if proc.returncode == 0:
    test_fail(
        "daemon accepted an authenticated username holding shell syntax "
        f"(rc={proc.returncode}):\n{proc.stderr}"
    )

print("single-quoted daemon hook refused a username holding shell syntax")
