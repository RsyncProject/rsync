#!/usr/bin/env python3
# Regression test for issue #829.
#
# Without --secluded-args the client's safe_arg() backslash-escapes wildcard
# chars in option values, so --chown / --groupmap=*:GROUP is sent to a daemon
# as --groupmap=\*:GROUP.  A daemon has no shell to strip the backslash, and
# read_args() used to store option args verbatim, so the receiver saw the
# literal "\*", the wildcard never matched, and the map was ignored (the
# module's configured gid won instead).  The fix un-backslashes daemon option
# args.
#
# We run it both ways:
#   * default args    -- the '*' is safe_arg-escaped and the daemon must
#                        un-backslash it (the path the fix repairs);
#   * --secluded-args -- the '*' is sent raw over the protected channel and
#                        read with unescape=0, so it must keep working too
#                        (a guard that the fix didn't disturb that path).
#
# No root needed: a non-root receiver can chgrp(2) to a group the test user
# belongs to, so we map every source group to a second such group and check
# the wildcard took effect.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12923

# Two distinct groups to map between.  As root (the usual CI case) we can
# chgrp(2) to any gid, so take two distinct named groups from the group
# database; a non-root user can only chgrp to groups it belongs to, so use those
# (skip if it is in fewer than two).
if os.geteuid() == 0:
    import grp
    usable = []
    for gr in grp.getgrall():
        if gr.gr_gid not in usable:
            usable.append(gr.gr_gid)
    if len(usable) < 2:
        test_skipped("need >=2 groups defined on the system")
else:
    usable = []
    for g in [os.getgid()] + list(os.getgroups()):
        if g not in usable:
            usable.append(g)
    if len(usable) < 2:
        test_skipped("need >=2 groups the test user belongs to")
src_gid, dst_gid = usable[0], usable[1]

moddir = SCRATCHDIR / 'gmod'
srcdir = SCRATCHDIR / 'gsrc'
makepath(moddir)

conf = write_daemon_conf([('gmod', {'path': str(moddir), 'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT) + 'gmod/'


def check(label, *extra_opts):
    rmtree(moddir)
    rmtree(srcdir)
    makepath(moddir)
    makepath(srcdir)
    f = srcdir / 'f.dat'
    f.write_text("hi\n")
    os.chown(f, -1, src_gid)        # source group differs from the map target

    # A --chown-style wildcard map sent to a daemon: the '*' must survive as a
    # wildcard so every source group is remapped to dst_gid.
    proc = subprocess.run(
        rsync_argv('-rg', *extra_opts, f'--groupmap=*:{dst_gid}', str(f), url),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        test_fail(f"[{label}] groupmap upload failed (rc={proc.returncode})")

    got = os.stat(moddir / 'f.dat').st_gid
    if got != dst_gid:
        test_fail(f"[{label}] --groupmap='*:{dst_gid}' wildcard ignored over "
                  f"daemon: got gid {got}, expected {dst_gid} (regression of #829)")


check('default-args')
check('secluded-args', '--secluded-args')
