#!/usr/bin/env python3

import os
import signal
import subprocess

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.results import test_fail, test_skipped
from harness.rsync import forced_protocol, rsync_argv


metadata(features={'dac_restriction', 'hardlinks', 'nonroot'}, transports={'pipe'},
         mutates={'filesystem', 'process'})

if os.geteuid() == 0:
    test_skipped('root can open a mode-000 source file', capability='nonroot')

base = SCRATCHDIR / 'hard-links-open-error'
rmtree(base)
source = base / 'source'
source.mkdir(parents=True)
old_time, source_time, newer_time = 1577836800, 1609459200, 1640995200
old_data = b'older content\n'
source_data = b'new content\n'
newer_data = b'newer local content\n'
separate_data = b'unrelated file\n'


def seed(directory, name, data, mtime):
    path = directory / name
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


seed(source, 'c1', source_data, source_time)
os.link(source / 'c1', source / 'c2')
os.link(source / 'c1', source / 'c3')
seed(source, 'other', separate_data, source_time)
os.chmod(source / 'c1', 0)
try:
    open(source / 'c1', 'rb').close()
except PermissionError:
    pass
else:
    test_skipped('mode 000 does not prevent this user reading the source',
                 capability='dac_restriction')


def make_destination(name):
    destination = base / name
    destination.mkdir()
    seed(destination, 'c1', old_data, old_time)
    return destination


def transfer(source_dir, destination, expected, *options):
    name = destination.name
    with subprocess.Popen(
            rsync_argv('-aH', *options, f'{source_dir}/', f'{destination}/'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate(timeout=5)
            test_fail(f'{name}: transfer stalled after the source open failed: '
                      f'{output!r}')
    if process.returncode != 23:
        test_fail(
            f'{name}: expected exit 23, got {process.returncode}: {output!r}')
    if 'Permission denied' not in output:
        test_fail(f'{name}: source open failure was not reported: {output!r}')
    actual = {str(path.relative_to(destination)): path.read_bytes()
              for path in destination.rglob('*') if path.is_file()}
    if actual != expected:
        test_fail(f'{name}: unexpected destination files: {actual!r}')


for name, options in (('incremental', ()), ('non-incremental', ('--no-inc-recursive',))):
    destination = make_destination(name)
    for member in ('c2', 'c3'):
        os.link(destination / 'c1', destination / member)
    transfer(source, destination,
             dict(c1=old_data, c2=old_data, c3=old_data, other=separate_data),
             *options)
    if len({(destination / member).stat().st_ino for member in ('c1', 'c2', 'c3')}) != 1:
        test_fail(f'{name}: failed transfer split the existing hard-link group')

transfer(source, make_destination('missing-waiters'),
         dict(c1=old_data, other=separate_data))

destination = make_destination('skipped-replacement')
seed(destination, 'c2', newer_data, newer_time)
transfer(source, destination,
         dict(c1=old_data, c2=newer_data, other=separate_data), '--update')
if (destination / 'c2').stat().st_mtime != newer_time:
    test_fail('skipped-replacement: --update changed the newer file timestamp')

protocol = forced_protocol()
if protocol is None or protocol >= 30:
    os.link(source / 'c1', source / 'c4')
    destination = make_destination('later-basis')
    seed(destination, 'c2', newer_data, newer_time)
    seed(destination, 'c3', source_data, source_time)
    transfer(source, destination,
             dict(c1=old_data, c2=newer_data, c3=source_data, c4=source_data,
                  other=separate_data), '--update', '--no-perms')
    if not (destination / 'c3').samefile(destination / 'c4'):
        test_fail('later-basis: the remaining member did not link to the matching file')

nested_source = base / 'nested-source'
nested_destination = base / 'cross-directory'
for directory in (nested_source, nested_destination):
    directory.mkdir()
for member in ('a', 'b', 'c', 'd'):
    (nested_source / member).mkdir()
(nested_destination / 'a').mkdir()
seed(nested_source / 'a', 'f', source_data, source_time)
for member in ('b', 'c', 'd'):
    os.link(nested_source / 'a' / 'f', nested_source / member / 'f')
seed(nested_source, 'other', separate_data, source_time)
os.chmod(nested_source / 'a' / 'f', 0)
seed(nested_destination / 'a', 'f', old_data, old_time)
transfer(nested_source, nested_destination,
         {'a/f': old_data, 'other': separate_data})
