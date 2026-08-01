#!/usr/bin/env python3
# Finding [20] (CWE-88 argument injection): rsync-ssl expands an untrusted SSL
# hostspec into the openssl/gnutls command line unquoted (rsync-ssl:146) and into
# the stunnel heredoc config (rsync-ssl:154) with no character policy.  A hostname
# containing whitespace / newline / control characters therefore injects extra
# TLS options or stunnel directives -- a forced connection target, a verification
# bypass, or stunnel config injection.  Fix: validate_ssl_hostname rejects such
# hostnames before any helper is invoked (rsync-ssl:138).
#
# Part 1 demonstrates the injection against an UNFIXED copy of rsync-ssl plus a
# fake openssl that records its argv: the crafted host's payload lands as a
# separate openssl argument.  Part 2 is the fix: the shipped rsync-ssl rejects the
# host string before exec'ing the helper.

import os
import shlex
import re
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, rmtree, test_fail

base = SCRATCHDIR / 'rsync-ssl-hostname'
rmtree(base)
base.mkdir(parents=True)

argv_capture = base / 'openssl_argv'
fake_openssl = base / 'fake_openssl'
fake_openssl.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(argv_capture))}\nexit 0\n")
fake_openssl.chmod(0o755)

# An unfixed rsync-ssl: drop the validate_ssl_hostname guard so the host string
# reaches the helper command line as it did before the fix.
unfixed = base / 'rsync-ssl-unfixed'
unfixed.write_text(re.sub(r'(?m)^(\s*)validate_ssl_hostname .*$',
                          r'\1: # hostname guard removed for this regression test',
                          (SRCDIR / 'rsync-ssl').read_text()))
unfixed.chmod(0o755)

EVIL_HOST = 'localhost INJECTED_OPENSSL_ARG'
env = {**os.environ, 'RSYNC_SSL_TYPE': 'openssl',
       'RSYNC_SSL_OPENSSL': str(fake_openssl), 'RSYNC_SSL_CA_CERT': '/dev/null'}
helper = ['--HELPER', EVIL_HOST, 'rsync', '--server', '--daemon', '.']

# --- Part 1: the unfixed helper injects the payload into the openssl argv.
subprocess.run(['bash', str(unfixed)] + helper, env=env,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
captured = argv_capture.read_text().splitlines() if argv_capture.exists() else []
if 'INJECTED_OPENSSL_ARG' not in captured:
    test_fail("premise: the crafted hostname did not inject a separate openssl "
              f"argument (captured argv: {captured})")

# --- Part 2: the shipped rsync-ssl rejects the injecting host string.
proc = subprocess.run(['bash', str(SRCDIR / 'rsync-ssl')] + helper, env=env,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if proc.returncode == 0 or 'invalid rsync-ssl hostname' not in proc.stdout:
    test_fail("rsync-ssl did NOT reject the argument-injecting hostname:\n" + proc.stdout)

print("rsync-ssl-hostname-validation: a control-char hostspec injects helper "
      "arguments; rsync-ssl rejects it")
