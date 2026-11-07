#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

REFUSAL = 'unsafe host characters for RSYNC_CONNECT_PROG'

base = SCRATCHDIR / 'connect-prog-nested'
rmtree(base)
base.mkdir(parents=True)

seen = base / 'seen'
probe = base / 'probe.sh'
probe.write_text(f'#!/bin/sh\nprintf %s "$1" > {shlex.quote(str(seen))}\n')
probe.chmod(0o755)

def run(host, prog):
    env = os.environ.copy()
    env['RSYNC_CONNECT_PROG'] = prog
    return subprocess.run(
        rsync_argv(f'rsync://{host}/m/', str(base / 'out')),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        cwd=str(base))

marker = base / 'pwned'
proc = run('localhost;touch${IFS}pwned;#', "sh -c 'printf %H >/dev/null'")
if 'socketpair_tcp failed' in proc.stdout:
    test_skipped('RSYNC_CONNECT_PROG socketpair is blocked in this environment')
if marker.exists():
    test_fail('RSYNC_CONNECT_PROG host escaped the template quote context and '
              'ran a command in the nested shell')
if proc.returncode == 0:
    test_fail(f'the malicious host completed a transfer: {proc.stdout!r}')
if REFUSAL not in proc.stdout:
    test_fail('the shell-active host was not refused for BEING shell-active; '
              'something else stopped the transfer, so this check would pass '
              f'with the guard removed: {proc.stdout!r}')

for host, why in (('-c', "looks like an option to the connect program"),
                  ('+x', "looks like an option too: `sh +x` is as real as `sh -x`"),
                  ('~root', "is tilde-expanded by the nested shell"),
                  ('%self', "is expanded by a nested fish, where %self is its pid"),
                  ('', "vanishes when a nested shell re-splits, shifting later args")):
    seen.unlink(missing_ok=True)
    proc = run(host, f'{probe} %H')
    if REFUSAL not in proc.stdout:
        got = seen.read_text() if seen.exists() else '(program not reached)'
        test_fail(f'host {host!r} was accepted, and it {why}; '
                  f'the connect program received {got!r}')

for host, expect in (('example.com', 'example.com'),
                     ('example.com.', 'example.com.'),
                     ('host_name', 'host_name'),
                     ('host+alias', 'host+alias'),
                     ('host~alias', 'host~alias'),
                     ('[fe80::1%eth0]', 'fe80::1%eth0')):
    seen.unlink(missing_ok=True)
    proc = run(host, f'{probe} %H')
    if REFUSAL in proc.stdout:
        test_fail(f'host {host!r} is legitimate but was refused: {proc.stdout!r}')
    if not seen.exists():
        test_fail(f'host {host!r} never reached the connect program: {proc.stdout!r}')
    got = seen.read_text()
    if got != expect:
        test_fail(f'host {host!r} reached the connect program as {got!r}, '
                  f'expected {expect!r}')

print('RSYNC_CONNECT_PROG refuses a shell-active host and an option- or '
      'tilde-active first character, and still passes ordinary ones through')
