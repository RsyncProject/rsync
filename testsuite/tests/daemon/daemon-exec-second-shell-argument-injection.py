#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

REFUSAL = 'refusing to run shell hook: %RSYNC_USER_NAME% holds a shell metacharacter'

base = SCRATCHDIR / 'exec-second-shell-argv'
rmtree(base)
module = base / 'module'
makepath(module)
sentinel = module / 'pwned'

bindir = base / 'bin'
makepath(bindir)
victim = bindir / 'touch'
victim.write_text('#!/bin/sh\n: > "$1"\n')
victim.chmod(0o755)

user = 'touc?'
password = 'known-password'
secrets = base / 'secrets'
secrets.write_text(f'{user}:{password}\n')
secrets.chmod(0o600)
pwfile = base / 'password'
pwfile.write_text(password + '\n')
pwfile.chmod(0o600)

control = base / 'control-marker'
outer = os.environ.get('RSYNC_SHELL') or '/bin/sh'
subprocess.run([outer, '-c', f"sh -c '{bindir}/touc? {control}'"],
               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if not control.exists():
    test_fail(f'the nested-shell glob did not select {victim} in this '
              'environment, so this test cannot show rsync refusing it; the '
              'check below would pass no matter what rsync did')

conf = write_daemon_conf([
    ('m', {
        'path': str(module), 'read only': 'no', 'auth users': '*',
        'secrets file': str(secrets),
        'pre-xfer exec': f"sh -c '{bindir}/%RSYNC_USER_NAME% {sentinel}'",
    }),
])
url = start_test_daemon(conf, 12983).replace('rsync://', f'rsync://{user}@', 1) + 'm/'
proc = subprocess.run(
    rsync_argv('-r', f'--password-file={pwfile}', f'{module}/', url),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

if sentinel.exists():
    test_fail('the nested-shell peer value selected and ran a command: '
              f'{sentinel} was created')
if proc.returncode == 0:
    test_fail(f'the daemon accepted a shell-active peer value: {proc.stdout!r}')

log = SCRATCHDIR / 'rsyncd.log'
log_text = log.read_text(errors='replace') if log.exists() else ''
if REFUSAL not in log_text:
    test_fail('the transfer failed, but not because the value was refused for '
              'holding a shell metacharacter -- an authentication or startup '
              'failure would look exactly like this, and would pass without '
              f'the guard: client={proc.stdout.strip()[:200]!r}')

print('the daemon refused a peer value that a nested shell would have turned '
      'into a command')
