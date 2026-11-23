#!/usr/bin/env python3

import os
import re
import shutil
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_argv, rsync_path_arg


COMPLETED_ROW = re.compile(r'(\d+)% .*\(xfr#(\d+), (?:ir|to)-chk=(\d+)/\d+\)')
SIZE = 64 * 1024
MTIME = 1600000000


def write(path, data):
    path.write_bytes(data)
    os.utime(path, (MTIME, MTIME))


def populate(source, destination, names, unchanged, damaged):
    for path in (source, destination):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True)
    for index, name in enumerate(names):
        data = bytes([65 + index]) * SIZE
        write(source / name, data)
        if name in unchanged:
            write(destination / name, data)
        elif name in damaged:
            # A shorter copy with a bad start: --append-verify fails its
            # verification and the redo phase sends the file again.
            write(destination / name, b'\0' * (SIZE // 2))


def check(context, label, options, names, unchanged, expected, damaged=(), copies=1):
    source = context.source
    destination = context.destination
    remote = ['-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}']
    for direction, peer_options, sender, receiver in (
        ('local', [], f'{source}/', f'{destination}/'),
        ('push', remote, f'{source}/', f'lh:{destination}/'),
        ('pull', remote, f'lh:{source}/', f'{destination}/'),
    ):
        populate(source, destination, names, unchanged, damaged)
        result = subprocess.run(
            rsync_argv('-rt', '--no-inc-recursive', '--info=progress2', *options,
                       *peer_options, *([sender] * copies), receiver),
            capture_output=True, text=True, timeout=120,
        )
        where = f'{label} ({direction})'
        output = f'{result.stdout}{result.stderr}'
        if result.returncode:
            test_fail(f'{where}: transfer failed: {result.returncode}\n{output}')
        for name in names:
            if (destination / name).read_bytes() != (source / name).read_bytes():
                test_fail(f'{where}: {name} differs after the transfer\n{output}')
        # Progress rows overwrite each other, so they are separated by \r.
        rows = [tuple(map(int, match.groups()))
                for match in map(COMPLETED_ROW.search, re.split(r'[\r\n]+', result.stdout))
                if match]
        if not rows:
            test_fail(f'{where}: no completed progress rows\n{output}')
        # The first row of each transfer shows the percentage when it finished.
        finished = {}
        for percent, transfer, _ in rows:
            finished.setdefault(transfer, percent)
        if expected is not None and finished != expected:
            test_fail(f'{where}: expected {expected}, got {finished}\n{output}')
        if rows[-1][0] != 100 or rows[-1][2] != 0:
            test_fail(f'{where}: the final row is not 100% with nothing left to check\n{output}')
        if max(percent for percent, _, _ in rows) > 100:
            test_fail(f'{where}: the percentage went above 100\n{output}')


@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'progress', 'transfer'})
def test(context: TestContext):
    names = ['a', 'b', 'c', 'd', 'e']

    # Entries passed before a transfer leave the total as that transfer starts.
    check(context, 'unchanged first', [], names, ['a', 'b'], {1: 33, 2: 66, 3: 100})
    check(context, 'unchanged between', [], names, ['a', 'c', 'e'], {1: 25, 2: 66})

    # Entries after the last transfer only leave the total when the list ends.
    check(context, 'unchanged last', [], names, ['d', 'e'], {1: 20, 2: 40, 3: 60})

    # With nothing to transfer the summary is complete rather than 0%.
    check(context, 'all unchanged', [], names, names, {0: 100})
    check(context, 'nothing unchanged', [], names, [], {1: 20, 2: 40, 3: 60, 4: 80, 5: 100})

    # Naming the source twice puts every entry in the list twice.  The sides
    # drop the copies at different points, so only the end is the same.
    check(context, 'duplicate entries', [], names, ['a'], None, copies=2)

    # A file sent again by the redo phase counts as further work, so the
    # percentage ends at 100 instead of passing it.
    check(context, 'redo', ['--append-verify', '--no-whole-file'], names, ['a'],
          {1: 25, 2: 50, 3: 75, 4: 100, 5: 100}, damaged=['c'])
    check(context, 'redo of everything', ['--append-verify', '--no-whole-file'], ['a'], [],
          {1: 100, 2: 100}, damaged=['a'])


if __name__ == '__main__':
    run(test)
