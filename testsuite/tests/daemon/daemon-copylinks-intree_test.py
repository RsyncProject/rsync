#!/usr/bin/env python3
"""A daemon sender must follow an in-tree symlink for -L/--copy-links.

A read-only daemon module that contains a benign in-tree symlink (`lnk` ->
a sibling regular file inside the module) must, when pulled with `-aL`, transfer
the symlink's target as a regular file -- exactly as a plain local `-aL` does.

Regression: the daemon sender was observed to refuse the in-tree symlink with
"failed to open ... Too many levels of symbolic links" (ELOOP), exit 23, leaving
the file untransferred -- while a local `-aL` of the same tree, and an older
rsync over the daemon, both follow it. The leaf-symlink refusal that protects the
content open must not defeat an operator's explicit --copy-links request for an
in-tree (non-escaping) symlink.
"""

import os

from rsyncfns import (
    SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12935

served = SCRATCHDIR / 'cli_mod'
dest = SCRATCHDIR / 'cli_dest'
rmtree(served)
rmtree(dest)
makepath(served)
(served / 'real').write_text("symlink target content\n")
os.symlink('real', served / 'lnk')          # in-tree symlink to a sibling file
(served / 'anchor').write_text("anchor\n")

conf = write_daemon_conf([('m', {'path': str(served), 'read only': 'yes'})])
url = start_test_daemon(conf, DAEMON_PORT)

# Pull with -L: the daemon sender must follow the in-tree symlink and send its
# target as a regular file (codes 0 ok; 23 = the very failure we are guarding).
run_rsync('-aL', f'{url}m/', f'{dest}/', check=False)

if not (dest / 'lnk').exists():
    test_fail("daemon -aL did not transfer the in-tree symlink 'lnk' "
              "(the sender refused to follow it)")
if os.path.islink(dest / 'lnk'):
    test_fail("daemon -aL left 'lnk' as a symlink instead of following it")
assert_same(dest / 'lnk', served / 'real',
            label="daemon -aL followed the in-tree symlink to its target")

print("daemon-copylinks-intree: daemon -aL follows an in-tree symlink")
