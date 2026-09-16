#!/usr/bin/env python3
# Functional regression: a daemon module with `path = /` (use chroot = no)
# cannot send ANY file in 3.4.3 -- every read fails with "Invalid argument (22)".
#
# Reported as #897 ("Regression in 3.4.3, Invalid argument (22) on all file
# reads when using native protocol").  Works in 3.4.2, works over a remote
# shell; only the native (daemon) protocol with an absolute module path breaks.
#
# Root cause: the 3.4.3 symlink-race hardening routes the sender's file open
# through secure_relative_open(module_dir, secure_path, ...) (sender.c), where
#   secure_path = F_PATHNAME + "/" + f_name.
# With `path = /` the module-relative F_PATHNAME is itself ABSOLUTE, so
# secure_path starts with '/'.  secure_relative_open()'s front door rejects any
# absolute relpath with EINVAL *before* it ever calls openat2 (matching the
# reporter's strace: the file is stat'd and access()'d but never opened).  The
# generator then reports "send_files failed to open ...: Invalid argument (22)"
# and the whole transfer ends in code 23.
#
# This is a pure functional regression (no attacker, no symlink): XFAIL until
# the sender open is made to tolerate an absolute module-root path (the
# accompanying sender.c fix).  Runs at any uid.

import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

DAEMON_PORT = 12897

# A small source tree under the scratch dir: a file at the served-subdir root
# and one nested deeper (the bug fails on EVERY file, regardless of depth).
served = SCRATCHDIR / 'served'
dst = SCRATCHDIR / 'pulldst'
rmtree(served)
rmtree(dst)
makepath(served / 'sub')
makepath(dst)
(served / 'README').write_text("readme-contents\n")
(served / 'sub' / 'deep.txt').write_text("deep-contents\n")

# Module rooted at the filesystem root, exactly like the report (path = /,
# use chroot = no).  We then request the served subtree by its absolute path
# with the leading '/' stripped, so the daemon serves $served from "/".
conf = write_daemon_conf([
    ('root', {'path': '/', 'read only': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

served_rel = str(served).lstrip('/')          # e.g. tmp/.../served
proc = subprocess.run(
    rsync_argv('-a', f'{url}root/{served_rel}/', f'{dst}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
out = proc.stdout or ''
print(out)

# Bug present: the sender refuses to open the files with EINVAL(22).
if 'Invalid argument (22)' in out or ('failed to open' in out and proc.returncode != 0):
    from rsyncfns import test_xfail
    test_xfail(
        "#897: daemon module `path = /` (use chroot = no) cannot send files -- "
        "`send_files failed to open ...: Invalid argument (22)`.  The sender's "
        "secure_relative_open(module_dir, secure_path) gets an ABSOLUTE "
        "secure_path (F_PATHNAME is absolute when path=/) and the front door "
        "rejects absolute relpaths with EINVAL before any openat2.  To be closed "
        "by letting the sender open succeed for an absolute module-root path.")

# Bug fixed (or never present): the files transfer intact.
if proc.returncode != 0:
    test_fail(f"daemon pull failed unexpectedly (rc={proc.returncode}); "
              f"not the #897 EINVAL symptom:\n{out}")
for rel in ('README', 'sub/deep.txt'):
    got = dst / rel
    if not got.is_file():
        test_fail(f"daemon pull did not deliver {rel} (dst={dst})")
    if got.read_text() != (served / rel).read_text():
        test_fail(f"delivered {rel} content differs from source")
