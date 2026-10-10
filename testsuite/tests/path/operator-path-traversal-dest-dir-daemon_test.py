#!/usr/bin/env python3
# Daemon destination ".." traversal into the module's excluded subtree, on a
# `path = /` module.  (Reported by Omar Elsayed.)  The peer pushes to a dest that
# climbs out of an in-module dir back into the excluded subtree via "..".
# Sandbox-anchored under SCRATCHDIR (Omar's original wrote to the real /secret),
# so it runs unprivileged; the module still serves "/" so the traversal is real.
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf, test_skipped,
)

DAEMON_PORT = 12963

# rsyncd.conf's "exclude" is a SPACE-SEPARATED pattern list, so a path with a
# space in it cannot be expressed there at all -- the pattern silently matches
# nothing and the traversal below then "escapes" for a reason that has nothing
# to do with the protection under test.  ("filter" is space-safe and does hold;
# verified by running this same case with a filter rule.)  Skip rather than
# report a bogus escape.
if ' ' in str(SCRATCHDIR):
    test_skipped("scratch path contains a space; rsyncd.conf 'exclude' is a "
                 "space-separated list and cannot express such a pattern")
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'traversal-dest-dir-daemon'
rmtree(base)
base.mkdir()

dest = base / 'dest-dir-daemon'
dest.mkdir()

secret = base / 'secret'                          # excluded subtree
secret.mkdir()
victim = secret / 'f0'
victim.write_text(SECRET)

src = dest / 'src'
src.mkdir()
sub = src / 'sub'
sub.mkdir()
(src / 'f0').write_text("NEW-PUSHED-CONTENT\n")

base_rel = str(base).lstrip('/')
conf = write_daemon_conf(
    [('everything', {'path': '/', 'read only': 'no', 'exclude': str(secret) + '/'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv(
        '-a',
        f'{src}/',
        f'{url}everything/{base_rel}/dest-dir-daemon/src/sub/../../../secret/',
    ),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after != SECRET:
    test_fail("escaped: a dest '..' traversal reached the excluded subtree "
              f"({victim} is now {after!r})")
print("did not escape")
