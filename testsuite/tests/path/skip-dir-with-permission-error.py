#!/usr/bin/env python3

import os

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync


@requires(features={'dac_restriction', 'dirfd_pinning', 'nonroot'}, transports={'pipe'},
          min_peer='4.0.0', mutates={'filesystem', 'process'}, tags={'regression'})
def test(context: TestContext):
    if os.geteuid() == 0:
        test_skipped('root can inspect a mode-000 source directory', capability='nonroot')
    if not (os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks):
        test_skipped('requires directory-relative marker inspection', capability='dirfd_pinning')

    source = context.source
    destination = context.destination
    unreadable = source / 'unreadable'
    unreadable.mkdir(parents=True)
    marker = unreadable / '.skip'
    marker.touch()
    (source / 'readable').touch()
    unreadable.chmod(0o000)
    try:
        try:
            with os.scandir(unreadable):
                marker.lstat()
        except PermissionError:
            pass
        else:
            test_skipped('mode 000 does not prevent this user inspecting the marker',
                         capability='dac_restriction')

        result = run_rsync('-a', '--skip-dir-with=.skip', f'{source}/', f'{destination}/',
                           check=False, capture_output=True)
        if result.returncode != 23 or 'cannot check marker' not in result.stderr:
            test_fail(f'expected a marker inspection error with exit 23, got {result.returncode}: '
                      f'{result.stderr!r}')
        if (destination / unreadable.name).exists():
            test_fail('copied a directory whose marker could not be inspected')
        if not (destination / 'readable').is_file():
            test_fail('did not copy the readable source file')
    finally:
        unreadable.chmod(0o700)


if __name__ == '__main__':
    run(test)
