#!/usr/bin/env python3

import os
import stat

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync


@requires(features={'nonroot'}, transports={'pipe'}, mutates={'filesystem', 'process'},
          tags={'transfer', 'regression'})
def test(context: TestContext):
    if os.geteuid() == 0:
        test_skipped('requires non-root directory permissions', capability='nonroot')

    source = context.source
    destination = context.destination
    backup = context.scratch / 'backup'
    outer = destination / 'outer'
    saved = backup / 'outer'
    source.mkdir()
    outer.mkdir(parents=True)
    (outer / 'first').write_bytes(b'first backup')
    outer.chmod(0o555)
    args = ('-a', f'--backup-dir={backup}', '--delete-during')

    def check_saved_mode():
        if stat.S_IMODE(saved.stat().st_mode) != 0o555:
            test_fail('rsync did not preserve the saved directory mode')

    try:
        run_rsync(*args, f'{source}/', f'{destination}/')
        check_saved_mode()
        if outer.exists() or (saved / 'first').read_bytes() != b'first backup':
            test_fail('the initial deletion was not backed up')

        (source / 'outer').mkdir()
        nested = outer / 'nested'
        nested.mkdir(parents=True)
        (nested / 'second').write_bytes(b'second backup')
        run_rsync(*args, f'{source}/', f'{destination}/')
        check_saved_mode()
        if nested.exists() or (saved / 'nested' / 'second').read_bytes() != b'second backup':
            test_fail('the next run did not back up a deletion beneath the saved directory')
        if (saved / 'first').read_bytes() != b'first backup':
            test_fail('reusing the directory changed an earlier backup')

        collision = outer / 'first'
        collision.mkdir()
        result = run_rsync(*args, f'{source}/', f'{destination}/', capture_output=True, check=False)
        if result.returncode != 23 or not collision.is_dir():
            test_fail('a failed directory backup did not preserve the original')
        check_saved_mode()
        if (saved / 'first').read_bytes() != b'first backup':
            test_fail('the collision changed an earlier backup')
        collision.rmdir()

        for option in ('--whole-file', '--no-whole-file'):
            name = option[2:]
            (outer / name).write_bytes(b'previous contents')
            (source / 'outer' / name).write_bytes(b'new')
            run_rsync(*args, '--inplace', option, f'{source}/', f'{destination}/')
            check_saved_mode()
            if (saved / name).read_bytes() != b'previous contents':
                test_fail(f'{option}: the earlier contents were not backed up')
            if (outer / name).read_bytes() != b'new':
                test_fail(f'{option}: the updated contents were not transferred')
    finally:
        for path in (outer, saved):
            if path.is_dir():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)


if __name__ == '__main__':
    run(test)
