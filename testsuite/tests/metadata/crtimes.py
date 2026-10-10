#!/usr/bin/env python3

import datetime
import os

from harness import rsync
from harness import TestContext, requires, run
from harness.rsync import checkit, run_rsync, test_skipped

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
    rsync.TLS_ARGS = '--crtimes'
    checkit(['-rtgvvv', '--crtimes', f'{context.source}/', f'{context.destination}/'], context.source, context.destination)

if __name__ == '__main__':
    run(test)
