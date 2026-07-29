#!/usr/bin/env python3
"""A metacharacter-free username becomes find arguments in a nested shell."""

import subprocess

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf

base = SCRATCHDIR / 'exec-second-shell-argv'
rmtree(base)
module = base / 'module'
makepath(module)
sentinel = module / 'pwned'

# No shell metacharacters from shell_unsafe_value() appear in this username.
# It is data to the outer shell but a pathname expansion in the nested shell.
user = 'touc?'
password = 'known-password'
secrets = base / 'secrets'
secrets.write_text(f'{user}:{password}\n')
secrets.chmod(0o600)
pwfile = base / 'password'
pwfile.write_text(password + '\n')
pwfile.chmod(0o600)

conf = write_daemon_conf([
    ('m', {
        'path': str(module), 'read only': 'no', 'auth users': '*',
        'secrets file': str(secrets),
        'pre-xfer exec': f"sh -c '/usr/bin/%RSYNC_USER_NAME% {sentinel}'",
    }),
])
url = start_test_daemon(conf, 12983).replace('rsync://', f'rsync://{user}@', 1) + 'm/'
proc = subprocess.run(
    rsync_argv('-r', f'--password-file={pwfile}', f'{module}/', url),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

if sentinel.exists():
    test_fail('nested-shell peer value executed a command')
if proc.returncode == 0:
    test_fail('daemon accepted a shell-active peer value')
print('daemon rejected the nested-shell peer value')
