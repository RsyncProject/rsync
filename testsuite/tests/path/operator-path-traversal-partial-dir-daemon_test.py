#!/usr/bin/env python3
# Daemon --partial-dir ".." traversal into the module's excluded subtree, on a
# `path = /` module.  (Reported by Omar Elsayed.)  The peer-supplied --partial-dir
# climbs via ".." back into the excluded subtree; a staged partial must not land
# there.  Sandbox-anchored under SCRATCHDIR (Omar's original wrote to the real
# /secret), so it runs unprivileged; the module still serves "/".
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf, test_skipped,
)

DAEMON_PORT = 12965

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

base = SCRATCHDIR / 'traversal-partial-dir-daemon'
rmtree(base)
base.mkdir()

partial = base / 'partial-dir-daemon'
partial.mkdir()

secret = base / 'secret'                          # excluded subtree
secret.mkdir()
victim = secret / 'f0'
victim.write_text(SECRET)

dest = partial / 'dest'
dest.mkdir()
src = partial / 'src'
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
        f'--partial-dir=/{base_rel}/partial-dir-daemon/src/sub/../../../secret/',
        f'{src}/',
        f'{url}everything/{base_rel}/partial-dir-daemon/dest',
    ),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after is None:
    test_fail("escaped: a --partial-dir '..' traversal DELETED the excluded victim")
if after != SECRET:
    test_fail("escaped: a --partial-dir '..' traversal OVERWROTE the excluded victim "
              f"({victim} is now {after!r})")
print("did not escape")
