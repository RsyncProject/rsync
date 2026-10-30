#!/usr/bin/env python3

import locale
import os
import shlex
import shutil
import subprocess
import sys

from rsyncfns import (
    FROMDIR, RSYNC, SCRATCHDIR, TODIR, build_rsyncd_conf, hands_setup,
    patched_rrsync, rsync_argv, rsync_path_arg, rsh_cmd, run_rsync,
    start_test_daemon, test_fail,
)


def output(proc):
    return (proc.stdout or '') + (proc.stderr or '')


def forwarded_option(*args):
    proc = run_rsync(
        '-nvv', '--rsh=echo', *args, f'localhost:{FROMDIR}/', f'{TODIR}/',
        check=False, capture_output=True,
    )
    for line in output(proc).splitlines():
        if 'opening connection using:' in line:
            return line
    test_fail(f'remote command was not reported:\n{output(proc)}')


valid = (
    (('--nice=remote:5/be_6',), '--nice=local:5/be_6'),
    (('--nice=remote:idle',), '--nice=local:idle'),
    (('--nice=remote:IDLE',), '--nice=local:idle'),
    (('--nice=remote:5', '--nice=remote:idle'), '--nice=local:5/idle'),
    (('--nice=remote:5,remote:idle',), '--nice=local:5/idle'),
    (('--nice=remote:5/be_6,remote:19/idle',), '--nice=local:19/idle'),
    (('--nice=remote:none',), '--nice=local:none'),
    (('--nice=remote:0',), '--nice=local:0'),
    (('--nice=REMOTE:-20/RT_0',), '--nice=local:-20/rt_0'),
    (('--nice=remote:19/BE_7',), '--nice=local:19/be_7'),
    (('--nice=ALL',), '--nice=local:19/idle'),
    (('--nice=local:19',), None),
    (('--nice=19/idle', '--no-nice'), None),
    (('--nice=local:6/idle,remote:5/be_6',), '--nice=local:5/be_6'),
)

for args, expected in valid:
    command = forwarded_option(*args)
    if expected and expected not in command:
        test_fail(f'{args!r} did not forward {expected!r}:\n{command}')
    if not expected and '--nice=' in command:
        test_fail(f'{args!r} unexpectedly forwarded --nice:\n{command}')

invalid = (
    '', 'local:', '2/', '20', '+20', '-21', '999999999999999999999999',
    'be_8', 'rt_9', 'locality', ' 1', '\t1',
    ' local:1', 'local: 1', 'local::1', 'idle/none', '1//idle', 'none/idle',
    'local:1,', 'local:1,,remote:2', 'somewhere:1', '19/idle/x',
)

for value in invalid:
    proc = run_rsync(f'--nice={value}', '--version', check=False, capture_output=True)
    if proc.returncode == 0 or 'Invalid argument passed to --nice' not in output(proc):
        test_fail(f'invalid --nice value was accepted: {value!r}')

# In Turkish, ASCII I lowercases to a dotless i rather than i. Option names use
# ASCII syntax, so their case matching must remain locale-independent.
saved_locale = locale.setlocale(locale.LC_CTYPE)
turkish_locale = None
try:
    for candidate in ('tr_TR.UTF-8', 'tr_TR.utf8', 'Turkish_Turkey.1254'):
        try:
            turkish_locale = locale.setlocale(locale.LC_CTYPE, candidate)
            break
        except locale.Error:
            pass
finally:
    locale.setlocale(locale.LC_CTYPE, saved_locale)

if turkish_locale:
    env = {**os.environ, 'LC_ALL': turkish_locale}
    proc = subprocess.run(
        rsync_argv('--nice=IDLE', '--version'), env=env,
        capture_output=True, text=True, timeout=20,
    )
    if proc.returncode != 0:
        test_fail(f'uppercase I/O priority depended on the locale:\n{output(proc)}')

true = shutil.which('true')
if true:
    rrsync_base = SCRATCHDIR / 'rrsync-nice'
    restricted = rrsync_base / 'restricted'
    restricted.mkdir(parents=True, exist_ok=True)
    rrsync = patched_rrsync(rrsync_base, rsync_path=true)
    for setting in ('local', 'local:19', 'local:idle', 'local:19/idle',
                    'LOCAL:19/IDLE'):
        env = {**os.environ,
               'SSH_ORIGINAL_COMMAND': f'rsync --server --nice={setting} . .'}
        proc = subprocess.run(
            [str(rrsync), '-no-lock', str(restricted)], env=env,
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            test_fail(f'rrsync rejected safe --nice setting {setting!r}:\n{output(proc)}')

    for setting in ('local:18', 'local:none', 'local:be_7', 'local:19/be_7',
                    'all:19/idle', 'local:-1/rt_0'):
        env = {**os.environ,
               'SSH_ORIGINAL_COMMAND': f'rsync --server --nice={setting} . .'}
        proc = subprocess.run(
            [str(rrsync), '-no-lock', str(restricted)], env=env,
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode == 0 or 'unsafe --nice setting' not in output(proc):
            test_fail(f'rrsync accepted unsafe --nice setting {setting!r}:\n{output(proc)}')

proc = run_rsync('--daemon', '--nice=local:19/idle', '--no-nice', '--help',
                 check=False, capture_output=True)
if proc.returncode != 0:
    test_fail(f'daemon niceness options were rejected:\n{output(proc)}')

hands_setup()
(FROMDIR / 'file').write_text('data\n')
rsh = rsh_cmd(None, '--no-cd')
proc = run_rsync(
    '-a', '--debug=CMD1', '-M--debug=CMD1',
    '--nice=local,remote:18', f'{FROMDIR}/', f'{TODIR}/',
    capture_output=True,
)
text = output(proc)
cpu_supported = 'cannot set client CPU priority to 19: operation is not supported' not in text
io_supported = 'cannot set client I/O priority to idle: operation is not supported' not in text
if cpu_supported:
    if 'set client CPU priority to 19' not in text:
        test_fail(f'local-transfer client priority was not applied:\n{text}')
    if 'set server CPU priority to 18' not in text:
        test_fail(f'local-transfer server priority was not applied independently:\n{text}')
if io_supported and 'set client I/O priority to idle' not in text:
    test_fail(f'target-only local I/O priority was not applied:\n{text}')

shutil.rmtree(TODIR)
proc = run_rsync(
    '-a', '--debug=CMD1', '-M--debug=CMD1', '-M--nice=local:17',
    '--nice=local:19,remote:18', f'{FROMDIR}/', f'{TODIR}/',
    capture_output=True,
)
text = output(proc)
if cpu_supported and 'set server CPU priority to 17' not in text:
    test_fail(f'explicit remote option did not override the remote priority:\n{text}')

shutil.rmtree(TODIR)
proc = run_rsync(
    '-nr', '--debug=CMD1', '--nice=local:19/idle', '--no-nice',
    f'{FROMDIR}/', f'{TODIR}/', capture_output=True,
)
if 'CPU priority' in output(proc) or 'I/O priority' in output(proc):
    test_fail(f'--no-nice did not clear local priority settings:\n{output(proc)}')

proc = run_rsync(
    '-a', '--debug=CMD1', '-M--debug=CMD1',
    '--nice=local:19/idle', '--nice=remote:19/idle',
    f'--rsh={rsh}', f'--rsync-path={rsync_path_arg(RSYNC)}',
    f'{FROMDIR}/', f'localhost:{TODIR}/', capture_output=True,
)
text = output(proc)
cpu_supported = not any(
    'CPU priority' in line and 'not supported' in line for line in text.splitlines()
)
io_supported = not any(
    'I/O priority' in line and 'not supported' in line for line in text.splitlines()
)
if cpu_supported:
    for role in ('client', 'server'):
        if f'set {role} CPU priority to 19' not in text:
            test_fail(f'{role} CPU priority was not applied:\n{text}')
if io_supported:
    for role in ('client', 'server'):
        if f'set {role} I/O priority to idle' not in text:
            test_fail(f'{role} I/O priority was not applied:\n{text}')

shutil.rmtree(TODIR)
proc = run_rsync(
    '-as', '--debug=CMD1', '-M--debug=CMD1', '--nice=remote:19', f'--rsh={rsh}',
    f'--rsync-path={rsync_path_arg(RSYNC)}', f'{FROMDIR}/',
    f'localhost:{TODIR}/', capture_output=True,
)
text = output(proc)
if cpu_supported and 'set server CPU priority to 19' not in text:
    test_fail(f'remote priority with secluded arguments was not applied:\n{text}')

shutil.rmtree(TODIR)
proc = run_rsync(
    '-a', '-M--debug=CMD1', '--nice=remote:none', f'--rsh={rsh}',
    f'--rsync-path={rsync_path_arg(RSYNC)}', f'{FROMDIR}/',
    f'localhost:{TODIR}/', capture_output=True,
)
if io_supported and 'set server I/O priority to none' not in output(proc):
    test_fail(f'remote I/O priority was not reset:\n{output(proc)}')

conf = build_rsyncd_conf()
with conf.open('a') as fh:
    fh.write(f'''\

[test-nice-refused]
    path = {FROMDIR}
    read only = yes
    refuse options = nice

[test-nice-rsh]
    path = {FROMDIR}
    read only = yes
    hosts allow = *
''')
daemon_rsh = (
    f'{rsync_path_arg(RSYNC)} --config={shlex.quote(str(conf))} '
    f'--daemon --nice=local:19/be_7'
)
proc = run_rsync(
    '-nr', '--debug=CMD1', '-M--debug=CMD1', '--nice=remote:19/idle',
    f'--rsh={rsh}', f'--rsync-path={daemon_rsh}', 'localhost::test-nice-rsh/',
    f'{SCRATCHDIR}/daemon-rsh-copy/', capture_output=True,
)
text = output(proc)
if cpu_supported and 'set daemon CPU priority to 19' not in text:
    test_fail(f'remote-shell daemon CPU priority was not applied:\n{text}')
if io_supported and 'set daemon I/O priority to idle' not in text:
    test_fail(f'remote-shell daemon I/O priority was not applied:\n{text}')

idle_code = (
    'import os\n'
    'os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))\n'
)
sched_idle = False
if hasattr(os, 'sched_setscheduler') and hasattr(os, 'SCHED_IDLE'):
    probe = subprocess.run(
        [sys.executable, '-c', idle_code], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=20,
    )
    sched_idle = probe.returncode == 0

if sched_idle:
    idle_wrapper = SCRATCHDIR / 'sched-idle-rsync.py'
    idle_wrapper.write_text(
        idle_code + 'import sys\nos.execvp(sys.argv[1], sys.argv[1:])\n'
    )
    idle_command = shlex.join([sys.executable, str(idle_wrapper), *shlex.split(RSYNC)])
    idle_daemon_rsh = (
        f'{idle_command} --config={shlex.quote(str(conf))} --daemon'
    )
    proc = run_rsync(
        '-nr', '--nice=remote:be_7', f'--rsh={rsh}',
        f'--rsync-path={idle_daemon_rsh}', 'localhost::test-nice-rsh/',
        f'{SCRATCHDIR}/idle-daemon-copy/', check=False, capture_output=True,
    )
    if proc.returncode == 0 or 'daemon clients may only request idle I/O priority' not in output(proc):
        test_fail(f'SCHED_IDLE daemon accepted an I/O priority increase:\n{output(proc)}')

daemon_log = SCRATCHDIR / 'rsyncd.log'
daemon_log.unlink()
daemon_url = start_test_daemon(
    conf, 12898, rsync_cmd=f'{RSYNC} --nice=local:19/be_7',
)
for setting in ('19', 'idle', '19/idle'):
    proc = run_rsync(
        '-nr', '--debug=CMD1', '-M--debug=CMD1', f'--nice=remote:{setting}',
        f'{daemon_url}test-from/', f'{SCRATCHDIR}/daemon-copy/',
        capture_output=True,
    )
    text = output(proc)
    if setting == '19/idle':
        if cpu_supported and 'set daemon CPU priority to 19' not in text:
            test_fail(f'daemon CPU priority was not lowered:\n{text}')
        if io_supported and 'set daemon I/O priority to idle' not in text:
            test_fail(f'daemon I/O priority was not lowered:\n{text}')

for setting, message in (
    ('18', 'daemon clients may only request CPU nice 19'),
    ('be_7', 'daemon clients may only request idle I/O priority'),
):
    proc = run_rsync(
        '-nr', f'--nice=remote:{setting}', f'{daemon_url}test-from/',
        f'{SCRATCHDIR}/daemon-copy/', check=False, capture_output=True,
    )
    if proc.returncode == 0 or message not in output(proc):
        test_fail(f'daemon accepted unsafe --nice setting {setting!r}:\n{output(proc)}')

proc = run_rsync(
    '-nr', '--nice=remote:19', f'{daemon_url}test-nice-refused/',
    f'{SCRATCHDIR}/daemon-copy/', check=False, capture_output=True,
)
if proc.returncode == 0 or 'server is configured to refuse --nice' not in output(proc):
    test_fail(f'daemon did not refuse --nice:\n{output(proc)}')
