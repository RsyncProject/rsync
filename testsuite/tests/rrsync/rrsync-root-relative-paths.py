#!/usr/bin/env python3

import os
import shlex
import subprocess

from fixtures.rrsync import rrsync_shell, rsync_wrapper, patched_rrsync
from harness.environment import RSYNC, SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv, rsync_path_arg
from harness import metadata

metadata(transports={'pipe'})

T = 1234567890

base = SCRATCHDIR / 'rrsync-root-relative-paths'
rmtree(base)
restricted = base / 'restricted'
source = base / 'source'
outside = base / 'outside'
pulled = base / 'pulled'
makepath(restricted / 'previous', source, outside, pulled)

(restricted / 'previous' / 'file').write_text('unchanged\n')
(source / 'file').write_text('unchanged\n')
(outside / 'secret').write_text('outside\n')
os.utime(restricted / 'previous' / 'file', (T, T))
os.utime(source / 'file', (T, T))

shim = base / 'rsync-shim'
rsync_wrapper(shim)
rrsync = patched_rrsync(base, rsync_path=str(shim))

rsh = base / 'fake-rsh'
rrsync_shell(rsh, rrsync, restricted)

rsh_absolute = base / 'fake-rsh-absolute'
rrsync_shell(rsh_absolute, rrsync, restricted, '-absolute')

def run(*args):
    return subprocess.run(
        rsync_argv('-e', str(rsh), *args),
        capture_output=True, text=True, timeout=30,
    )

listed = run('--list-only', 'dummy:/')
listed_ctx = (f'rc={listed.returncode}, stdout={listed.stdout.strip()!r}, '
              f'stderr={listed.stderr.strip()!r}')
if listed.returncode != 0 or 'previous' not in listed.stdout:
    test_fail(f'listing the restricted root with / failed ({listed_ctx})')

linked = run('-a', '--link-dest=/previous', str(source) + '/', 'dummy:/current')
linked_ctx = (f'rc={linked.returncode}, stdout={linked.stdout.strip()!r}, '
              f'stderr={linked.stderr.strip()!r}')
current = restricted / 'current' / 'file'
previous = restricted / 'previous' / 'file'
if linked.returncode != 0 or not current.is_file():
    test_fail(f'root-relative --link-dest transfer failed ({linked_ctx})')
current_stat = os.stat(current)
previous_stat = os.stat(previous)
if ((current_stat.st_dev, current_stat.st_ino)
        != (previous_stat.st_dev, previous_stat.st_ino)):
    test_fail(f'root-relative --link-dest did not hard-link to its basis '
              f'({linked_ctx})')

repeated = run('-a', 'dummy:///previous/file', str(pulled) + '/')
if (repeated.returncode != 0
        or not (pulled / 'file').is_file()
        or (pulled / 'file').read_text() != 'unchanged\n'):
    test_fail('repeated leading slashes did not resolve beneath the '
              f'restricted root (rc={repeated.returncode}, '
              f'stderr={repeated.stderr.strip()!r})')

rmtree(pulled)
pulled.mkdir()
absolute = subprocess.run(
    rsync_argv('-a', '-e', str(rsh_absolute),
               'dummy:' + str(restricted / 'previous' / 'file'),
               str(pulled) + '/'),
    capture_output=True, text=True, timeout=30,
)
if (absolute.returncode != 0
        or not (pulled / 'file').is_file()
        or (pulled / 'file').read_text() != 'unchanged\n'):
    test_fail('-absolute no longer accepts an in-tree server path '
              f'(rc={absolute.returncode}, stderr={absolute.stderr.strip()!r})')

outside_arg = '///' + str(outside / 'secret').lstrip('/')
rmtree(pulled)
pulled.mkdir()
escaped_pull = run('-a', 'dummy:' + outside_arg, str(pulled) + '/')
if (pulled / 'secret').exists():
    test_fail('repeated leading slashes escaped the restricted root on pull')

replacement = base / 'replacement'
replacement.write_text('replacement\n')
escaped_push = run('-a', str(replacement), 'dummy:' + outside_arg)
if (outside / 'secret').read_text() != 'outside\n':
    test_fail('repeated leading slashes escaped the restricted root on push')

print('rrsync preserves restricted-root paths without repeated-slash escapes')
