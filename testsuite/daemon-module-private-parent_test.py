#!/usr/bin/env python3
# A daemon must serve a module whose path lies under a directory the SERVED
# (dropped-privilege) uid cannot traverse.
#
# The daemon chdir's into the module root while still root, then drops to the
# module uid; both the directory scan AND the file-content open must resolve
# relative to that pinned root, NOT re-walk the absolute module path -- which
# re-traverses the module's ancestors as the unprivileged uid and EACCESes.  The
# common real-world shape is a module under a 0700 home (path = /home/backup/data);
# here we plant an explicit 0700 parent so the test is deterministic and
# independent of where the suite is built.
#
# RED before the privilege-drop-safe anchoring (the daemon's directory scan, or
# send_files' content open, fails with "Permission denied"); GREEN after.
# Requires root (a 0700 parent the served uid cannot traverse, and a daemon that
# drops to an unprivileged uid).

import os
import pwd
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12951
CONTENT = "served-from-under-a-private-parent\n"


if os.geteuid() != 0:
    test_skipped("requires root (a 0700 parent the served uid cannot traverse, "
                 "and a daemon that drops to an unprivileged uid)")

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
    test_skipped("no unprivileged uid available to drop the daemon to")

base = SCRATCHDIR / 'priv-parent'
rmtree(base)
# A 0700 root-owned parent the served uid cannot search.  The module lives under
# it; its own contents are world-readable so serving works once the daemon is
# inside (it chdir'd in while still root).
private = base / 'private'
modpath = private / 'mod'
sub = modpath / 'sub'
makepath(sub)
(sub / 'file').write_text(CONTENT)
os.chmod(modpath, 0o755)
os.chmod(sub, 0o755)
os.chmod(sub / 'file', 0o644)
os.chmod(private, 0o700)          # <-- the barrier: unsearchable to UNPRIV

dest = base / 'dest'
makepath(dest)

# Numeric uid/gid so we don't depend on a group named like the user (e.g.
# "nobody" is a user but the group is "nogroup" on Debian/Ubuntu).
conf = write_daemon_conf([
    ('m', {'path': modpath, 'read only': 'yes',
           'uid': str(UNPRIV.pw_uid), 'gid': str(UNPRIV.pw_gid)}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def pull(*opts):
    """Pull the module to a fresh dest with the given options; assert it serves
    sub/file (the content open under the 0700 parent must not EACCES)."""
    rmtree(dest)
    makepath(dest)
    proc = subprocess.run(
        rsync_argv(*opts, f'{url}m/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        test_fail(
            f"daemon failed to serve a module under a 0700 parent as uid "
            f"{UNPRIV.pw_name} ({' '.join(opts)}): {proc.stderr.strip()!r}.  The "
            "scan and the content open must resolve relative to the module root "
            "the daemon pinned while privileged, not re-walk the absolute module "
            "path (which EACCESes on the 0700 ancestor under the dropped uid).")
    got = dest / 'sub' / 'file'
    if not got.is_file() or got.read_text() != CONTENT:
        test_fail(f"the under-0700-parent module did not serve its file with "
                  f"'{' '.join(opts)}' (dest/sub/file missing/wrong)")


# Default mode: content open via sender_open_confined() -> held_dir_path_fd()
# (the dpc base open).
pull('-r')
# Symlink-following mode: content open via sender_open_copylinks_confined() ->
# secure_relative_open(module_dir, ...) -- the other re-walk site (covers the
# absolute-basedir branch of the resolver).
pull('-rL')

print("daemon-module-private-parent: served a module under a 0700 parent as "
      f"uid {UNPRIV.pw_name} (default + --copy-links content opens)")
