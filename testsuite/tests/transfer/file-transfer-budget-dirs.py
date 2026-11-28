#!/usr/bin/env python3

import os
import stat
from contextlib import ExitStack

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import forced_protocol, run_rsync


def directory_metadata(path):
    info = path.stat()
    return stat.S_IMODE(info.st_mode), info.st_mtime_ns


def restore_access(*paths):
    for path in paths:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode):
            path.chmod(0o700)


def check_directories(context, cleanup):
    failures = []
    for mode in ('--inc-recursive', '--no-inc-recursive'):
        for options in ('-rpt', '-r'):
            case = context.scratch / (mode[2:] + options)
            source = case / 'from'
            dest = case / 'to'
            source.mkdir(parents=True)
            dest.mkdir()
            (source / 'a-refused').write_bytes(b'x')
            for relative in ('z-later', 'z-later/child'):
                (source / relative).mkdir()
                (dest / relative).mkdir()
            (source / 'z-later/child/file').write_bytes(b'new')
            (dest / 'z-later/child/file').write_bytes(b'old')
            cleanup.callback(restore_access, source / 'z-later', source / 'z-later/child',
                             dest / 'z-later', dest / 'z-later/child')
            before = {}
            for relative in ('z-later/child', 'z-later'):
                os.chmod(source / relative, 0o555)
                os.utime(source / relative, (1600000000, 1600000000))
                os.chmod(dest / relative, 0o775)
                os.utime(dest / relative, (1500000000, 1500000000))
                before[relative] = directory_metadata(dest / relative)

            result = run_rsync(options, mode, '--file-transfer-budget=0',
                               f'{source}/', f'{dest}/', capture_output=True, check=False)
            if result.returncode != 23 or 'a-refused' not in result.stderr:
                test_fail(f'{mode} {options}: missing budget refusal: {result.stderr}')
            if (dest / 'a-refused').exists() or (dest / 'z-later/child/file').read_bytes() != b'old':
                test_fail(f'{mode} {options}: an entry was installed after refusal')
            for relative, original in before.items():
                actual = directory_metadata(dest / relative)
                if actual != original:
                    failures.append(f'{mode} {options}: skipped {relative} changed '
                                    f'from {original} to {actual}')

            prepared = context.scratch / ('prepared-' + mode[2:] + options)
            source = prepared / 'from'
            dest = prepared / 'to'
            source.mkdir(parents=True)
            dest.mkdir()
            (source / 'a-admitted').write_bytes(b'1234')
            (source / 'b-refused').write_bytes(b'x')
            cleanup.callback(restore_access, source, dest)
            os.chmod(source, 0o555)
            os.chmod(dest, 0o555)
            os.utime(source, (1600000000, 1600000000))
            os.utime(dest, (1500000000, 1500000000))

            result = run_rsync(options, mode, '--file-transfer-budget=4',
                               f'{source}/', f'{dest}/', capture_output=True, check=False)
            if result.returncode != 23 or 'b-refused' not in result.stderr:
                test_fail(f'{mode} {options}: missing later refusal: {result.stderr}')
            if (dest / 'a-admitted').read_bytes() != b'1234' or (dest / 'b-refused').exists():
                test_fail(f'{mode} {options}: admitted work did not finish before refusal')
            actual = directory_metadata(dest)
            if actual[0] != 0o555 or (options == '-rpt' and actual != directory_metadata(source)):
                failures.append(f'{mode} {options}: prepared directory was not finalised: {actual}')

    if (forced_protocol() or 30) >= 30:
        for permissions in (0o555, 0o750):
            case = context.scratch / f'placeholder-{permissions:o}'
            source = case / 'from'
            dest = case / 'to'
            source.mkdir(parents=True)
            dest.mkdir()
            for relative in ('a-stop', 'z-created'):
                (source / relative).mkdir()
            (dest / 'a-stop').mkdir()
            (source / 'a-stop/refused').write_bytes(b'x')
            (source / 'z-created/unscheduled').write_bytes(b'x')
            cleanup.callback(restore_access, source / 'a-stop', source / 'z-created',
                             dest / 'a-stop', dest / 'z-created')
            for relative in ('a-stop', 'z-created'):
                os.chmod(source / relative, permissions)
                os.utime(source / relative, (1600000000, 1600000000))
            os.chmod(dest / 'a-stop', permissions)
            result = run_rsync('-rpt', '--inc-recursive', '--file-transfer-budget=0',
                               f'{source}/', f'{dest}/', capture_output=True, check=False)
            if result.returncode != 23 or 'a-stop/refused' not in result.stderr:
                test_fail(f'placeholder: missing budget refusal: {result.stderr}')
            for relative in ('a-stop', 'z-created'):
                actual = directory_metadata(dest / relative)
                if actual != directory_metadata(source / relative):
                    failures.append(f'placeholder: {relative} was not finalised: {actual}')
            if (dest / 'a-stop/refused').exists() or (dest / 'z-created/unscheduled').exists():
                test_fail('directory finalisation scheduled contents after refusal')

    if failures:
        test_fail('\n'.join(failures))


@requires(transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    with ExitStack() as cleanup:
        check_directories(context, cleanup)


if __name__ == '__main__':
    run(test)
