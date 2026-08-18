#!/usr/bin/env python3
# Python rewrite of testsuite/daemon-chroot-acl.test.
#
# Regression test for GHSA-rjfm-3w2m-jf4f: a hostname-based "hosts deny"
# rule must still match when the daemon performs a 'daemon chroot' and
# the chroot does not contain the NSS files glibc needs for reverse
# DNS. Pre-fix, reverse DNS happened *after* the chroot, the lookup
# failed, client_name() returned "UNKNOWN", and a deny rule referring
# to the connecting hostname silently failed to match.

import os
import platform
import shutil
import subprocess
import sys

from rsyncfns import (
    SCRATCHDIR, TODIR,
    require_tcp, rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
)


DAEMON_PORT = 12878

# This test fundamentally needs a real TCP peer address: the daemon reverse-
# resolves the connecting IP for a hostname-based "hosts deny" ACL check.
# The stdio-pipe transport has no peer IP, so only run under --use-tcp.
require_tcp("needs a real TCP peer address for reverse-DNS hostname ACL; "
            "run with --use-tcp")

if platform.system() != 'Linux':
    test_skipped("test is Linux-specific (uses chroot+unshare)")

# Need CAP_SYS_CHROOT. Re-exec under a user namespace if not root.
def _can_chroot() -> bool:
    proc = subprocess.run(['chroot', '/', '/bin/true'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


if not _can_chroot():
    if not os.environ.get('RSYNC_UNSHARED'):
        unshare = shutil.which('unshare')
        if unshare is not None:
            probe = subprocess.run(
                [unshare, '--user', '--map-root-user', 'true'],
                capture_output=True,
            )
            if probe.returncode == 0:
                print("Re-running under unshare --user --map-root-user...")
                env = os.environ.copy()
                env['RSYNC_UNSHARED'] = '1'
                os.execvpe(
                    unshare,
                    [unshare, '--user', '--map-root-user',
                     sys.executable, __file__],
                    env,
                )
    test_skipped("need CAP_SYS_CHROOT (root or unshare --user --map-root-user)")


# Find what 127.0.0.1 reverse-resolves to.
def _client_hostname() -> str:
    try:
        out = subprocess.check_output(['getent', 'hosts', '127.0.0.1'], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ''
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            return parts[1]
    return ''


client_hostname = _client_hostname()
if not client_hostname or client_hostname == '127.0.0.1':
    test_skipped("no reverse DNS for 127.0.0.1")

chrootdir = SCRATCHDIR / 'chroot'
rmtree(chrootdir)
(chrootdir / 'modroot').mkdir(parents=True)
(chrootdir / 'modroot' / 'file1').write_text("from chroot\n")

conf = SCRATCHDIR / 'test-rsyncd.conf'
logfile = SCRATCHDIR / 'rsyncd.log'


def write_conf(global_rev: str, module_rev: str) -> None:
    conf.write_text(f"""\
use chroot = no
log file = {logfile}
daemon chroot = {chrootdir}
reverse lookup = {global_rev}
hosts deny = {client_hostname}
max verbosity = 4

[chrootmod]
    path = /modroot
    read only = yes
    reverse lookup = {module_rev}
""")


def run_check(label: str) -> bool:
    if logfile.exists():
        logfile.unlink()
    rmtree(TODIR)
    TODIR.mkdir()

    # rsyncd re-reads its config file on each accepted connection, so
    # rewriting `conf` between scenarios is enough -- we keep the one
    # daemon for both.
    proc = subprocess.run(
        rsync_argv('-av', f'{url}chrootmod/', f'{TODIR}/'),
        capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr

    print(f"----- {label} (rsync exit {proc.returncode}):")
    print(out)
    print("----- daemon log:")
    if logfile.exists():
        print(logfile.read_text())
    print("-----")

    return '@ERROR' in out and 'access denied' in out


# Spin up the daemon once; we'll rewrite `conf` between scenarios and rely
# on rsyncd's per-connection re-read of the config file.
write_conf('yes', 'yes')
url = start_test_daemon(conf, DAEMON_PORT)

# Scenario A: global reverse lookup. Covered by b6abdb4c.
if not run_check("Scenario A (global reverse lookup = yes)"):
    test_fail("Scenario A: hostname deny rule was bypassed")

# Scenario B: only per-module reverse-lookup enabled.
write_conf('no', 'yes')
if not run_check("Scenario B (per-module reverse lookup only)"):
    test_fail(
        "Scenario B: hostname deny rule was bypassed (per-module reverse "
        "lookup with daemon chroot still has the bypass)"
    )
