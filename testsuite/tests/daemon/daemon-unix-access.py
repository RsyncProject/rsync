#!/usr/bin/env python3

import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager

from harness import TestContext, requires, run
from harness.daemon_config import write_daemon_conf
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv, under_valgrind


@contextmanager
def daemon_connection(listener, config, *, remote_shell=False):
    timeout = 15 if under_valgrind() else 5
    args = ('--server', '--daemon', f'--config={config}', '.') if remote_shell else (
        '--daemon', '--no-detach', f'--config={config}')
    with socket.socket(listener.family, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(listener.getsockname())
        conn, _ = listener.accept()
        with conn:
            proc = subprocess.Popen(
                rsync_argv(*args),
                stdin=conn, stdout=conn, stderr=subprocess.DEVNULL)
        try:
            yield client, proc
        finally:
            client.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
                test_fail('daemon did not exit after the client disconnected')


def module_reply(listener, config, module, forwarded=False, *, remote_shell=False):
    with daemon_connection(listener, config, remote_shell=remote_shell) as (client, _), \
            client.makefile('rwb', buffering=0) as stream:
        if forwarded:
            stream.write(b'PROXY TCP4 127.0.0.1 127.0.0.1 43210 873\r\n')
        greeting = stream.readline()
        if not greeting.startswith(b'@RSYNCD: '):
            test_fail(f'{module}: unexpected daemon greeting {greeting!r}')
        stream.write(b'@RSYNCD: 31.0\n' + module.encode('ascii') + b'\n')
        return stream.readline()


def check_proxy_refused(listener, config, log, label, reason, *, remote_shell=False):
    offset = log.stat().st_size if log.exists() else 0
    with daemon_connection(listener, config, remote_shell=remote_shell) as (client, proc):
        try:
            data = client.recv(1)
        except ConnectionResetError:
            data = b''
        except socket.timeout:
            test_fail(f'{label}: waited for a PROXY header instead of refusing the peer')
        if data:
            test_fail(f'{label}: unexpected output before the peer was trusted: {data!r}')
    if proc.returncode <= 0 or not log.is_file():
        test_fail(f'{label}: daemon did not report a refusal')
    with log.open('rb') as stream:
        stream.seek(offset)
        if reason not in stream.read():
            test_fail(f'{label}: daemon did not log the expected proxy trust refusal')


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
    log = context.scratch / 'proxy-trust.log'
    proxied = {**common, 'proxy protocol': 'yes', 'log file': str(log)}
    proxy = write_daemon_conf(modules, {**proxied, 'proxy protocol hosts': '*'}, name='proxy.conf')
    unix_proxy = write_daemon_conf(modules, {**proxied, 'proxy protocol hosts': 'unix:'},
                                  name='unix-proxy.conf')
    tcp_proxy = write_daemon_conf(modules, {**proxied, 'proxy protocol hosts': '127.0.0.1'},
                                 name='tcp-proxy.conf')
    undetermined_proxy = write_daemon_conf(modules, {**proxied, 'proxy protocol hosts': 'undetermined'},
                                          name='undetermined-proxy.conf')
    untrusted = b'proxy protocol rejected from untrusted peer'

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
            check(listener, unix_proxy, 'Unix proxy', named_unix=False, forwarded=True)
            check_proxy_refused(listener, undetermined_proxy, log, 'Unix proxy without Unix trust', untrusted)

    if sys.platform.startswith('linux'):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(f'\0rsync-access-{os.getpid()}')
            listener.listen(1)
            check(listener, direct, 'abstract Unix', named_unix=True)
            for config, label in ((unix_proxy, 'abstract Unix proxy'), (proxy, 'abstract Unix wildcard')):
                check_proxy_refused(listener, config, log, label,
                                    b'proxy protocol rejected from abstract Unix socket')
            check_proxy_refused(listener, proxy, log, 'abstract Unix on the remote-shell entry point',
                                b'proxy protocol rejected from abstract Unix socket', remote_shell=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        check(listener, direct, 'TCP', named_unix=False)
        check_proxy_refused(listener, unix_proxy, log, 'Unix proxy rule on TCP', untrusted)
        reply = module_reply(listener, tcp_proxy, 'allow-ip', forwarded=True, remote_shell=True)
        if not reply.startswith(b'@RSYNCD: OK'):
            test_fail(f'TCP proxy on the remote-shell entry point: unexpected reply {reply!r}')


if __name__ == '__main__':
    run(test)
