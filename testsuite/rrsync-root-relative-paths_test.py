#!/usr/bin/env python3
"""Leading slashes through rrsync must remain relative to the restricted root."""

import os
import shlex
import subprocess

from rsyncfns import (
    RSYNC, SCRATCHDIR, makepath, patched_rrsync, rmtree, rsync_argv,
    rsync_path_arg, test_fail,
)

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
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
rrsync = patched_rrsync(base, rsync_path=str(shim))

rsh = base / 'fake-rsh'
rsh.write_text(
    '#!/bin/sh\n'
    'shift\n'
    'SSH_ORIGINAL_COMMAND="$*"\n'
    'export SSH_ORIGINAL_COMMAND\n'
    'exec %s %s\n' % (shlex.quote(str(rrsync)), shlex.quote(str(restricted))))
rsh.chmod(0o755)

rsh_absolute = base / 'fake-rsh-absolute'
rsh_absolute.write_text(
    '#!/bin/sh\n'
    'shift\n'
    'SSH_ORIGINAL_COMMAND="$*"\n'
    'export SSH_ORIGINAL_COMMAND\n'
    'exec %s -absolute %s\n'
    % (shlex.quote(str(rrsync)), shlex.quote(str(restricted))))
rsh_absolute.chmod(0o755)


def run(*args):
    return subprocess.run(
        rsync_argv('-e', str(rsh), *args),
        capture_output=True, text=True, timeout=30,
    )


# A leading slash denotes the root of the restricted tree, not an empty path.
listed = run('--list-only', 'dummy:/')
listed_ctx = (f'rc={listed.returncode}, stdout={listed.stdout.strip()!r}, '
              f'stderr={listed.stderr.strip()!r}')
if listed.returncode != 0 or 'previous' not in listed.stdout:
    test_fail(f'listing the restricted root with / failed ({listed_ctx})')

# Option operands use the same convention.  The basis is a sibling of the
# destination, so stripping the slash and leaving a relative name makes rsync
# look for destination/previous and silently transfer a full copy.
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

# Repeated leading slashes have the same restricted-root meaning.  Assert the
# authorised path still works rather than satisfying confinement by rejecting
# every repeated-slash path.
repeated = run('-a', 'dummy:///previous/file', str(pulled) + '/')
if (repeated.returncode != 0
        or not (pulled / 'file').is_file()
        or (pulled / 'file').read_text() != 'unchanged\n'):
    test_fail('repeated leading slashes did not resolve beneath the '
              f'restricted root (rc={repeated.returncode}, '
              f'stderr={repeated.stderr.strip()!r})')

# The explicit -absolute mode continues to accept a complete server path under
# the restricted directory; root-relative handling must not reinterpret it.
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

# Repeated leading slashes must not regain host-root meaning in either transfer
# direction.  The requested path is the absolute spelling of a file outside
# the restricted tree; a vulnerable wrapper would read or replace that file.
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
