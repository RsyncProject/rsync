#!/usr/bin/env python3

import os
import shlex
import stat
import subprocess
import sys

from fixtures.rrsync import patched_rrsync, rrsync_shell, rsync_wrapper
from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv


def check_record(path, tag, name):
    lines = path.read_text().splitlines()
    if not any(tag in line and line.endswith(' ' + name) for line in lines):
        test_fail(f'{name}: missing {tag!r} record in {path.name}: {lines!r}')


@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'rrsync'})
def test(context: TestContext):
    base = context.scratch
    source, restricted = base / 'source', base / 'restricted'
    (source / 'sub').mkdir(parents=True)
    restricted.mkdir()
    (source / 'sub' / 'file').write_bytes(b'payload\n')
    log = base / 'transfer log'
    log.write_text('previous record\n')
    log.chmod(0o600)

    rsync = base / 'rsync-under-test'
    rsync_wrapper(rsync)
    wrapper = patched_rrsync(base, rsync_path=str(rsync))
    shell = base / 'remote-shell'

    def transfer(*argv, flags=('-wo', '-no-lock'), logfile=log,
                 root=restricted, pull=False, expect=None, preexec_fn=None):
        options = (*flags, '-log', str(logfile)) if logfile is not None else flags
        rrsync_shell(shell, wrapper, root, *options)
        operands = ('dummy:', f'{base / "download"}/') if pull else (f'{source}/', 'dummy:')
        result = subprocess.run(
            rsync_argv('-rI', *argv, '-e', shlex.quote(str(shell)), *operands),
            cwd=base, capture_output=True, text=True, timeout=20,
            preexec_fn=preexec_fn)
        if expect is None:
            if result.returncode:
                test_fail(f'transfer failed ({result.returncode}): {result.stderr}')
        elif not result.returncode or expect not in result.stderr:
            test_fail(f'expected {expect!r}, got {result.returncode}: {result.stderr}')
        return result

    transfer('-q', flags=('-wo',))
    if (restricted / 'sub' / 'file').read_bytes() != b'payload\n':
        test_fail('logged upload did not preserve file contents')
    check_record(log, '>f', 'sub/file')

    file_list = base / 'list'
    file_list.write_text('sub/file\n')
    transfer(f'--files-from={file_list}', flags=('-ro',), pull=True, logfile=log.name)
    if (base / 'download' / 'sub' / 'file').read_bytes() != b'payload\n':
        test_fail('logged pull with a local file list did not select the file')
    check_record(log, '<f', 'sub/file')

    obsolete = restricted / 'obsolete'
    obsolete.write_bytes(b'obsolete')
    transfer('--delete')
    if obsolete.exists():
        test_fail('logged deletion did not remove the obsolete file')
    check_record(log, '*deleting', 'obsolete')
    if not log.read_text().startswith('previous record\n'):
        test_fail('logging overwrote previous records')
    if stat.S_IMODE(log.stat().st_mode) != 0o600:
        test_fail('logging changed existing file permissions')

    if sys.platform.startswith('linux'):
        import resource
        import signal

        log_size = log.stat().st_size

        def exhaust_log():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (log_size, log_size))

        failed = transfer('-q', expect='write to log file failed', preexec_fn=exhaust_log)
        if failed.returncode != 13 or log.stat().st_size != log_size:
            test_fail('required log write failure did not stop the transfer with status 13')

    alias = base / 'log-directory'
    alias.symlink_to(base, target_is_directory=True)
    new_log = base / 'new.log'
    transfer(flags=('-wo', '-no-overwrite', '-no-lock'), logfile=alias / 'new.log')
    if not new_log.is_file():
        test_fail('logging through an administrator symlink did not create the log')
    if stat.S_IMODE(new_log.stat().st_mode) != 0o600:
        test_fail('new restricted transfer log did not use private permissions')

    empty = base / 'empty'
    empty.mkdir()
    into_root = base / 'into-root'
    into_root.symlink_to(empty, target_is_directory=True)
    out_of_root = empty / 'out-of-root'
    out_of_root.symlink_to(base, target_is_directory=True)
    rejected_paths = [empty / 'inside.log', empty / '..' / 'outside.log',
                      into_root / 'inside.log', out_of_root / 'outside.log',
                      base / 'absent' / 'log', base]
    if hasattr(os, 'mkfifo'):
        fifo = base / 'fifo'
        os.mkfifo(fifo)
        rejected_paths.append(fifo)
    for rejected in rejected_paths:
        failed = transfer(logfile=rejected, root=empty, expect='failed to open required log file')
        if str(rejected) in failed.stderr:
            test_fail('required log diagnostic disclosed the configured path')
        if any(path.name != 'out-of-root' for path in empty.iterdir()):
            test_fail(f'{rejected}: transfer or logfile started despite log setup failure')
    transfer(root='/', expect='-log cannot be used when DIR is /')
    transfer('-M--log-file=client.log', expect='option --log-file has been disabled')
    transfer('-M--rrsync-log-file=client.log', expect='invalid rsync-command syntax')
    transfer('-M--log-file=client.log', logfile=None)
    if not (restricted / 'client.log').is_file():
        test_fail('ordinary client logging no longer works')


if __name__ == '__main__':
    run(test)
