#!/usr/bin/env python3

import sys

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.results import test_fail
from harness.rsync import run_rsync


@requires(features={'daemon', 'tcp'}, transports={'tcp'}, min_peer='4.0.0',
          mutates={'environment', 'filesystem', 'process', 'socket'}, tags={'daemon'})
def test(context: TestContext):
    module = context.scratch / 'module'
    module.mkdir()
    (module / 'file').write_bytes(b'normal transfer\n')
    config = write_daemon_conf([('files', {'path': module, 'read only': 'yes'})])
    url = start_test_daemon(config, 12996)

    def transfer(label, options):
        target = context.scratch / label
        result = run_rsync('-r', '--ipv4', f'--sockopts={options}', f'{url}files/', f'{target}/',
                           capture_output=True, check=False)
        payload = target / 'file'
        if result.returncode or not payload.is_file() or payload.read_bytes() != b'normal transfer\n':
            test_fail(f'{label}: socket options prevented the transfer: {result.stderr}')
        return result.stderr

    stderr = transfer('legacy', 'SO_KEEPALIVE,SO_KEEPALIVE=0, SO_SNDBUF=8192')
    if stderr:
        test_fail(f'legacy integer options produced diagnostics: {stderr}')

    stderr = transfer('invalid', 'SO_KEEPALIVE=word,SO_KEEPALIVE=2147483648,NOT_A_SOCKET_OPTION')
    if stderr.count('Invalid integer value for socket option SO_KEEPALIVE') != 2:
        test_fail(f'malformed integer values were not diagnosed: {stderr}')
    if 'Unknown socket option NOT_A_SOCKET_OPTION' not in stderr:
        test_fail(f'unknown name was not diagnosed: {stderr}')

    stderr = transfer('congestion', 'TCP_CONGESTION=reno')
    if not sys.platform.startswith('linux'):
        if 'Unavailable socket option TCP_CONGESTION in this build' not in stderr:
            test_fail(f'known unavailable option was not diagnosed: {stderr}')
        return
    if 'Unknown socket option' in stderr or 'Unavailable socket option' in stderr:
        test_fail(f'Linux TCP_CONGESTION was not recognised: {stderr}')
    stderr = transfer('empty-congestion', 'TCP_CONGESTION=')
    if 'Socket option TCP_CONGESTION requires a non-empty value' not in stderr:
        test_fail(f'empty congestion name was not rejected: {stderr}')
    stderr = transfer('long-congestion', 'TCP_CONGESTION=one-two-three-xx')
    if 'Value for socket option TCP_CONGESTION is too long' not in stderr:
        test_fail(f'oversized congestion name was not rejected: {stderr}')
    stderr = transfer('refused-congestion', 'TCP_CONGESTION=no_such_cc')
    if 'failed to set socket option TCP_CONGESTION' not in stderr:
        test_fail(f'kernel refusal was not diagnosed: {stderr}')


if __name__ == '__main__':
    run(test)
