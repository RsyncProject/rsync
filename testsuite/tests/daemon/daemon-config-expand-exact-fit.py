#!/usr/bin/env python3

import os

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.results import test_fail
from harness.rsync import run_rsync


DAEMON_PORT = 12948
EXPANSION_SLACK = 2048  # Extra space reserved by expand_vars().


@requires(features={'daemon'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'environment', 'filesystem', 'process'}, tags={'configuration', 'regression'})
def test(context: TestContext):
    context.source.mkdir()
    modules = []
    expected = {}
    for name, spare in (('near-fit', 1), ('exact-fit', 0)):
        key = f'RSYNC_TEST_COMMENT_{spare}'
        template = f'%{key}%'
        comment = 'x' * (len(template) + EXPANSION_SLACK - spare - 1) + '!'
        os.environ[key] = expected[name] = comment
        modules.append((name, {'path': str(context.source), 'comment': template}))

    config = write_daemon_conf(modules)
    listing = run_rsync(start_test_daemon(config, DAEMON_PORT), capture_output=True).stdout
    comments = {}
    for line in listing.splitlines():
        name, separator, comment = line.partition('\t')
        if separator and name.strip() in expected:
            comments[name.strip()] = comment
    for name, comment in expected.items():
        actual = comments.get(name, '')
        if actual != comment:
            test_fail(f'{name}: expected {len(comment)} bytes of comment, got {len(actual)}')


if __name__ == '__main__':
    run(test)
