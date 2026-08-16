#!/usr/bin/env python3
# Daemon --backup-dir ".." traversal into the module's excluded subtree, on a
# `path = /` module.  (Reported by Omar Elsayed.)  Overwriting an existing dest
# file triggers a backup of the OLD file; the peer-supplied --backup-dir climbs
# via ".." back into the excluded subtree, so the backup must not land there.
# Sandbox-anchored under SCRATCHDIR (Omar's original wrote to the real /secret),
# so it runs unprivileged; the module still serves "/".
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf, test_skipped,
)

DAEMON_PORT = 12964

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

base = SCRATCHDIR / 'traversal-backup-dir-daemon'
rmtree(base)
base.mkdir()

backup = base / 'backup-dir-daemon'
backup.mkdir()

secret = base / 'secret'                          # excluded subtree
secret.mkdir()
victim = secret / 'f0'
victim.write_text(SECRET)                          # the backup must not clobber it

dest = backup / 'dest'
dest.mkdir()
(dest / 'f0').write_text("OLD-DESTINATION-FILE-CONTENT\n")   # existing -> backed up
src = backup / 'src'
src.mkdir()
sub = src / 'sub'
sub.mkdir()
(src / 'f0').write_text("NEW-PUSHED-CONTENT\n")     # differs -> overwrite

base_rel = str(base).lstrip('/')
conf = write_daemon_conf(
    [('everything', {'path': '/', 'read only': 'no', 'exclude': str(secret) + '/'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv(
        '-a', '--backup',
        f'--backup-dir=/{base_rel}/backup-dir-daemon/src/sub/../../../secret/',
        f'{src}/',
        f'{url}everything/{base_rel}/backup-dir-daemon/dest',
    ),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after != SECRET:
    test_fail("escaped: a --backup-dir '..' traversal reached the excluded subtree "
              f"({victim} is now {after!r})")
print("did not escape")
