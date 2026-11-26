#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail


@requires(transports={'pipe', 'tcp'}, mutates={'filesystem', 'process'},
          tags={'compatibility', 'tls'})
def test(context: TestContext):
    wrapper = context.repository / 'rsync-ssl'
    openssl = context.scratch / 'openssl probe'
    openssl.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    openssl.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('RSYNC_SSL_') and key != 'RSYNC_PORT'}
    ca = str(context.scratch / 'ca.pem')
    env.update(RSYNC_SSL_TYPE='openssl', RSYNC_SSL_OPENSSL=str(openssl),
               RSYNC_SSL_CA_CERT=ca, RSYNC_PORT='8874')

    def invoke(host, **updates):
        return subprocess.run(
            ['bash', str(wrapper), '--HELPER', host, 'rsync', '--server', '--daemon', '.'],
            env={**env, **updates}, capture_output=True, text=True, timeout=10,
        )

    def check(host, endpoint, identity, *, address=False, **updates):
        result = invoke(host, **updates)
        if result.returncode:
            test_fail(f'{host}: helper failed: {result.stderr}')
        args = result.stdout.splitlines()
        wanted = {'-connect': endpoint, '-CAfile': updates.get('RSYNC_SSL_CA_CERT', ca),
                  '-verify': '4'}
        if address:
            wanted['-verify_ip'] = identity
            if '-servername' in args or '-verify_hostname' in args:
                test_fail(f'{host}: treated a valid IP literal as a DNS name: {args}')
        else:
            wanted.update({'-verify_hostname': identity, '-servername': identity})
            if '-verify_ip' in args:
                test_fail(f'{host}: treated a DNS name as an IP literal: {args}')
        if '-verify_return_error' not in args:
            test_fail(f'{host}: certificate errors are not fatal: {args}')
        for flag, value in wanted.items():
            if args.count(flag) != 1 or args[args.index(flag) + 1:][:1] != [value]:
                test_fail(f'{host}: expected {flag} {value!r}: {args}')

    check('face', 'face:8874', 'face')
    check('127.0.0.1', '127.0.0.1:8874', '127.0.0.1', address=True)
    check('::1', '[::1]:8874', '::1', address=True)
    check('fe80::1%eth0', '[fe80::1%eth0]:8874', 'fe80::1', address=True)
    check('::ffff:192.0.2.1', '[::ffff:192.0.2.1]:8874', '::ffff:192.0.2.1', address=True)
    check('localhost', 'localhost:8874', 'localhost',
          RSYNC_SSL_CA_CERT=str(context.scratch / 'local CA.pem'))

    for host in ('127.0.0.256', '127.0.0', '127.00.0.1', 'fe80::1%'):
        result = invoke(host)
        if result.returncode != 1 or result.stdout or 'invalid rsync-ssl hostname' not in result.stderr:
            test_fail(f'{host}: malformed address was not rejected by the wrapper')

    result = invoke('127.0.0.1', RSYNC_SSL_SKIP_HOSTNAME_CHECK='1')
    args = result.stdout.splitlines()
    if (result.returncode or '-verify_return_error' not in args or '-CAfile' not in args
            or {'-verify_ip', '-verify_hostname', '-servername'}.intersection(args)):
        test_fail(f'Identity-check opt-out changed CA validation or IP handling: {args}')


if __name__ == '__main__':
    run(test)
