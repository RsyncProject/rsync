#!/usr/bin/env python3

import os
import platform
import shutil
import subprocess
import sys

from harness.rsync import (
    SCRATCHDIR, TODIR,
    require_tcp, rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12878

require_tcp("needs a real TCP peer address for reverse-DNS hostname ACL; "
            "run with --use-tcp")

if platform.system() != 'Linux':
    test_skipped("test is Linux-specific (uses chroot+unshare)",
                 capability='linux_nss_chroot')

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
    test_skipped("need CAP_SYS_CHROOT (root or unshare --user --map-root-user)",
                 capability='chroot')

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
    test_skipped("no reverse DNS for 127.0.0.1", capability='linux_nss_chroot')

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

write_conf('yes', 'yes')
url = start_test_daemon(conf, DAEMON_PORT)

if not run_check("Scenario A (global reverse lookup = yes)"):
    test_fail("Scenario A: hostname deny rule was bypassed")

write_conf('no', 'yes')
if not run_check("Scenario B (per-module reverse lookup only)"):
    test_fail(
        "Scenario B: hostname deny rule was bypassed (per-module reverse "
        "lookup with daemon chroot still has the bypass)"
    )
