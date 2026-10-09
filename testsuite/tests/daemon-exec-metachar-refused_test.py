#!/usr/bin/env python3
"""A peer value carrying shell syntax must not reach a shell-executed hook.

Context-aware quoting is only correct for a single level of shell parsing, and
a hook may re-parse the substituted word in a nested shell:

    pre-xfer exec = sh -c 'printf %s %RSYNC_USER_NAME% >out'

There the level-1 quotes are removed before the inner shell sees the value, so
no amount of escaping keeps it as data.  The daemon therefore refuses the value
outright.  This checks the refusal in each quote context, including the nested
form, and -- so the rule cannot be satisfied by refusing everything -- that an
ordinary value still runs the hook and arrives intact.

One daemon serves a module per case: a second daemon cannot lock the shared pid
file, so starting one per case would only work in the stdio-pipe transport.
"""

import os
import subprocess
from pathlib import Path

from rsyncfns import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

PORT = 12943

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

# Each quote context the tracker distinguishes, plus the nested-shell form that
# defeats escaping entirely.  %s is printf's format; the value is its argument.
TEMPLATES = {
    'unq': 'printf %s {v} >{out}',
    'sq': "printf %s '{v}' >{out}",
    'dq': 'printf %s "{v}" >{out}',
    'dqapos': 'printf %s "it\'s {v}" >{out}',
    'nest': "sh -c 'printf %s {v} >{out}'",
}

# No spaces or ':' are possible in a wire username; ${IFS} supplies the
# separator once command substitution is live.
MARKER = 'metachar-pwned'
HOSTILE = f'$(touch${{IFS}}{MARKER})x'
BENIGN = 'alice.b-1_x'

sentinel = Path.cwd() / MARKER
if sentinel.exists():
    sentinel.unlink()

modules = []
cases = []
for label, template in TEMPLATES.items():
    for kind, user in (('bad', HOSTILE), ('ok', BENIGN)):
        mod = f'{label}_{kind}'
        dest = SCRATCHDIR / f'metachar-dest-{mod}'
        rmtree(dest)
        makepath(dest)
        out = SCRATCHDIR / f'metachar-out-{mod}'
        if out.exists():
            out.unlink()
        secrets = SCRATCHDIR / f'metachar-{mod}.secrets'
        secrets.write_text(f'{user}:pw\n')
        secrets.chmod(0o600)
        modules.append((mod, {
            'path': str(dest),
            'read only': 'no',
            'use chroot': 'no',
            'auth users': '*',
            'secrets file': str(secrets),
            'pre-xfer exec': template.format(v='%RSYNC_USER_NAME%', out=out),
        }))
        cases.append((mod, kind, user, out))

pwfile = SCRATCHDIR / 'metachar.password'
pwfile.write_text('pw\n')
pwfile.chmod(0o600)

conf = write_daemon_conf(modules, name='metachar.conf')
url = start_test_daemon(conf, PORT)
dlog = SCRATCHDIR / 'rsyncd.log'

os.environ['RSYNC_PASSWORD'] = 'wrong-environment-fallback'
for mod, kind, user, out in cases:
    before = len(dlog.read_text(errors='replace')) if dlog.exists() else 0
    user_url = url.replace('rsync://', f'rsync://{user}@', 1) + mod + '/'
    proc = subprocess.run(
        rsync_argv('-r', f'--password-file={pwfile}', f'{src}/', user_url),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log = (dlog.read_text(errors='replace')[before:]) if dlog.exists() else ''
    ctx = (f'[{mod}] rc={proc.returncode} stderr={proc.stderr.strip()[:200]!r} '
           f'log={log[-300:]!r}')

    if kind == 'bad':
        if sentinel.exists():
            sentinel.unlink()
            test_fail(f'peer value executed as shell syntax {ctx}')
        if proc.returncode == 0:
            test_fail(f'hostile value did not fail the transfer {ctx}')
        # Make sure it failed for the intended reason, not some other error.
        if 'shell metacharacter' not in log:
            test_fail(f'transfer failed but not via the metacharacter refusal {ctx}')
    else:
        if proc.returncode != 0:
            test_fail(f'refusal rule rejected an ordinary value {ctx}')
        if not out.exists():
            test_fail(f'positive control failed: hook did not run {ctx}')
        got = out.read_text()
        if BENIGN not in got:
            test_fail(f'hook did not receive the value intact: {got!r} {ctx}')

print('shell metacharacters in peer values are refused; ordinary values still run')
