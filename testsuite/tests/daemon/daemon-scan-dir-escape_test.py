#!/usr/bin/env python3
# A daemon sender must not let a symlink-following mode (-L / --copy-dirlinks /
# --copy-unsafe-links) escape the served MODULE during the directory scan.
#
# The content open is confined beneath module_dir even in these modes
# (sender_open_copylinks_confined), but the directory ENUMERATION (opendir(fbuf)
# in flist.c send_directory) was only confined in the default no-follow mode.
# So a daemon module containing an in-module symlinked directory that points
# OUTSIDE the module, pulled with --copy-dirlinks, made the sender follow it and
# enumerate the out-of-module directory, leaking its entry names, metadata and
# (preserved) symlink targets to the client -- even though the content open
# refuses the out-of-module file data.
#
# Deterministic: a STATIC in-module symlink, no race.  --copy-dirlinks descends
# the symlinked directory (the escape vector) but preserves file symlinks under
# it, so an out-of-module symlink whose target is a unique marker proves the
# enumeration -- not the content open -- escaped the module.  FAILS before the
# daemon-follow-mode confinement; PASSES once the enumeration is confined beneath
# module_dir in following modes too (still following in-module symlinks, refusing
# module escapes).

import os

from rsyncfns import (
    SCRATCHDIR, get_rootgid, get_rootuid, get_testuid, makepath, rmtree,
    run_rsync, start_test_daemon, test_fail,
)

DAEMON_PORT = 12947
MARKER = "XFIL-OUTSIDE-MODULE-SYMLINK-TARGET"

base = SCRATCHDIR / 'daemon-scan-escape'
rmtree(base)
mod = base / 'mod'              # the served module
outside = base / 'outside'      # OUTSIDE the module (a sibling of mod/)
dest = base / 'dest'
makepath(mod / 'real', outside, dest)

# Legitimate in-module content (sanity: the transfer runs and --copy-dirlinks
# is active).
(mod / 'real' / 'in.txt').write_text("in-module\n")

# The escape vector: an in-module symlink to a DIRECTORY outside the module.
# --copy-dirlinks treats it as a directory and descends it.
os.symlink('../outside', mod / 'escape')

# Out-of-module sentinels.  xfil_link is a FILE symlink (not dereferenced by
# --copy-dirlinks), so its marker target is transferred verbatim via readlinkat
# during the scan -- a pure enumeration signal.  The dir/file names are
# secondary signals.
os.symlink(MARKER, outside / 'xfil_link')
(outside / 'xfil_dir').mkdir()
(outside / 'xfil_dir' / 'inner').write_text("x\n")
(outside / 'xfil_file').write_text("x\n")

# A use-chroot=no daemon drops to nobody under a root test run and can't read a
# root-owned module, so as root we pin it to root (as build_rsyncd_conf does);
# off-root it runs as us and must not set uid/gid, so those lines simply vanish.
root = get_testuid() == get_rootuid()
ids = f"uid = {get_rootuid()}\ngid = {get_rootgid()}" if root else ""

conf = base / 'daemon-scan.conf'
conf.write_text(f"""\
pid file = {base}/rsyncd.pid
use chroot = no
{ids}
log file = {base}/rsyncd.log

[m]
    path = {mod}
    read only = yes
    hosts allow = 127.0.0.1
""")
url = start_test_daemon(conf, DAEMON_PORT)

# --copy-dirlinks makes the daemon sender descend the symlinked 'escape' dir.
# check=False: with the fix the escape is refused, which may surface as a
# per-entry error; assert on the resulting tree either way.
run_rsync('-a', '--copy-dirlinks', f'{url}m/', f'{dest}/', check=False)

esc = dest / 'escape'
leak = None
link = esc / 'xfil_link'
if link.is_symlink() and os.readlink(link) == MARKER:
    leak = f"symlink target leaked: dest/escape/xfil_link -> {MARKER}"
else:
    for name in ('xfil_dir', 'xfil_file', 'xfil_link'):
        if (esc / name).exists() or (esc / name).is_symlink():
            leak = f"out-of-module name leaked: dest/escape/{name}"
            break

if leak is not None:
    test_fail(
        "daemon directory-enumeration escaped the module in a symlink-following "
        f"mode: the sender followed an in-module symlink out of the module and "
        f"copied an out-of-module entry to the client ({leak}).  send_directory's "
        "enumeration must stay confined beneath module_dir in following modes "
        "too, like the content open.")
print("daemon-scan-dir-escape: --copy-dirlinks enumeration stayed within the module")
