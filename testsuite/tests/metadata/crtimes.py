#!/usr/bin/env python3

import datetime
import os

from harness import environment
from harness import TestContext, requires, run
from harness.comparison import checkit
from harness.results import test_skipped
from harness.rsync import run_rsync

@requires(features={'crtimes'}, transports={'pipe'}, mutates={'filesystem'}, tags={'metadata'})
def test(context: TestContext):
    if '"crtimes": true' not in run_rsync('-VV', capture_output=True).stdout:
        test_skipped('crtimes is unavailable', capability='crtimes')

    context.source.mkdir(parents=True)
    source = context.source / 'foo'
    source.write_text('hiho\n')

    def set_time(path, value):
        timestamp = value.timestamp()
        os.utime(path, (timestamp, timestamp))

    set_time(context.source, datetime.datetime(2001, 1, 1, 11, 11, 11))
    set_time(context.source, datetime.datetime(2002, 2, 2, 22, 22, 22))
    set_time(source, datetime.datetime(2001, 11, 11, 11, 11, 11))
    set_time(source, datetime.datetime(2002, 12, 12, 22, 22, 22))
    environment.TLS_ARGS = '--crtimes'
    checkit(['-rtgvvv', '--crtimes', f'{context.source}/', f'{context.destination}/'], context.source, context.destination)

if __name__ == '__main__':
    run(test)
