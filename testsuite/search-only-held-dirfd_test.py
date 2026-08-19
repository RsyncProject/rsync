#!/usr/bin/env python3
"""Known-name operations must not require permission to list parent dirs.

The confined resolver holds directory descriptors to prevent symlink races.
On Linux, those traversal and *at() anchor descriptors can use O_PATH: opening
a known file beneath a searchable directory, or creating one beneath a
writable/searchable directory, does not require directory read permission.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rsyncfns import (
    SCRATCHDIR, forced_protocol, rmtree, rsync_argv, test_fail, test_skipped,
)

if not sys.platform.startswith('linux'):
    test_skipped('search-only held-dirfd coverage is Linux-specific')

launcher = []
if os.geteuid() == 0:
    setpriv = shutil.which('setpriv')
    if setpriv is None:
        test_skipped('setpriv is unavailable for the root-run testsuite')
    launcher = [setpriv, '--reuid=65534', '--regid=65534', '--clear-groups']

external_base = os.geteuid() == 0
base = (
    Path(tempfile.mkdtemp(prefix='rsync-search-only-held-dirfd-'))
    if external_base
    else SCRATCHDIR / 'search-only-held-dirfd'
)
rmtree(base)

src = base / 'src'
xonly = src / 'xonly'
readable = xonly / 'readable'
nested_src = src / 'nested'
exact_dest = base / 'exact-dest'
tree_dest = base / 'tree-dest'
unreadable_dest = base / 'unreadable-dest'
write_only_dest = base / 'write-only-dest'
nested_dest = base / 'nested-dest'
nested_parent = nested_dest / 'nested'
for path in (
    readable,
    nested_src,
    exact_dest,
    tree_dest,
    unreadable_dest,
    write_only_dest,
    nested_parent,
):
    path.mkdir(parents=True, exist_ok=True)

(xonly / 'exact').write_text('known file beneath search-only parent\n')
(readable / 'nested').write_text('enumerated below search-only ancestor\n')
incoming = src / 'incoming'
incoming.write_text('created beneath write-search-only destination\n')
(nested_src / 'known').write_text(
    'created beneath nested write-search-only parent\n'
)

if os.geteuid() == 0:
    for root, dirs, files in os.walk(base):
        os.chown(root, 65534, 65534)
        for name in dirs + files:
            os.chown(Path(root) / name, 65534, 65534)


def permission_probe(path, flag, expected, label):
    proc = subprocess.run(
        launcher + ['test', flag, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != expected:
        test_skipped(
            f'filesystem does not enforce {label}: test {flag} returned '
            f'{proc.returncode}, expected {expected}'
        )


failures = []
try:
    xonly.chmod(0o111)
    write_only_dest.chmod(0o333)
    nested_parent.chmod(0o333)

    permission_probe(xonly, '-r', 1, 'search-only mode')
    permission_probe(xonly, '-x', 0, 'search-only mode')
    permission_probe(write_only_dest, '-r', 1, 'write-search-only mode')
    permission_probe(write_only_dest, '-w', 0, 'write-search-only mode')
    permission_probe(write_only_dest, '-x', 0, 'write-search-only mode')
    permission_probe(nested_parent, '-r', 1, 'nested write-search-only mode')
    permission_probe(nested_parent, '-w', 0, 'nested write-search-only mode')
    permission_probe(nested_parent, '-x', 0, 'nested write-search-only mode')

    # Keep received implied dirs usable on systems without a safe fchmodat2.
    # The source remains mode 0111, so sender traversal coverage is unchanged.
    exact = subprocess.run(
        launcher + rsync_argv(
            '-aR', '--chmod=Du+rw', 'xonly/exact', f'{exact_dest}/',
        ),
        cwd=src,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    exact_path = exact_dest / 'xonly' / 'exact'
    exact_content = exact_path.read_text() if exact_path.is_file() else None
    if exact.returncode != 0 or exact_content != (
        'known file beneath search-only parent\n'
    ):
        failures.append(
            'exact -R source beneath mode 0111 failed: '
            f'rc={exact.returncode}, stderr={exact.stderr.strip()!r}, '
            f'content={exact_content!r}'
        )

    tree = subprocess.run(
        launcher + rsync_argv(
            '-aR', '--chmod=Du+rw', 'xonly/readable/', f'{tree_dest}/',
        ),
        cwd=src,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    tree_path = tree_dest / 'xonly' / 'readable' / 'nested'
    tree_content = tree_path.read_text() if tree_path.is_file() else None
    if tree.returncode != 0 or tree_content != (
        'enumerated below search-only ancestor\n'
    ):
        failures.append(
            'readable directory beneath mode 0111 ancestor failed: '
            f'rc={tree.returncode}, stderr={tree.stderr.strip()!r}, '
            f'content={tree_content!r}'
        )

    unreadable = subprocess.run(
        launcher + rsync_argv(
            '-a', 'xonly/', f'{unreadable_dest}/',
        ),
        cwd=src,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if unreadable.returncode == 0:
        failures.append(
            'mode 0111 source directory was enumerable without read permission'
        )

    receiver = subprocess.run(
        launcher + rsync_argv(
            '-t', str(incoming), f'{write_only_dest}/',
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    received = write_only_dest / 'incoming'
    received_content = received.read_text() if received.is_file() else None
    if receiver.returncode != 0 or received_content != (
        'created beneath write-search-only destination\n'
    ):
        failures.append(
            'known-file creation beneath mode 0333 destination failed: '
            f'rc={receiver.returncode}, stderr={receiver.stderr.strip()!r}, '
            f'content={received_content!r}'
        )

    # Protocol 29 rejects this nested -R shape before the resolver is reached.
    proto = forced_protocol()
    if proto is None or proto >= 30:
        nested_receiver = subprocess.run(
            launcher + rsync_argv(
                '-tR', '--no-implied-dirs', 'nested/known', f'{nested_dest}/',
            ),
            cwd=src,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        nested_received = nested_parent / 'known'
        nested_content = (
            nested_received.read_text() if nested_received.is_file() else None
        )
        if nested_receiver.returncode != 0 or nested_content != (
            'created beneath nested write-search-only parent\n'
        ):
            failures.append(
                'known-file creation beneath nested mode 0333 destination '
                f'failed: rc={nested_receiver.returncode}, '
                f'stderr={nested_receiver.stderr.strip()!r}, '
                f'content={nested_content!r}'
            )
finally:
    xonly.chmod(0o755)
    write_only_dest.chmod(0o755)
    nested_parent.chmod(0o755)
    for dest in (exact_dest, tree_dest):
        copied_xonly = dest / 'xonly'
        if copied_xonly.is_dir():
            copied_xonly.chmod(0o755)
    if external_base:
        rmtree(base)

if failures:
    test_fail('\n'.join(failures))
