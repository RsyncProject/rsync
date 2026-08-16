#!/usr/bin/env python3
# Finding [12] (CWE-295 improper certificate validation): in stunnel mode
# rsync-ssl builds the stunnel client config with NO CA / verify directives when
# RSYNC_SSL_CA_CERT is unset (rsync-ssl:88-99 leaves verify=""), so the TLS
# session validates no server certificate.  An active network attacker can then
# MITM the rsync-over-SSL connection -- reading and modifying transferred file
# data and the daemon auth challenge/response.  Fix: rsync-ssl refuses stunnel
# mode without a CA unless RSYNC_SSL_ALLOW_INSECURE_STUNNEL=1 (rsync-ssl:140).
#
# Part 1 demonstrates the danger: with the explicit insecure opt-out and no CA, a
# fake stunnel captures the generated config and it carries no certificate
# verification at all -- the MITM-able config the old default produced.  Part 2 is
# the fix: without the opt-out and without a CA, rsync-ssl refuses to run.

import os
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, rmtree, test_fail

base = SCRATCHDIR / 'rsync-ssl-stunnel'
rmtree(base)
base.mkdir(parents=True)

config_capture = base / 'stunnel_config'
fake_stunnel = base / 'fake_stunnel'
# rsync-ssl feeds the generated stunnel config on fd 10 (exec ... -fd 10 ...);
# bash, not /bin/sh, since dash cannot redirect from fd >9 -- and via env, since
# the BSDs keep bash in /usr/local/bin, not /bin.
fake_stunnel.write_text(f"#!/usr/bin/env bash\ncat <&10 > {config_capture} 2>/dev/null\nexit 0\n")
fake_stunnel.chmod(0o755)

helper = ['--HELPER', 'localhost', 'rsync', '--server', '--daemon', '.']
env = {**os.environ, 'RSYNC_SSL_TYPE': 'stunnel', 'RSYNC_SSL_STUNNEL': str(fake_stunnel)}
env.pop('RSYNC_SSL_CA_CERT', None)

# --- Part 1: with the explicit insecure opt-out and no CA, the generated config
# has no certificate verification -- the MITM-able connection.
env1 = {**env, 'RSYNC_SSL_ALLOW_INSECURE_STUNNEL': '1'}
subprocess.run(['bash', str(SRCDIR / 'rsync-ssl')] + helper, env=env1,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
cfg = config_capture.read_text() if config_capture.exists() else ''
if 'connect = localhost' not in cfg:
    test_fail(f"premise: fake stunnel did not capture a config:\n{cfg!r}")
if 'verifyChain' in cfg or 'CAfile' in cfg:
    test_fail("premise: expected NO certificate-verification directives in the "
              f"no-CA stunnel config:\n{cfg}")

# --- Part 2: without the opt-out and without a CA, rsync-ssl refuses to run, so
# the unverified connection shown in part 1 cannot happen by accident.
proc = subprocess.run(['bash', str(SRCDIR / 'rsync-ssl')] + helper, env=env,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if proc.returncode == 0 or 'stunnel requires RSYNC_SSL_CA_CERT' not in proc.stdout:
    test_fail("rsync-ssl did NOT refuse insecure stunnel mode without a CA:\n" + proc.stdout)

print("rsync-ssl-stunnel-ca-required: stunnel without a CA yields an unverified "
      "(MITM-able) config; rsync-ssl refuses it unless explicitly opted out")
