#!/usr/bin/env python3

import json
import os
import random
import subprocess
import sys
import time

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import forced_protocol, rsync_argv


WRITER = '''
import shutil
import sys

with open(sys.argv[1], 'wb') as fifo, open(sys.argv[2], 'rb') as payload:
    shutil.copyfileobj(payload, fifo)
    fifo.flush()
    print('written', flush=True)
    if sys.argv[3] == 'hold':
        sys.stdin.buffer.read(1)
'''


def invoke(*args):
    return subprocess.run(rsync_argv(*args), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          env={**os.environ, 'LC_ALL': 'C'}, timeout=20)


def expect(result, code, message=''):
    if result.returncode != code or message not in result.stderr:
        test_fail(f'{result.args}: expected exit {code} and {message!r}, got {result.returncode}: '
                  f'{result.stdout}{result.stderr}')


def transfer(source, target, size, data=None, *, hold=False, options=()):
    writer = None
    if data is not None:
        payload = source.parent / 'payload'
        payload.write_bytes(data)
        writer = subprocess.Popen(
            [sys.executable, '-c', WRITER, str(source), str(payload), 'hold' if hold else 'close'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    try:
        result = invoke('--stream-source', f'--stream-size={size}',
                        f'--stream-timeout={1 if hold or data is None else 5}',
                        *options, str(source), str(target))
        if writer and result.returncode == 0 and not hold:
            try:
                writer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                test_fail('rsync returned success without letting the FIFO writer finish')
    finally:
        if writer:
            if writer.poll() is None:
                writer.kill()
            output, error = writer.communicate(timeout=5)
    if writer and (hold or result.returncode == 0):
        if output != 'written\n' or (not hold and writer.returncode != 0):
            test_fail(f'writer did not complete the fixture: {output}{error}')
    return result


def assert_preserved(target, before, label):
    after = target.stat()
    if (target.read_bytes() != b'previous destination\n'
            or (after.st_ino, after.st_mode, after.st_mtime_ns)
            != (before.st_ino, before.st_mode, before.st_mtime_ns)
            or {p.name for p in target.parent.iterdir()} != {'out'}):
        test_fail(f'{label}: rejected data altered the destination or left a temporary file')


def check_interrupted_copy(source, target, options):
    target.write_bytes(b'previous destination\n')
    before = target.stat()
    payload = source.parent / 'payload'
    random_source = random.Random(185)
    payload.write_bytes(bytes(random_source.getrandbits(8) for _ in range(1048576)))
    with subprocess.Popen([sys.executable, '-c', WRITER, str(source), str(payload), 'hold'],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE) as writer:
        try:
            args = rsync_argv('--stream-source', '--stream-size=1MiB', '--stream-timeout=8',
                             *options, str(source), str(target))
            with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as rsync:
                try:
                    deadline = time.monotonic() + 6
                    while not any(path.stat().st_size > 0 for path in target.parent.glob('.out.*')):
                        if rsync.poll() is not None or time.monotonic() >= deadline:
                            test_fail('receiver did not stage bytes before the writer closed the FIFO')
                        time.sleep(0.02)
                    if target.read_bytes() != b'previous destination\n' or writer.poll() is not None:
                        test_fail('destination changed before EOF')
                    rsync.terminate()
                    out, err = rsync.communicate(timeout=6)
                    expect(subprocess.CompletedProcess(args, rsync.returncode, out, err), 20)
                finally:
                    if rsync.poll() is None:
                        rsync.kill()
        finally:
            if writer.poll() is None:
                writer.kill()
            writer.communicate(timeout=5)
    assert_preserved(target, before, 'interrupted copy')


def check_transfers(source, target, compressor):
    options = (f'--compress-choice={compressor}', '--debug=NSTR')
    directory = target.parent
    regular = source.parent / 'regular'
    regular.write_bytes(b'data')
    target.write_bytes(b'previous destination\n')
    result = invoke('--whole-file', *options, str(regular), str(target))
    expect(result, 0)
    if target.read_bytes() != b'data' or (compressor != 'none'
            and f'compress: {compressor} (level' not in result.stdout):
        test_fail(f'{compressor}: ordinary file did not use the selected codec: {result.stdout}')

    for size in (4, 0, 1, 32767, 32768, 32769, 262144, 262145):
        data = (b'123456789\n' * (size // 10 + 1))[:size]
        # Same length and mtime must not allow a quick-check skip.
        target.write_bytes(b'x' * size)
        now = source.stat().st_mtime_ns
        os.utime(target, ns=(now, now))
        result = transfer(source, target, size, data, options=('--whole-file', '--stats', *options))
        expect(result, 0)
        if target.read_bytes() != data or {p.name for p in directory.iterdir()} != {'out'}:
            test_fail(f'{size} byte FIFO did not produce exactly the requested file')
        if f'Total file size: {size:,} bytes' not in result.stdout:
            test_fail(f'{size} byte FIFO was reported with an incorrect size: {result.stdout}')

    # Both the reused blocks and unmatched literals cross sender window boundaries.
    random_source = random.Random(168)
    old = bytes(random_source.getrandbits(8) for _ in range(1048576))
    data = (b'new' * 100000 + old[262144:786432] + b'end' * 120000)
    delta_options = ('--no-whole-file', '--block-size=131072', '--stats', *options)
    regular.write_bytes(data)
    target.write_bytes(old)
    result = invoke(*delta_options, str(regular), str(target))
    expect(result, 0)
    if target.read_bytes() != data or 'Matched data: 524,288 bytes' not in result.stdout:
        test_fail(f'ordinary delta did not reuse the expected blocks: {result.stdout}')

    target.write_bytes(old)
    result = transfer(source, target, len(data), data, options=delta_options)
    expect(result, 0)
    if target.read_bytes() != data or 'Matched data: 524,288 bytes' not in result.stdout:
        test_fail(f'FIFO delta did not reuse the expected blocks: {result.stdout}')

    for name, size, payload, hold, code, message in (
        ('absent writer', 0, None, False, 30, 'timed out'),
        ('short', 300000, b'x' * 270000, False, 11, 'ended before'),
        ('long', 300000, b'x' * 300001, False, 11, 'exceeds'),
        ('idle after data', 300000, b'x' * 270000, True, 30, 'timed out'),
        ('idle instead of EOF', 300000, b'x' * 300000, True, 30, 'timed out'),
        ('overrun at zero', 0, b'x', False, 11, 'exceeds'),
    ):
        target.write_bytes(b'previous destination\n')
        target.chmod(0o640)
        before = target.stat()
        result = transfer(source, target, size, payload, hold=hold, options=options)
        expect(result, code, message)
        assert_preserved(target, before, name)

    check_interrupted_copy(source, target, options)


@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'transfer', 'regression'})
def test(context: TestContext):
    source = context.scratch / 'source'
    directory = context.scratch / 'destination'
    directory.mkdir()
    target = directory / 'out'
    flags = ('--stream-source', '--stream-size=0', '--stream-timeout=1')

    protocol = forced_protocol()
    if sys.platform != 'linux' or hasattr(sys, 'getandroidapilevel'):
        expect(invoke(*flags, str(source), str(target)), 1, 'not supported on this platform')
        return
    if protocol and protocol < 31:
        expect(invoke(*flags, str(source), str(target)), 1, 'requires protocol 31 or newer')
        return

    os.mkfifo(source, 0o600)
    version = invoke('-VV')
    expect(version, 0)
    compressors = ['none', *[name for name in json.loads(version.stdout).get('compress_list', [])
                            if name != 'none']]
    for compressor in compressors:
        print(f'Checking {compressor} stream transfers', flush=True)
        check_transfers(source, target, compressor)

    for option in ('--inplace', '--partial', '-P', '--partial-dir=partial', '--append-verify',
                   '--delay-updates', '--write-devices', '--backup', f'--backup-dir={context.scratch}',
                   f'--temp-dir={context.scratch}', '--remove-source-files', '--delete-missing-args',
                   '--ignore-existing', '--size-only', '--checksum', '--dry-run', '--list-only',
                   f'--copy-dest={context.scratch}', f'--write-batch={context.scratch / "batch"}',
                   '--read-batch=absent', '--copy-as=nobody', '-M--inplace', '--exclude=*',
                   '--checksum-choice=none', '--timeout=1', '--old-dirs', '--force', '--update-links'):
        expect(invoke(*flags, option, str(source), str(target)), 1, '--stream-source')

    for args in (('--stream-source',), ('--stream-size=0',), ('--stream-timeout=1',),
                 (*flags, '--stream-size=-1'), (*flags, '--stream-size=99999999999999999999'),
                 (*flags, '--stream-timeout=0'), (*flags, '--stream-timeout=-1'),
                 (*flags, '--stream-timeout=2147484')):
        expect(invoke(*args, str(source), str(target)), 1)
    expect(invoke(*flags, str(source)), 1, '--stream-source')
    expect(invoke(*flags, str(source), ''), 1, '--stream-source')
    expect(invoke(*flags, str(source), str(source), str(target)), 1, '--stream-source')
    expect(invoke(*flags, '-e', 'false', str(source), 'remote:target'), 1, '--stream-source')
    expect(invoke(*flags, '-e', 'false', 'remote:source', str(target)), 1, '--stream-source')
    expect(invoke('--server', *flags, '.', str(target)), 1, '--stream-source')
    expect(invoke('--server', '--secluded-args', *flags, '.', str(target)), 1, '--stream-source')

    regular = source.parent / 'regular'
    regular.write_bytes(b'ordinary input\n')
    expect(invoke(*flags, str(regular), str(target)), 11, 'FIFO')
    link = source.parent / 'link'
    link.symlink_to(source.name)
    expect(invoke(*flags, str(link), str(target)), 11, 'FIFO')
    if not source.is_fifo():
        test_fail('FIFO source was removed or replaced')

    target.unlink()
    os.mkfifo(target, 0o600)
    result = transfer(source, target, 0)
    if result.returncode == 0 or 'destination' not in result.stderr or not target.is_fifo():
        test_fail(f'non-regular destination was not refused and preserved: {result.stderr}')


if __name__ == '__main__':
    run(test)
