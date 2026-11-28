#!/usr/bin/env python3

import os
import shutil
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv, run_rsync


@requires(features={'symlink'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    source = context.source
    source.mkdir()
    (source / 'a-current').write_bytes(b'already here')
    (source / 'b-exact').write_bytes(b'1234')
    (source / 'c-refused').write_bytes(b'12345')
    (source / 'd-later').touch()

    for mode in ('--inc-recursive', '--no-inc-recursive'):
        dest = context.scratch / mode[2:]
        dest.mkdir()
        shutil.copy2(source / 'a-current', dest / 'a-current')
        (dest / 'c-refused').symlink_to('prior')
        (dest / 'prior').write_bytes(b'preserve this data')
        result = run_rsync('-rt', mode, '--file-transfer-budget=4', f'{source}/', f'{dest}/',
                           capture_output=True, check=False)
        if result.returncode != 23 or 'c-refused' not in result.stderr:
            test_fail(f'{mode}: did not report the first refused file: {result.stderr}')
        if (dest / 'b-exact').read_bytes() != b'1234':
            test_fail(f'{mode}: an exact fit was not installed')
        if (not (dest / 'c-refused').is_symlink() or os.readlink(dest / 'c-refused') != 'prior'
                or (dest / 'prior').read_bytes() != b'preserve this data'):
            test_fail(f'{mode}: the refused destination was modified')
        if (dest / 'd-later').exists():
            test_fail(f'{mode}: a later file was admitted after the first refusal')

    dest = context.destination
    dest.mkdir()
    result = run_rsync('-rtn', '--file-transfer-budget=4', f'{source}/', f'{dest}/',
                       capture_output=True, check=False)
    if result.returncode != 23 or list(dest.iterdir()):
        test_fail('dry run did not apply admission without modifying the destination')

    zero_source = context.scratch / 'zero-source'
    zero_source.mkdir()
    (zero_source / 'a-empty').touch()
    (zero_source / 'b-full').write_bytes(b'x')
    result = run_rsync('-rt', '--file-transfer-budget=0', f'{zero_source}/', f'{dest}/',
                       capture_output=True, check=False)
    if result.returncode != 23 or sorted(path.name for path in dest.iterdir()) != ['a-empty']:
        test_fail('a zero budget did not admit the empty file and refuse the non-empty file')

    for refused in ('--delete', '--delete-missing-args', '--inplace', '--link-dest=../old',
                    '--remove-source-files', '-M--delete'):
        result = run_rsync('-rt', '--file-transfer-budget=4', refused, f'{source}/', f'{dest}/',
                           capture_output=True, check=False)
        if result.returncode != 1 or '--file-transfer-budget' not in result.stderr:
            test_fail(f'{refused}: did not refuse an incompatible mode: {result.stderr}')
    for protected in ((), ('-s',)):
        result = subprocess.run(
            rsync_argv('--file-transfer-budget=4', '--server', *protected, '.', str(dest)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 1 or '--file-transfer-budget requires' not in result.stderr:
            test_fail(f'server invocation did not refuse a file budget: {result.stderr}')
    if sorted(path.name for path in dest.iterdir()) != ['a-empty']:
        test_fail('a refused option affected the destination')


if __name__ == '__main__':
    run(test)
