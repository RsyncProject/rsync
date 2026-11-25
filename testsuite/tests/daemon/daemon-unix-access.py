#!/usr/bin/env python3

import os
import socket
import subprocess
import sys
import tempfile

from harness import TestContext, requires, run
from harness.daemon_config import write_daemon_conf
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, under_valgrind


def module_reply(listener, config, module, forwarded=False):
    timeout = 15 if under_valgrind() else 5
    with socket.socket(listener.family, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(listener.getsockname())
        conn, _ = listener.accept()
        with conn:
            proc = subprocess.Popen(
                rsync_argv('--daemon', '--no-detach', f'--config={config}'),
                stdin=conn, stdout=conn, stderr=subprocess.DEVNULL)
        try:
            with client.makefile('rwb', buffering=0) as stream:
                if forwarded:
                    stream.write(b'PROXY TCP4 127.0.0.1 127.0.0.1 43210 873\r\n')
                greeting = stream.readline()
                if module is None:
                    return greeting
                if not greeting.startswith(b'@RSYNCD: '):
                    test_fail(f'{module}: unexpected daemon greeting {greeting!r}')
                stream.write(b'@RSYNCD: 31.0\n' + module.encode('ascii') + b'\n')
                return stream.readline()
        finally:
            client.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
                test_fail('daemon did not exit after the client disconnected')


@requires(features={'unix_listener'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'regression'})
def test(context: TestContext):
    if not hasattr(socket, 'AF_UNIX') or sys.platform == 'cygwin':
        test_skipped('requires a named Unix socket on daemon stdin', capability='unix_listener')

    context.source.mkdir()
    modules = [
        ('allow-unix', {'hosts allow': 'UNIX:'}),
        ('deny-unix', {'hosts deny': 'unix:'}),
        ('allow-ip', {'hosts allow': '127.0.0.1'}),
        ('allow-wins', {'hosts allow': 'unix:', 'hosts deny': '*'}),
        ('require-auth', {'hosts allow': 'unix:', 'auth users': 'fixture'}),
    ]
    modules = [(name, {'path': str(context.source), **options}) for name, options in modules]
    common = {'hosts allow': '', 'reverse lookup': 'no'}
    direct = write_daemon_conf(modules, common)
    proxy = write_daemon_conf(modules, {**common, 'proxy protocol': 'yes',
                                       'proxy protocol hosts': '*'}, name='proxy.conf')
    untrusted = write_daemon_conf(modules, {**common, 'proxy protocol': 'yes',
                                           'proxy protocol hosts': 'unix:'}, name='untrusted.conf')

    def check(listener, config, label, named_unix, forwarded=False):
        allowed = b'@RSYNCD: OK'
        denied = b'@ERROR: access denied'
        expected = {
            'allow-unix': allowed if named_unix else denied,
            'deny-unix': denied if named_unix else allowed,
            'allow-ip': denied if named_unix else allowed,
            'allow-wins': allowed if named_unix else denied,
            'require-auth': b'@RSYNCD: AUTHREQD ' if named_unix else denied,
        }
        for module, prefix in expected.items():
            reply = module_reply(listener, config, module, forwarded)
            if not reply.startswith(prefix):
                test_fail(f'{label}/{module}: expected {prefix!r}, got {reply!r}')

    with tempfile.TemporaryDirectory(prefix='rsync-unix-access-') as tmpdir:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(f'{tmpdir}/peer.sock')
            listener.listen(1)
            check(listener, direct, 'filesystem Unix', named_unix=True)
            check(listener, proxy, 'forwarded IP', named_unix=False, forwarded=True)
            if module_reply(listener, untrusted, None):
                test_fail('unix: granted trust to a proxy peer')

    if sys.platform.startswith('linux'):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(f'\0rsync-access-{os.getpid()}')
            listener.listen(1)
            check(listener, direct, 'abstract Unix', named_unix=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        check(listener, direct, 'TCP', named_unix=False)


if __name__ == '__main__':
    run(test)
