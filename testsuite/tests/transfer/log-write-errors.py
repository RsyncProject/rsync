#!/usr/bin/env python3

import errno
import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv


@requires(transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem'}, tags={'regression'})
def test(context: TestContext):
    source = context.scratch / 'source'
    source.write_bytes(b'rsync log write test\n')
    context.destination.mkdir()
    for stream in ('stdout', 'stderr'):
        logfile = context.scratch / f'{stream}.log'
        operand = source if stream == 'stdout' else context.scratch / 'missing'
        options = ('-v',) if stream == 'stdout' else ('--stderr=all',)
        args = rsync_argv(*options, f'--log-file={logfile}', str(operand), f'{context.destination}/')
        outputs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with source.open('rb') as read_only:
            outputs[stream] = read_only
            result = subprocess.run(args, **outputs, timeout=10)
        if result.returncode != 13:
            test_fail(f'failed {stream} write: expected status 13, got {result.returncode}')
        if stream == 'stderr' and result.stdout:
            test_fail(f'diagnostic was redirected to stdout: {result.stdout!r}')
        record = f'write to {stream} failed: {os.strerror(errno.EBADF)} ({errno.EBADF})'
        if record not in logfile.read_text():
            test_fail(f'{logfile.name} did not record the failing operation and errno')


if __name__ == '__main__':
    run(test)
