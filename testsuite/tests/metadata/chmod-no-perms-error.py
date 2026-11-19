#!/usr/bin/env python3

import errno
import os
import shutil
import stat
import subprocess

from harness import metadata
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import SCRATCHDIR, SRCDIR
from harness.filesystem import rmtree
from harness.results import test_error, test_fail
from harness.rsync import rsync_argv


metadata(features={'daemon', 'linux_interpose'}, transports={'pipe'},
         mutates={'filesystem', 'process'})

base = SCRATCHDIR / 'chmod-no-perms-error'
rmtree(base)
source = base / 'source'
source.mkdir(parents=True)
(source / 'payload').write_text('payload data\n')
os.chmod(source / 'payload', 0o644)
os.utime(source / 'payload', (1577836800, 1577836800))

library = base / 'chmod-error.so'
fixture = SRCDIR / 'testsuite' / 'fixtures' / 'chmod-error.c'
build = subprocess.run(
    ['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode:
    test_error(f'cannot build chmod hook: {build.stdout!r}')


def fault_environment(destination, marker, error, *, lookup=False):
    env = os.environ.copy()
    env.pop('RSYNC_TEST_LOOKUP_ERRNO', None)
    env.pop('RSYNC_TEST_CHMOD_ERRNO', None)
    env.update({
        'LD_PRELOAD': str(library),
        'RSYNC_TEST_CHMOD_DIR': str(destination.resolve()),
        'RSYNC_TEST_CHMOD_MARKER': str(marker.resolve()),
    })
    env['RSYNC_TEST_LOOKUP_ERRNO' if lookup else 'RSYNC_TEST_CHMOD_ERRNO'] = str(error)
    return env


def transfer(name, preserve, error, expected, *, lookup=False):
    destination = base / name
    marker = base / f'{name}-hook-fired'
    env = fault_environment(destination, marker, error, lookup=lookup)
    result = subprocess.run(
        rsync_argv('-rt' if lookup else '-r', preserve, f'{source}/', f'{destination}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if not marker.exists():
        test_fail(f'{name}: requested failure was not injected: {result.stdout!r}')
    if result.returncode != expected:
        test_fail(
            f'{name}: expected exit {expected}, got {result.returncode}: '
            f'{result.stdout!r}')
    copied = destination / 'payload'
    if expected == 0 and (
            not copied.is_file() or copied.read_text() != 'payload data\n'):
        test_fail(f'{name}: copied data is missing or changed')
    return result.stdout


for name, error in (('optional-eperm', errno.EPERM), ('optional-eacces', errno.EACCES)):
    output = transfer(name, '--no-perms', error, 0)
    if 'failed to set permissions' in output:
        test_fail(f'{name}: optional mode failure was reported: {output!r}')

for name, preserve, error in (
        ('required-perms', '--perms', errno.EPERM),
        ('required-executability', '--executability', errno.EACCES),
        ('mode-io-error', '--no-perms', errno.EIO)):
    output = transfer(name, preserve, error, 23)
    if os.strerror(error) not in output:
        test_fail(f'{name}: mode error was not reported: {output!r}')

for name, error in (('lookup-denied', errno.EACCES), ('lookup-eperm', errno.EPERM),
                    ('lookup-loop', errno.ELOOP)):
    output = transfer(name, '--no-perms', error, 23, lookup=True)
    if 'failed to set permissions' not in output or os.strerror(error) not in output:
        test_fail(f'{name}: lookup error was not reported: {output!r}')


# An explicit daemon mode still applies to an up-to-date file without --perms.
incoming = base / 'incoming'
incoming.mkdir()
payload = incoming / 'payload'
shutil.copy2(source / 'payload', payload)
logfile = base / 'daemon.log'
config = write_daemon_conf(
    [('probe', {'path': str(incoming), 'read only': 'no', 'incoming chmod': 'Fu+x'})],
    global_options={'log file': str(logfile)},
)
url = start_test_daemon(config, 12894)
command = rsync_argv('-rt', '--no-perms', f'{source}/', f'{url}probe/')
marker = base / 'incoming-hook-fired'
result = subprocess.run(
    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    env=fault_environment(incoming, marker, errno.EPERM),
)
if not marker.exists() or result.returncode != 23:
    test_fail(f'incoming chmod: expected a reported refusal and exit 23: {result.stdout!r}')
if 'failed to set permissions' not in result.stdout + logfile.read_text():
    test_fail('incoming chmod: failed mode was not diagnosed')
if stat.S_IMODE(payload.stat().st_mode) != 0o644:
    test_fail('incoming chmod: refusing chmod changed the existing mode')

result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if result.returncode != 0 or stat.S_IMODE(payload.stat().st_mode) != 0o744:
    test_fail(f'incoming chmod: control did not apply the requested mode: {result.stdout!r}')
if payload.read_text() != 'payload data\n':
    test_fail('incoming chmod: payload changed during a metadata-only update')
