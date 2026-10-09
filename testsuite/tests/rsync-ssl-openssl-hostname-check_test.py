#!/usr/bin/env python3
# Companion to rsync-ssl-stunnel-hostname-check for the openssl backend.  The
# openssl arm has always passed -verify_hostname $hostname (3.4.x onward), so the
# hostname identity is bound by default.  This test locks that in AND checks the
# new RSYNC_SSL_SKIP_HOSTNAME_CHECK=1 opt-out: chain verification ($caopt) and SNI
# (-servername) stay, only -verify_hostname is dropped.
#
# A fake openssl echoes its argv to a capture file via RSYNC_SSL_OPENSSL.

import os
import shlex
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, rmtree, test_fail

base = SCRATCHDIR / 'rsync-ssl-openssl-host'
rmtree(base)
base.mkdir(parents=True)

args_capture = base / 'openssl_args'
fake_openssl = base / 'fake_openssl'
fake_openssl.write_text(f'#!/bin/sh\necho "$@" > {shlex.quote(str(args_capture))}\nexit 0\n')
fake_openssl.chmod(0o755)
ca = base / 'ca.pem'
ca.write_text("dummy-ca\n")

helper = ['--HELPER', 'localhost', 'rsync', '--server', '--daemon', '.']
base_env = {**os.environ, 'RSYNC_SSL_TYPE': 'openssl',
            'RSYNC_SSL_OPENSSL': str(fake_openssl), 'RSYNC_SSL_CA_CERT': str(ca)}
base_env.pop('RSYNC_SSL_SKIP_HOSTNAME_CHECK', None)


def run(env):
    if args_capture.exists():
        args_capture.unlink()
    subprocess.run(['bash', str(SRCDIR / 'rsync-ssl')] + helper, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return args_capture.read_text() if args_capture.exists() else ''


# --- Default: the openssl client verifies the hostname.
args = run(base_env)
if '-connect localhost:' not in args:
    test_fail(f"premise: fake openssl did not capture argv:\n{args!r}")
if '-verify_hostname localhost' not in args:
    test_fail(f"openssl backend is missing -verify_hostname localhost:\n{args}")

# --- Chain-only opt-out: drop -verify_hostname, keep SNI + chain verification.
args = run({**base_env, 'RSYNC_SSL_SKIP_HOSTNAME_CHECK': '1'})
if '-verify_hostname' in args:
    test_fail("RSYNC_SSL_SKIP_HOSTNAME_CHECK=1 must drop -verify_hostname from "
              f"the openssl backend:\n{args}")
if '-servername localhost' not in args or '-CAfile' not in args:
    test_fail("SKIP_HOSTNAME_CHECK must keep SNI (-servername) and chain "
              f"verification (-CAfile):\n{args}")

print("rsync-ssl-openssl-hostname-check: openssl binds the cert to the host by "
      "default, and SKIP_HOSTNAME_CHECK opts back to chain-only")
