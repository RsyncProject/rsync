#!/usr/bin/env python3

import os
import re
import shlex
import subprocess

from harness.rsync import SCRATCHDIR, SRCDIR, rmtree, test_fail

base = SCRATCHDIR / 'rsync-ssl'
rmtree(base)
base.mkdir(parents=True)
wrapper = ['bash', str(SRCDIR / 'rsync-ssl')]
helper = ['--HELPER', 'localhost', 'rsync', '--server', '--daemon', '.']

def executable(path, body):
    path.write_text(body)
    path.chmod(0o755)
    return path

def invoke(script, args, env):
    return subprocess.run(['bash', str(script), *args], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

openssl_dir = base / 'openssl'
openssl_dir.mkdir()
openssl_args = openssl_dir / 'args'
fake_openssl = executable(
    openssl_dir / 'openssl',
    f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(openssl_args))}\n",
)
ca = openssl_dir / 'ca.pem'
ca.write_text('dummy-ca\n')
openssl_env = {
    **os.environ,
    'RSYNC_SSL_TYPE': 'openssl',
    'RSYNC_SSL_OPENSSL': str(fake_openssl),
    'RSYNC_SSL_CA_CERT': str(ca),
}
openssl_env.pop('RSYNC_SSL_SKIP_HOSTNAME_CHECK', None)

unfixed = executable(
    openssl_dir / 'unfixed',
    re.sub(r'(?m)^(\s*)validate_ssl_hostname .*$', r'\1:',
           (SRCDIR / 'rsync-ssl').read_text()),
)
evil_host = 'localhost INJECTED_OPENSSL_ARG'
evil_helper = ['--HELPER', evil_host, 'rsync', '--server', '--daemon', '.']
invoke(unfixed, evil_helper, openssl_env)
captured = openssl_args.read_text().splitlines() if openssl_args.exists() else []
if 'INJECTED_OPENSSL_ARG' not in captured:
    test_fail(f'unsafe control did not inject an OpenSSL argument: {captured}')
result = invoke(SRCDIR / 'rsync-ssl', evil_helper, openssl_env)
if result.returncode == 0 or 'invalid rsync-ssl hostname' not in result.stdout:
    test_fail(f'crafted hostname was not rejected:\n{result.stdout}')

def openssl_run(env):
    openssl_args.unlink(missing_ok=True)
    invoke(SRCDIR / 'rsync-ssl', helper, env)
    return openssl_args.read_text().splitlines() if openssl_args.exists() else []

captured = openssl_run(openssl_env)
if not {'-verify_hostname', 'localhost'} <= set(captured):
    test_fail(f'OpenSSL hostname verification is missing: {captured}')
captured = openssl_run({**openssl_env, 'RSYNC_SSL_SKIP_HOSTNAME_CHECK': '1'})
if '-verify_hostname' in captured:
    test_fail(f'hostname opt-out retained -verify_hostname: {captured}')
if not {'-servername', 'localhost', '-CAfile'} <= set(captured):
    test_fail(f'hostname opt-out removed SNI or chain verification: {captured}')

stunnel_dir = base / 'stunnel'
stunnel_dir.mkdir()
stunnel_config = stunnel_dir / 'config'
fake_stunnel = executable(
    stunnel_dir / 'stunnel',
    f'#!/usr/bin/env bash\ncat <&10 > {shlex.quote(str(stunnel_config))} 2>/dev/null\n',
)
stunnel_ca = stunnel_dir / 'ca.pem'
stunnel_ca.write_text('dummy-ca\n')
stunnel_env = {
    **os.environ,
    'RSYNC_SSL_TYPE': 'stunnel',
    'RSYNC_SSL_STUNNEL': str(fake_stunnel),
}
for name in ('RSYNC_SSL_CA_CERT', 'RSYNC_SSL_ALLOW_INSECURE_STUNNEL',
             'RSYNC_SSL_SKIP_HOSTNAME_CHECK'):
    stunnel_env.pop(name, None)

def stunnel_run(env):
    stunnel_config.unlink(missing_ok=True)
    result = invoke(SRCDIR / 'rsync-ssl', helper, env)
    config = stunnel_config.read_text() if stunnel_config.exists() else ''
    return result, config

_, config = stunnel_run({**stunnel_env, 'RSYNC_SSL_ALLOW_INSECURE_STUNNEL': '1'})
if 'connect = localhost' not in config:
    test_fail(f'fake stunnel did not capture a config:\n{config!r}')
if 'verifyChain' in config or 'CAfile' in config or 'checkHost' in config:
    test_fail(f'insecure stunnel config enabled verification:\n{config}')

result, _ = stunnel_run(stunnel_env)
if result.returncode == 0 or 'stunnel requires RSYNC_SSL_CA_CERT' not in result.stdout:
    test_fail(f'stunnel without a CA was not rejected:\n{result.stdout}')

_, config = stunnel_run({**stunnel_env, 'RSYNC_SSL_CA_CERT': str(stunnel_ca)})
if 'verifyChain = yes' not in config or 'checkHost = localhost' not in config:
    test_fail(f'stunnel did not verify the chain and hostname:\n{config}')

_, config = stunnel_run({
    **stunnel_env,
    'RSYNC_SSL_CA_CERT': str(stunnel_ca),
    'RSYNC_SSL_SKIP_HOSTNAME_CHECK': '1',
})
if 'verifyChain = yes' not in config or 'checkHost' in config:
    test_fail(f'stunnel hostname opt-out changed chain verification:\n{config}')

type_dir = base / 'type'
fake_bin = type_dir / 'bin'
fake_bin.mkdir(parents=True)
rsync_args = type_dir / 'args'
ssl_type = type_dir / 'type'
executable(
    fake_bin / 'rsync',
    f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(rsync_args))}\n"
    f"printf '%s\\n' \"${{RSYNC_SSL_TYPE-UNSET}}\" > {shlex.quote(str(ssl_type))}\n",
)
type_env = {**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ.get('PATH', '')}
for name in ('RSYNC_SSL_TYPE', 'RSYNC_SSL_OPENSSL', 'RSYNC_SSL_STUNNEL'):
    type_env.pop(name, None)

def type_run(args, expected):
    rsync_args.unlink(missing_ok=True)
    ssl_type.unlink(missing_ok=True)
    result = invoke(SRCDIR / 'rsync-ssl', args, type_env)
    if result.returncode:
        test_fail(f'rsync-ssl exited {result.returncode} for {args!r}:\n{result.stdout}')
    got = rsync_args.read_text().splitlines() if rsync_args.exists() else []
    actual = ssl_type.read_text().strip() if ssl_type.exists() else 'UNSET'
    if actual != expected:
        test_fail(f'RSYNC_SSL_TYPE is {actual!r}, expected {expected!r} for {args!r}')
    return got

for args, expected in (
    (['--dry-run', '--type=stunnel', 'host::mod'], 'stunnel'),
    (['--type=stunnel', '--dry-run', 'host::mod'], 'stunnel'),
    (['-av', 'host::mod', '--type=openssl'], 'openssl'),
    (['-av', 'host::mod'], 'UNSET'),
):
    captured = type_run(args, expected)
    if any(value.startswith('--type=') for value in captured):
        test_fail(f'--type was passed to rsync for {args!r}: {captured}')
    if any(value not in captured for value in args if not value.startswith('--type=')):
        test_fail(f'rsync arguments were lost for {args!r}: {captured}')

rsh = f"--rsh='{SRCDIR / 'rsync-ssl'}' --HELPER"
captured = type_run(['--', '--type=stunnel', 'host::mod'], 'UNSET')
if captured != [rsh, '--', '--type=stunnel', 'host::mod']:
    test_fail(f'arguments after -- were changed: {captured}')
captured = type_run(['--type=openssl', '--', '--type=stunnel', 'host::mod'], 'openssl')
if captured != [rsh, '--', '--type=stunnel', 'host::mod']:
    test_fail(f'arguments after -- were changed: {captured}')

print('rsync-ssl validates hosts, verifies certificates and consumes wrapper options')
