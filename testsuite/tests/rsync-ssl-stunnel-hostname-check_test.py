#!/usr/bin/env python3
# Finding (codex scan, CWE-295 improper certificate validation): in stunnel mode
# rsync-ssl emitted "verifyChain = yes" + "CAfile" but NO "checkHost" directive,
# so the TLS session validated the certificate CHAIN but not its HOSTNAME.  An
# active network attacker holding a cert that chains to the configured CA but is
# issued for a DIFFERENT name could MITM the rsync-over-SSL connection.  Fix:
# emit "checkHost = $hostname" whenever the chain is verified, mirroring the
# openssl backend's -verify_hostname.  RSYNC_SSL_SKIP_HOSTNAME_CHECK=1 keeps chain
# verification but skips the identity check (restores 3.4.x stunnel behaviour).
#
# rsync-ssl feeds the generated stunnel config on fd 10; a fake stunnel captures
# it and we inspect the directives.

import os
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, rmtree, test_fail

base = SCRATCHDIR / 'rsync-ssl-stunnel-host'
rmtree(base)
base.mkdir(parents=True)

config_capture = base / 'stunnel_config'
fake_stunnel = base / 'fake_stunnel'
# bash, not /bin/sh: dash cannot redirect from fd >9; via env for the BSDs' bash.
fake_stunnel.write_text(f"#!/usr/bin/env bash\ncat <&10 > {config_capture} 2>/dev/null\nexit 0\n")
fake_stunnel.chmod(0o755)
ca = base / 'ca.pem'
ca.write_text("dummy-ca\n")

helper = ['--HELPER', 'localhost', 'rsync', '--server', '--daemon', '.']
base_env = {**os.environ, 'RSYNC_SSL_TYPE': 'stunnel',
            'RSYNC_SSL_STUNNEL': str(fake_stunnel)}
for v in ('RSYNC_SSL_CA_CERT', 'RSYNC_SSL_ALLOW_INSECURE_STUNNEL',
          'RSYNC_SSL_SKIP_HOSTNAME_CHECK'):
    base_env.pop(v, None)


def run(env):
    if config_capture.exists():
        config_capture.unlink()
    subprocess.run(['bash', str(SRCDIR / 'rsync-ssl')] + helper, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return config_capture.read_text() if config_capture.exists() else ''


# --- Part 1: CA configured, default -> chain AND hostname are verified.
cfg = run({**base_env, 'RSYNC_SSL_CA_CERT': str(ca)})
if 'connect = localhost' not in cfg:
    test_fail(f"premise: fake stunnel did not capture a config:\n{cfg!r}")
if 'verifyChain = yes' not in cfg:
    test_fail(f"a CA-configured stunnel config must verify the chain:\n{cfg}")
if 'checkHost = localhost' not in cfg:
    test_fail("CA-configured stunnel config is missing the hostname binding "
              f"(checkHost = localhost):\n{cfg}")

# --- Part 2: explicit insecure opt-out, no CA -> no verification at all.
cfg = run({**base_env, 'RSYNC_SSL_CA_CERT': '',
           'RSYNC_SSL_ALLOW_INSECURE_STUNNEL': '1'})
if 'checkHost' in cfg:
    test_fail(f"insecure (no-CA) stunnel config must not emit checkHost:\n{cfg}")

# --- Part 3: chain-only opt-out -> chain verified, hostname check skipped.
cfg = run({**base_env, 'RSYNC_SSL_CA_CERT': str(ca),
           'RSYNC_SSL_SKIP_HOSTNAME_CHECK': '1'})
if 'verifyChain = yes' not in cfg:
    test_fail(f"SKIP_HOSTNAME_CHECK must keep chain verification:\n{cfg}")
if 'checkHost' in cfg:
    test_fail("RSYNC_SSL_SKIP_HOSTNAME_CHECK=1 must drop the hostname binding "
              f"(no checkHost):\n{cfg}")

print("rsync-ssl-stunnel-hostname-check: stunnel binds the cert to the host when "
      "verifying a chain, and SKIP_HOSTNAME_CHECK opts back to chain-only")
