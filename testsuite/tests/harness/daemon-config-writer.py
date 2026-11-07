#!/usr/bin/env python3

from unittest.mock import patch

from harness import TestContext, requires, run, test_fail
from harness.daemon_config import build_rsyncd_conf, write_daemon_conf

@requires(transports={'pipe'}, mutates={'filesystem'}, tags={'harness'})
def test(context: TestContext):
    with patch('harness.daemon_config.os.getuid', return_value=1000):
        path = write_daemon_conf(
            [('module', {'path': str(context.source), 'read only': 'yes'})],
            global_options={'uid': '123', 'gid': '456'},
            name='nonroot.conf',
        )
    text = path.read_text()
    if 'uid =' in text or 'gid =' in text:
        test_fail('non-root configuration retained privileged identities')
    if f'path = {context.source}' not in text or 'read only = yes' not in text:
        test_fail('module options were not written')

    with patch('harness.daemon_config.os.getuid', return_value=0):
        path = write_daemon_conf(
            [('module', {'path': str(context.destination)})],
            global_options={'pid file': str(context.scratch / 'custom.pid')},
            name='root.conf',
        )
    text = path.read_text()
    if 'uid = 0' not in text or 'gid = 0' not in text:
        test_fail('root configuration omitted daemon identities')
    if f'pid file = {context.scratch / "custom.pid"}' not in text:
        test_fail('global override was not written')

    text = build_rsyncd_conf().read_text()
    for section in ('test-from', 'test-to', 'test-scratch', 'test-hidden'):
        if f'[{section}]' not in text:
            test_fail(f'standard configuration omitted {section}')

if __name__ == '__main__':
    run(test)
