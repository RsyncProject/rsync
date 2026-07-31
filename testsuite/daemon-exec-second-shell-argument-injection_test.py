#!/usr/bin/env python3
"""A metacharacter-free peer value still selects a command in a nested shell.

`pre-xfer exec = sh -c '.../%RSYNC_USER_NAME% ...'` re-parses the substituted
word in a SECOND shell.  rsync escapes for the quoting context it sees, which is
correct for the outer shell only; the inner one gets the value bare.  A username
of `touc?` therefore carries no character that can execute in one level of
parsing, yet the inner shell glob-expands `<dir>/touc?` to `<dir>/touch` and
runs it.  The substitution has stopped being data and has chosen the command.

Two things this test has to avoid.

It must not pass merely because nothing happened: an absent marker plus a
non-zero exit is equally true when authentication failed, when the daemon could
not start, or when the globbed command was missing.  So the daemon log is
required to carry the specific refusal.

And it must not pass because the attack was inert here.  The control below runs
the same nested-shell expansion directly and requires it to work, so a pass
means rsync refused a live attack rather than that the mechanism never fired in
this environment.  The command it globs onto is one this test creates, rather
than /usr/bin/touch -- a hardcoded system path would make the whole thing hinge
on a binary the build never guaranteed.
"""

import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

REFUSAL = 'refusing to run shell hook: %RSYNC_USER_NAME% holds a shell metacharacter'

base = SCRATCHDIR / 'exec-second-shell-argv'
rmtree(base)
module = base / 'module'
makepath(module)
sentinel = module / 'pwned'

# The command the glob resolves to.  Ours, so the test does not depend on a
# system binary's presence or on its argument handling.  Exactly one file in
# this directory matches "touc?", so the expansion is unambiguous.
bindir = base / 'bin'
makepath(bindir)
victim = bindir / 'touch'
victim.write_text('#!/bin/sh\n: > "$1"\n')
victim.chmod(0o755)

# No character from shell_unsafe_value()'s set appears in this username: it is
# data to the outer shell and a pathname expansion in the nested one.
user = 'touc?'
password = 'known-password'
secrets = base / 'secrets'
secrets.write_text(f'{user}:{password}\n')
secrets.chmod(0o600)
pwfile = base / 'password'
pwfile.write_text(password + '\n')
pwfile.chmod(0o600)

# --- positive control: the attack must be live in this environment ---------
control = base / 'control-marker'
subprocess.run(['sh', '-c', f"sh -c '{bindir}/touc? {control}'"],
               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if not control.exists():
    test_fail(f'the nested-shell glob did not select {victim} in this '
              'environment, so this test cannot show rsync refusing it; the '
              'check below would pass no matter what rsync did')

# --- the daemon must refuse the same value --------------------------------
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
