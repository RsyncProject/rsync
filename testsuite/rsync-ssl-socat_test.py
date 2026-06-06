#!/usr/bin/env python3
"""rsync-ssl socat transport anti-regression tests.

These tests exercise the wrapper/helper contract without requiring a live TLS
server. Fake helper binaries capture argv so the test can verify the intended
transport selection and OPENSSL address construction.
"""

import os
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, test_fail


RSYNC_SSL = SRCDIR / 'rsync-ssl'
BASH = shutil.which('bash')
HELPER_ARGV = SCRATCHDIR / 'helper.argv'
RSYNC_ARGV = SCRATCHDIR / 'rsync.argv'
OPENSSL_ARGV = SCRATCHDIR / 'openssl.argv'
FAKEBIN = SCRATCHDIR / 'fakebin'
FAKEBIN.mkdir()

if BASH is None:
    test_fail('bash is required to run rsync-ssl')


def script(path, text):
    path.write_text(text)
    path.chmod(0o755)
    return path


fake_socat = script(FAKEBIN / 'socat', f'''#!/bin/sh
: > "{HELPER_ARGV}"
for arg
do
\tprintf '%s\\n' "$arg" >> "{HELPER_ARGV}"
done
exit 0
''')

FALLBACKBIN = SCRATCHDIR / 'fallbackbin'
FALLBACKBIN.mkdir()
fallback_helper_argv = SCRATCHDIR / 'fallback-helper.argv'

script(FALLBACKBIN / 'socat', f'''#!/bin/sh
: > "{fallback_helper_argv}"
for arg
do
\tprintf '%s\\n' "$arg" >> "{fallback_helper_argv}"
done
exit 0
''')

script(FAKEBIN / 'openssl', f'''#!/bin/sh
: > "{OPENSSL_ARGV}"
for arg
do
\tprintf '%s\\n' "$arg" >> "{OPENSSL_ARGV}"
done
exit 0
''')

script(FAKEBIN / 'rsync', f'''#!/bin/sh
: > "{RSYNC_ARGV}"
printf 'RSYNC_SSL_TYPE=%s\\n' "$RSYNC_SSL_TYPE" >> "{RSYNC_ARGV}"
for arg
do
\tprintf '%s\\n' "$arg" >> "{RSYNC_ARGV}"
done
exit 0
''')


def clean_env(**updates):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('RSYNC_SSL_') or key == 'RSYNC_PORT':
            del env[key]
    env['PATH'] = f'{FAKEBIN}:{env["PATH"]}'
    for key, value in updates.items():
        env[key] = value
    return env


def fallback_env(**updates):
    env = clean_env(**updates)
    env['PATH'] = f'{FALLBACKBIN}'
    return env


def run_helper(host, **env_updates):
    HELPER_ARGV.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(RSYNC_SSL), '--HELPER', host, 'rsync', '--server', '--daemon', '.'],
        env=clean_env(RSYNC_SSL_TYPE='socat', RSYNC_SSL_SOCAT=str(fake_socat),
                      **env_updates),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        test_fail(f'rsync-ssl socat helper failed: {proc.stderr}')
    if not HELPER_ARGV.exists():
        test_fail('fake socat helper was not executed')
    return HELPER_ARGV.read_text().splitlines()


# --- --type=socat is consumed by the wrapper and passed via helper env -------
proc = subprocess.run(
    [str(RSYNC_SSL), '--type=socat', 'example.com::module'],
    env=clean_env(),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
if proc.returncode != 0:
    test_fail(f'rsync-ssl --type=socat wrapper failed: {proc.stderr}')
rsync_argv = RSYNC_ARGV.read_text().splitlines()
if rsync_argv[0] != 'RSYNC_SSL_TYPE=socat':
    test_fail('--type=socat did not set RSYNC_SSL_TYPE for the rsync wrapper')
if '--type=socat' in rsync_argv:
    test_fail('--type=socat leaked through to the real rsync argv')
if not any(arg.startswith('--rsh=') and arg.endswith(' --HELPER')
           for arg in rsync_argv):
    test_fail('rsync-ssl did not install itself as the rsync --rsh helper')


# --- socat helper uses default verification and SNI for host names -----------
argv = run_helper('example.com')
want = [
    '-',
    'OPENSSL:example.com:874,commonname=example.com,snihost=example.com,verify=1',
]
if argv != want:
    test_fail(f'unexpected socat argv for host name: {argv!r}')


# --- explicit CA/cert/key/port are preserved and IP addresses disable SNI ----
argv = run_helper(
    '127.0.0.1',
    RSYNC_PORT='8873',
    RSYNC_SSL_CA_CERT='/tmp/ca.pem',
    RSYNC_SSL_CERT='/tmp/cert.pem',
    RSYNC_SSL_KEY='/tmp/key.pem',
)
want = [
    '-',
    ('OPENSSL:127.0.0.1:8873,commonname=127.0.0.1,no-sni=1,'
     'cafile=/tmp/ca.pem,verify=1,cert=/tmp/cert.pem,key=/tmp/key.pem'),
]
if argv != want:
    test_fail(f'unexpected socat argv for IP address: {argv!r}')


# --- empty RSYNC_SSL_CA_CERT deliberately disables socat verification --------
argv = run_helper('example.net', RSYNC_SSL_CA_CERT='')
want = [
    '-',
    'OPENSSL:example.net:874,commonname=example.net,snihost=example.net,verify=0',
]
if argv != want:
    test_fail(f'unexpected socat argv for disabled verification: {argv!r}')


# --- default helper selection keeps existing openssl-first behaviour ---------
OPENSSL_ARGV.unlink(missing_ok=True)
proc = subprocess.run(
    [str(RSYNC_SSL), '--HELPER', 'example.org', 'rsync', '--server',
     '--daemon', '.'],
    env=clean_env(),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
if proc.returncode != 0:
    test_fail(f'rsync-ssl default helper failed: {proc.stderr}')
if not OPENSSL_ARGV.exists():
    test_fail('default rsync-ssl helper selection did not execute openssl')
openssl_argv = OPENSSL_ARGV.read_text().splitlines()
if not openssl_argv or openssl_argv[0] != 's_client':
    test_fail(f'default helper selection did not use openssl s_client: {openssl_argv!r}')


# --- if openssl is unavailable, default selection prefers socat over stunnel -
fallback_helper_argv.unlink(missing_ok=True)
proc = subprocess.run(
    [BASH, str(RSYNC_SSL), '--HELPER', 'fallback.example', 'rsync',
     '--server', '--daemon', '.'],
    env=fallback_env(),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
if proc.returncode != 0:
    test_fail(f'rsync-ssl fallback helper failed: {proc.stderr}')
if not fallback_helper_argv.exists():
    test_fail('default rsync-ssl fallback selection did not execute socat')
fallback_argv = fallback_helper_argv.read_text().splitlines()
want = [
    '-',
    ('OPENSSL:fallback.example:874,commonname=fallback.example,'
     'snihost=fallback.example,verify=1'),
]
if fallback_argv != want:
    test_fail(f'unexpected socat argv for fallback selection: {fallback_argv!r}')

print('rsync-ssl-socat: wrapper and helper transport behaviour verified')
