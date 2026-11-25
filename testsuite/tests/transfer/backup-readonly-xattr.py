#!/usr/bin/env python3

import os
import stat

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness.xattrs import get_value, is_supported, set_value


@requires(features={'xattr_runtime', 'nonroot'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    if os.geteuid() == 0:
        test_skipped('requires non-root directory permissions', capability='nonroot')
    if not is_supported():
        test_skipped('xattrs are unavailable', capability='xattr_runtime')

    source = context.source / 'outer' / 'nested'
    destination = context.destination / 'outer' / 'nested'
    backup = context.scratch / 'backup'
    saved = backup / 'outer' / 'nested'
    for directory in (source, destination, saved):
        directory.mkdir(parents=True)
    old = destination / 'file'
    new = source / 'file'
    old.write_bytes(b'previous data')
    new.write_bytes(b'updated data')
    value = 'same large xattr value ' * 16
    set_value('backup-test', value, old, new)
    saved.chmod(0o400)
    args = ('-aX', f'--backup-dir={backup}', f'{context.source}/', f'{context.destination}/')

    try:
        for options in (('--inplace', '--no-whole-file'), ('--no-inplace',)):
            run_rsync(*args[:2], *options, *args[2:])
            if old.read_bytes() != new.read_bytes():
                test_fail(f'{options}: the updated file was not installed')
            if get_value('backup-test', old) != value.encode():
                test_fail(f'{options}: the transferred xattr was lost')
            if stat.S_IMODE(saved.stat().st_mode) != 0o400:
                test_fail(f'{options}: the saved directory mode changed')
            saved.chmod(0o500)
            try:
                if get_value('backup-test', saved / 'file') != value.encode():
                    test_fail(f'{options}: the backup xattr was lost')
            finally:
                saved.chmod(0o400)
            new.write_bytes(new.read_bytes() + b'!')
    finally:
        if saved.is_dir():
            saved.chmod(0o700)


if __name__ == '__main__':
    run(test)
