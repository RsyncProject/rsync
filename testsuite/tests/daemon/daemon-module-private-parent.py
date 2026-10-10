#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12951
CONTENT = "served-from-under-a-private-parent\n"

if os.geteuid() != 0:
    test_skipped("requires root (a 0700 parent the served uid cannot traverse, "
                 "and a daemon that drops to an unprivileged uid)", capability='root')

UNPRIV = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name)
        if u.pw_uid != 0 and u.pw_uid != os.geteuid():
            UNPRIV = u
            break
    except KeyError:
        continue
if UNPRIV is None:
    test_skipped("no unprivileged uid available to drop the daemon to", capability='root')

base = SCRATCHDIR / 'priv-parent'
rmtree(base)
private = base / 'private'
modpath = private / 'mod'
sub = modpath / 'sub'
makepath(sub)
(sub / 'file').write_text(CONTENT)
os.chmod(modpath, 0o755)
os.chmod(sub, 0o755)
os.chmod(sub / 'file', 0o644)
os.chmod(private, 0o700)

dest = base / 'dest'
makepath(dest)

conf = write_daemon_conf([
    ('m', {'path': modpath, 'read only': 'yes',
           'uid': str(UNPRIV.pw_uid), 'gid': str(UNPRIV.pw_gid)}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def pull(*opts):
    rmtree(dest)
    makepath(dest)
    proc = subprocess.run(
        rsync_argv(*opts, f'{url}m/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        test_fail(f"module under 0700 parent failed as {UNPRIV.pw_name} "
                  f"({' '.join(opts)}): {proc.stderr.strip()!r}")
    got = dest / 'sub' / 'file'
    if not got.is_file() or got.read_text() != CONTENT:
        test_fail(f"the under-0700-parent module did not serve its file with "
                  f"'{' '.join(opts)}' (dest/sub/file missing/wrong)")

pull('-r')
pull('-rL')

print("daemon-module-private-parent: served a module under a 0700 parent as "
      f"uid {UNPRIV.pw_name} (default + --copy-links content opens)")
