#!/usr/bin/env python3

import os
import stat

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync


@requires(features={'dirfd_pinning', 'symlink'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'regression'})
def test(context: TestContext):
    source = context.source
    if not (os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks):
        test_skipped('requires directory-relative marker inspection', capability='dirfd_pinning')

    for name in ('omit', 'keep/nested', 'keep/direct', 'link-marker', 'dir-marker'):
        directory = source / name
        directory.mkdir(parents=True)
        (directory / 'payload').write_text('source data\n')
    (source / 'omit' / '.skip').touch()
    (source / 'keep' / 'nested' / '.skip').touch()
    (source / 'link-marker' / '.skip').symlink_to('payload')
    (source / 'dir-marker' / '.skip').mkdir()

    option = '--skip-dir-with=.skip'
    for recursion in ('--inc-recursive', '--no-inc-recursive'):
        destination = context.scratch / recursion[2:]
        saved = destination / 'keep' / 'nested' / 'saved'
        saved.parent.mkdir(parents=True)
        saved.write_text('keep this destination data\n')
        run_rsync('-a', option, recursion, f'{source}/', f'{destination}/')
        for path in ('keep/direct/payload', 'link-marker/payload', 'dir-marker/payload'):
            if (destination / path).read_text() != 'source data\n':
                test_fail(f'{recursion}: did not transfer {path}')
        if (destination / 'omit').exists() or (saved.parent / 'payload').exists():
            test_fail(f'{recursion}: transferred a marked directory')
        if saved.read_text() != 'keep this destination data\n':
            test_fail(f'{recursion}: changed the contents of a skipped directory')

    destination = context.destination
    destination.mkdir(mode=0o700)
    source.chmod(0o755)
    (source / '.skip').touch()
    run_rsync('-a', option, f'{source}/', f'{destination}/')
    run_rsync('-a', option, str(source), f'{destination}/')
    if list(destination.iterdir()) or stat.S_IMODE(destination.stat().st_mode) != 0o700:
        test_fail('a marked source root changed the destination')

    for invalid in ('--skip-dir-with=../.skip', '--skip-dir-with=.', '--skip-dir-with='):
        result = run_rsync('-a', invalid, f'{source}/', f'{destination}/',
                           check=False, capture_output=True)
        if result.returncode != 1 or 'marker basename' not in result.stderr:
            test_fail(f'{invalid}: expected a basename error: {result.stderr}')

    for incompatible in ('--delete-before', '--delete-missing-args', '--force',
                         '--relative', '-M--delete'):
        result = run_rsync('-a', option, incompatible, f'{source}/', f'{destination}/',
                           check=False, capture_output=True)
        if result.returncode != 1 or '--skip-dir-with' not in result.stderr:
            test_fail(f'{incompatible}: expected an explicit refusal: {result.stderr}')

    result = run_rsync('-a', option, '-e', 'not-a-test-shell', f'{source}/',
                       f'lh:{destination}/', check=False, capture_output=True)
    if result.returncode != 1 or 'local copy' not in result.stderr:
        test_fail(f'remote use did not fail before launching a shell: {result.stderr}')


if __name__ == '__main__':
    run(test)
