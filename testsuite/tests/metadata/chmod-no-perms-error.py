#!/usr/bin/env python3

import errno
import os
import subprocess

from harness import metadata
from harness.environment import SCRATCHDIR, SRCDIR
from harness.filesystem import rmtree
from harness.results import test_error, test_fail
from harness.rsync import rsync_argv


metadata(features={'linux_interpose'}, transports={'pipe'},
         mutates={'filesystem', 'process'})

base = SCRATCHDIR / 'chmod-no-perms-error'
rmtree(base)
source = base / 'source'
source.mkdir(parents=True)
(source / 'payload').write_text('payload data\n')

library = base / 'chmod-error.so'
fixture = SRCDIR / 'testsuite' / 'fixtures' / 'chmod-error.c'
build = subprocess.run(
    ['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode:
    test_error(f'cannot build chmod hook: {build.stdout!r}')


def transfer(name, preserve, error, expected):
    destination = base / name
    marker = base / f'{name}-hook-fired'
    env = os.environ.copy()
    env.update({
        'LD_PRELOAD': str(library),
        'RSYNC_TEST_CHMOD_DIR': str(destination.resolve()),
        'RSYNC_TEST_CHMOD_ERRNO': str(error),
        'RSYNC_TEST_CHMOD_MARKER': str(marker.resolve()),
    })
    result = subprocess.run(
        rsync_argv('-r', preserve, f'{source}/', f'{destination}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if not marker.exists():
        test_fail(f'{name}: chmod hook did not fire: {result.stdout!r}')
    if result.returncode != expected:
        test_fail(
            f'{name}: expected exit {expected}, got {result.returncode}: '
            f'{result.stdout!r}')
    copied = destination / 'payload'
    if expected == 0 and (
            not copied.is_file() or copied.read_text() != 'payload data\n'):
        test_fail(f'{name}: copied data is missing or changed')
    return result.stdout


optional_output = transfer('optional', '--no-perms', errno.EPERM, 0)
if 'failed to set permissions' in optional_output:
    test_fail(f'optional mode failure was reported: {optional_output!r}')

required_output = transfer('required', '--perms', errno.EPERM, 23)
if os.strerror(errno.EPERM) not in required_output:
    test_fail(f'required mode failure was not reported: {required_output!r}')

path_output = transfer('path-error', '--no-perms', errno.ELOOP, 23)
if os.strerror(errno.ELOOP) not in path_output:
    test_fail(f'path failure was not reported: {path_output!r}')
