#!/usr/bin/env python3
"""The hook metacharacter refusal applies to operator values too, as documented.

rsyncd.conf(5) states this under `pre-xfer exec`: a %VAR% expanded into a hook
command is refused if the value carries a shell-active character, and that
applies to EVERY %RSYNC_*% value including ones the operator supplied.  A module
whose path contains a space therefore cannot be interpolated into a hook, and
the documented alternative is to read the value from the environment instead.

The refusal is deliberate rather than an oversight -- rsync cannot tell an
operator's space from an attacker's once both are inside the same string, and
`path` may itself be built from a peer value (`path = /home/%RSYNC_USER_NAME%`).
It is pinned here because it is a real usability cost, and if it ever changes
the manual has to change with it.
"""

import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

REFUSAL = 'holds a shell metacharacter'

base = SCRATCHDIR / 'exec-metachar-documented-limit'
rmtree(base)
module = base / 'My Backups'          # an ordinary directory name with a space
makepath(module)
(module / 'f.txt').write_text('DATA\n')

# --- interpolated: refused ------------------------------------------------
dest = base / 'dest'
makepath(dest)
conf = write_daemon_conf([
    ('m', {
        'path': str(module), 'read only': 'yes',
        'pre-xfer exec': "sh -c 'printf %s \"%RSYNC_MODULE_PATH%\" >/dev/null'",
    }),
], name='exec-metachar-interpolated.conf')
url = start_test_daemon(conf, 12991)

proc = subprocess.run(rsync_argv('-r', f'{url}m/', f'{dest}/'),
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if proc.returncode == 0:
    test_fail('a module path containing a space was interpolated into a hook '
              'and served; rsyncd.conf(5) says it is refused, so either the '
              f'manual or the code is now wrong: {proc.stdout.strip()[:300]!r}')

log = SCRATCHDIR / 'rsyncd.log'
log_text = log.read_text(errors='replace') if log.exists() else ''
if REFUSAL not in log_text:
    test_fail('the transfer failed, but not with the documented metacharacter '
              f'refusal -- something else stopped it: {proc.stdout.strip()[:300]!r}')

# --- read from the environment: works -------------------------------------
# The manual offers this as the way to use such a value, so it has to work.
hook = base / 'hook.sh'
witness = base / 'saw.txt'
hook.write_text(f'#!/bin/sh\nprintf %s "$RSYNC_MODULE_PATH" > {witness}\n')
hook.chmod(0o755)

dest2 = base / 'dest2'
makepath(dest2)
conf2 = write_daemon_conf([
    ('m', {'path': str(module), 'read only': 'yes', 'pre-xfer exec': str(hook)}),
], globals={'pid file': str(base / 'rsyncd2.pid')},
    name='exec-metachar-environment.conf')
url2 = start_test_daemon(conf2, 12992)

proc2 = subprocess.run(rsync_argv('-r', f'{url2}m/', f'{dest2}/'),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if proc2.returncode != 0:
    test_fail('the documented workaround failed: reading $RSYNC_MODULE_PATH '
              'from the environment must not be subject to the refusal '
              f'({proc2.stdout.strip()[:300]!r})')
if not (dest2 / 'f.txt').is_file():
    test_fail(f'workaround reported success but transferred nothing: {proc2.stdout!r}')
if not witness.is_file():
    test_fail('the hook did not run at all, so the workaround is unproven')
got = witness.read_text()
if got != str(module):
    test_fail(f'the hook received {got!r} from the environment, not the module '
              f'path {str(module)!r} -- the space did not survive')

print('an operator value with a space is refused when interpolated, and works '
      'when read from the environment, exactly as rsyncd.conf(5) says')
