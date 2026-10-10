#!/usr/bin/env python3
"""Creation-time preservation"""

import datetime
import os

import rsyncfns
from harness import TestContext, requires, run, unsupported
from rsyncfns import checkit, run_rsync


@requires(features={'crtimes'}, protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe'}, mutates={'filesystem'}, tags={'metadata'})
def test(context: TestContext):
    if '"crtimes": true' not in run_rsync('-VV', capture_output=True).stdout:
        unsupported('crtimes')

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
    rsyncfns.TLS_ARGS = '--crtimes'
    checkit(['-rtgvvv', '--crtimes', f'{context.source}/', f'{context.destination}/'], context.source, context.destination)


if __name__ == '__main__':
    run(test)
