#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

REFUSAL = 'holds a shell metacharacter'

os.environ['RSYNC_MODULE_PATH'] = '/inherited-value-that-rsync-must-overwrite'

base = SCRATCHDIR / 'exec-metachar-documented-limit'
rmtree(base)
module = base / 'My Backups'
makepath(module)
(module / 'f.txt').write_text('DATA\n')

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

hook = base / 'hook.sh'
witness = base / 'saw.txt'
hook.write_text(f'#!/bin/sh\nprintf %s "$RSYNC_MODULE_PATH" > {shlex.quote(str(witness))}\n')
hook.chmod(0o755)

dest2 = base / 'dest2'
makepath(dest2)
conf2 = write_daemon_conf([
    ('m', {'path': str(module), 'read only': 'yes',
           'pre-xfer exec': shlex.quote(str(hook))}),
], global_options={'pid file': str(base / 'rsyncd2.pid')},
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

port = 12995
for ch in ('*', '?', '[', ']', '#', '!', '~', '{', '}'):
    mod = base / f'ch{ord(ch)}' / f'x{ch}y'
    makepath(mod)
    (mod / 'f.txt').write_text('DATA\n')
    chlog = base / f'log{ord(ch)}'
    c = write_daemon_conf([
        ('m', {'path': str(mod), 'read only': 'yes',
               'pre-xfer exec': "sh -c 'printf %s \"%RSYNC_MODULE_PATH%\" >/dev/null'"}),
    ], global_options={'pid file': str(base / f'pid{ord(ch)}'), 'log file': str(chlog)},
        name=f'exec-metachar-{ord(ch)}.conf')
    u = start_test_daemon(c, port)
    port += 1
    out = base / f'out{ord(ch)}'
    makepath(out)
    r = subprocess.run(rsync_argv('-r', f'{u}m/', f'{out}/'),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode == 0:
        test_fail(f'a module path containing {ch!r} was interpolated into a hook '
                  'and served; rsyncd.conf(5) lists it as refused')
    chlog_text = chlog.read_text(errors='replace') if chlog.is_file() else ''
    if REFUSAL not in chlog_text:
        test_fail(f'the transfer for {ch!r} failed, but not with the documented '
                  f'refusal -- the module path exists, so something else went '
                  f'wrong and this check proves nothing: {r.stdout.strip()[:200]!r}')

print('an operator value with a space is refused when interpolated, and works '
      'when read from the environment, exactly as rsyncd.conf(5) says; every '
      'documented character is refused too')
