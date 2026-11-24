#!/usr/bin/env python3

import re

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import run_rsync


@requires(transports={'pipe'}, mutates={'filesystem', 'process'},
          tags={'transfer', 'regression'})
def test(context: TestContext):
    source = context.source
    destination = context.destination
    backup = context.scratch / 'backup'
    source.mkdir()
    destination.mkdir()
    backup.mkdir()
    old = bytes((i * 73 + 19) % 251 + 1 for i in range(128 * 1024))
    (destination / 'file').write_bytes(old)
    (source / 'file').write_bytes(old + b'!')
    result = run_rsync('-a', '--inplace', '--no-whole-file', '--stats',
                       f'--backup-dir={backup}', f'{source}/', f'{destination}/',
                       capture_output=True)
    match = re.search(r'^Matched data: ([0-9,]+) bytes$', result.stdout, re.MULTILINE)
    if not match or int(match.group(1).replace(',', '')) < len(old) // 2:
        test_fail(f'the delta did not match its saved basis:\n{result.stdout}')
    if (destination / 'file').read_bytes() != old + b'!':
        test_fail('the destination has incorrect contents')
    if (backup / 'file').read_bytes() != old:
        test_fail('the backup has incorrect contents')


if __name__ == '__main__':
    run(test)
