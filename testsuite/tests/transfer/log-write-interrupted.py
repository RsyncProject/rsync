#!/usr/bin/env python3

import os
from pathlib import Path
import signal
import subprocess
import time

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv


def fill_pipe(fd):
    block = bytes(os.fpathconf(fd, 'PC_PIPE_BUF'))
    size = 0
    os.set_blocking(fd, False)
    while True:
        try:
            size += os.write(fd, block)
        except BlockingIOError:
            os.set_blocking(fd, True)
            return size


def wait_for_stdout_write(process, signal_bit=0):
    proc = Path('/proc') / str(process.pid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            status = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines())
            pending = int(status['SigPnd'], 16) | int(status['ShdPnd'], 16)
            if status['State'].strip().startswith('S') and not pending & signal_bit:
                call = (proc / 'syscall').read_text().split()
                if len(call) > 1 and call[1] == '0x1':
                    return True
        except FileNotFoundError:
            pass
        time.sleep(0.01)
    test_fail('rsync did not block writing stdout')


@requires(features={'proc_fd'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'regression'})
def test(context: TestContext):
    source = context.source
    source.write_bytes(b'data\n')
    context.destination.mkdir()
    args = rsync_argv('--outbuf=B', '--out-format=%n', str(source), f'{context.destination}/')
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, 'rb') as reader, os.fdopen(write_fd, 'wb') as writer:
        filled = fill_pipe(write_fd)
        process = subprocess.Popen(args, stdout=writer, stderr=subprocess.PIPE)
        writer.close()
        try:
            if not wait_for_stdout_write(process):
                _, stderr = process.communicate(timeout=5)
                test_fail(f'rsync exited before blocking on stdout: {stderr!r}')
            # Keep the pipe full until the notification has been handled.
            process.send_signal(signal.SIGCHLD)
            if not wait_for_stdout_write(process, 1 << (signal.SIGCHLD - 1)):
                _, stderr = process.communicate(timeout=5)
                test_fail(f'SIGCHLD aborted the stdout write ({process.returncode}): {stderr!r}')
            if reader.read(filled) != bytes(filled):
                test_fail('the stdout pipe filler was changed')
            _, stderr = process.communicate(timeout=10)
            if process.returncode or stderr:
                test_fail(f'interrupted transfer failed ({process.returncode}): {stderr!r}')
            expected = os.fsencode(source.name) + b'\n'
            os.set_blocking(read_fd, False)
            if reader.read(len(expected) + 1) != expected:
                test_fail('rsync did not deliver the stdout line exactly once')
            if (context.destination / source.name).read_bytes() != source.read_bytes():
                test_fail('the transferred file did not match the source')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == '__main__':
    run(test)
