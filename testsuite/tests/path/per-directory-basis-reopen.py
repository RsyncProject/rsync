#!/usr/bin/env python3

import os
import re
import subprocess
import sys

from harness import TestContext, requires, run
from harness.mutation import find_compiler
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, under_valgrind


@requires(features={'linux_interpose', 'native_build'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'path', 'regression'})
def test(context: TestContext):
    if not sys.platform.startswith('linux') or under_valgrind():
        test_skipped('requires Linux openat interposition', capability='linux_interpose')
    compiler = find_compiler()
    if not compiler:
        test_skipped('no C compiler is available', capability='native_build')
    fixture = context.repository / 'testsuite/fixtures/per-directory-basis-reopen.c'
    library = context.scratch / 'per-directory-basis-reopen.so'
    build = subprocess.run(
        [*compiler, '-shared', '-fPIC', '-Wall', '-Wextra', '-o', str(library), str(fixture), '-ldl'],
        capture_output=True, text=True, timeout=30,
    )
    if build.returncode:
        test_fail(f'Cannot build per-directory basis reopen hook: {build.stderr}')

    source = context.scratch / 'source'
    season = source / 'season'
    season.mkdir(parents=True)
    stamp = 1_600_000_000
    basis = b'0123456789abcdef' * 8192
    updated = b'new\n' * 128 + basis[512:]
    following = b'ordinary following file\n'
    episode = season / 'episode'
    episode.write_bytes(updated)
    os.utime(episode, (stamp + 60, stamp + 60))
    (season / 'following').write_bytes(following)

    for protocol, options in (('configured', ()), ('29', ('--protocol=29',))):
        for fault in (False, True):
            label = f'protocol {protocol}, {"failed reopen" if fault else "control"}'
            dest = context.scratch / f'dest-{protocol}-{fault}'
            dest.mkdir()
            retained = dest / 'episode'
            retained.write_bytes(basis)
            os.utime(retained, (stamp, stamp))
            marker = context.scratch / f'generator-opened-{protocol}'
            env = {**os.environ, 'LC_ALL': 'C'}
            if fault:
                env.update(LD_PRELOAD=str(library), RSYNC_TEST_BASIS_PARENT=str(dest),
                           RSYNC_TEST_BASIS_MARKER=str(marker))
            result = subprocess.run(
                rsync_argv('-a', *options, '--copy-dest-per-dir=..', '--no-whole-file',
                           '--stats', '--no-human-readable', f'{source}/', f'{dest}/'),
                env=env, capture_output=True, text=True, timeout=20,
            )
            if fault:
                if not marker.is_file():
                    test_fail(f'{label}: the generator did not open the selected basis')
                error = 'cannot read per-directory delta basis for season/episode: No such file or directory'
                if result.returncode != 23 or error not in result.stderr:
                    test_fail(f'{label}: expected exit 23 for the missing basis, '
                              f'got {result.returncode}: {result.stderr}')
            elif result.returncode != 0:
                test_fail(f'{label}: expected a successful delta transfer: {result.stderr}')

            matched = re.search(r'^Matched data: ([0-9,]+) bytes$', result.stdout, re.MULTILINE)
            if not matched or int(matched[1].replace(',', '')) < len(basis) // 2:
                test_fail(f'{label}: the receiver did not consume the matched delta: {result.stdout!r}')
            target = dest / 'season'
            expected = {'following'} if fault else {'episode', 'following'}
            entries = {child.name for child in target.iterdir()} if target.is_dir() else set()
            if entries != expected or {child.name for child in dest.iterdir()} != {'episode', 'season'}:
                test_fail(f'{label}: unexpected destination entries: {sorted(dest.iterdir())}, {sorted(entries)}')
            if retained.read_bytes() != basis:
                test_fail(f'{label}: the basis changed')
            if (target / 'following').read_bytes() != following:
                test_fail(f'{label}: the following file has incorrect contents')
            if not fault and (target / 'episode').read_bytes() != updated:
                test_fail(f'{label}: the successful delta has incorrect contents')


if __name__ == '__main__':
    run(test)
