#!/usr/bin/env python3
# Malicious daemon-sender expands --delete scope by flipping an implied
# parent into a content-dir.
#
# Threat (Mitchell Benjamin report, May 2026; PoC 3):
#   With `rsync -rR --delete rsync://host/m/dir/file dest/`, the client
#   requests a single relative path.  The sender's honest flow encodes
#   implied parents with XMIT_TOP_DIR|XMIT_NO_CONTENT_DIR (flist.c:415
#   in send_file_entry).  A malicious daemon-sender that omits
#   XMIT_NO_CONTENT_DIR on the implied parent "dir" makes the receiver
#   set FLAG_CONTENT_DIR (flist.c:1095), and the generator then calls
#   delete_in_dir("dest/dir") under --delete, sweeping pre-existing
#   siblings the receiver expected to keep.
#
#   The receiver-side fix (this branch) downgrades implied-parent dirs
#   to non-content regardless of the sender's xflags byte, so
#   delete_in_dir cannot run on a directory the implied_filter_list
#   only knows as a parent.
#
# Test approach (Mitchell's PoC 3 adapted):
#   We don't need a fake-daemon-protocol speaker.  Instead, build a
#   "malicious rsync" out of the same source tree by doing a one-line
#   substring replacement in flist.c that strips XMIT_NO_CONTENT_DIR
#   from the implied-parent encoding, then run the patched binary as
#   the daemon-sender and the production rsync (the one under test)
#   as the receiver.  The receiver-side fix is what we're verifying.
#
#   Honest-sender baseline (the production rsync as daemon-sender)
#   exists implicitly: every other -rR --delete test in the suite
#   already exercises that path, so we don't repeat it here.  The
#   patched-rsync arm is the one signal that distinguishes RED from
#   GREEN.
#
# Build cost:
#   make(1) is invoked inside a copied source tree, ~10-30s.  We skip
#   the test cleanly if SRCDIR isn't a writable build tree (e.g. when
#   running an installed rsync from a binary package via --rsync-bin).
#
# Outcome:
#   * dest/dir/keep.txt SURVIVES the pull -> fix is in place (PASS).
#   * dest/dir/keep.txt is DELETED -> bug present (FAIL with leak
#     diagnostic).

import os
import shlex
import shutil
import subprocess

from rsyncfns import (
    SCRATCHDIR, SRCDIR, makepath, rmtree, rsync_argv,
    test_fail, test_skipped, write_daemon_conf,
)

# Sanity gates ---------------------------------------------------------------
# Need a writable source tree we can copy and `make rsync` in.
if not (SRCDIR / 'flist.c').is_file() or not (SRCDIR / 'Makefile').is_file():
    test_skipped("malicious-sender-delete-scope: needs a writable rsync "
                 f"source tree at SRCDIR={SRCDIR} with Makefile + flist.c; "
                 "the patched-sender build relies on it.")
if not shutil.which('make'):
    test_skipped("malicious-sender-delete-scope: make(1) not on PATH")
if not shutil.which('gcc') and not shutil.which('cc'):
    test_skipped("malicious-sender-delete-scope: no C compiler on PATH")


# -- Build the malicious sender ---------------------------------------------
# flist.c send_implied_dirs() sets up the flags an implied-parent will be
# encoded with via the FLAG_IMPLIED_DIR branch of send_file_entry's xflags
# computation (line ~415).  Strip XMIT_NO_CONTENT_DIR from that one
# assignment so the implied parent is sent with XMIT_TOP_DIR ALONE.  Every
# other encoding path is unchanged, so the resulting daemon-sender is
# honest about everything except the implied-parent encoding -- which is
# exactly the threat model.
mal_src = SCRATCHDIR / 'mal-rsync'
rmtree(mal_src)
shutil.copytree(SRCDIR, mal_src, symlinks=True,
                ignore=shutil.ignore_patterns('testtmp', '.git', 'auto-build-save'))

flist_c = mal_src / 'flist.c'
PATCH_OLD = "xflags = XMIT_TOP_DIR | XMIT_NO_CONTENT_DIR;"
PATCH_NEW = "xflags = XMIT_TOP_DIR; /* malicious-sender PoC: drop NO_CONTENT_DIR */"
text = flist_c.read_text()
if PATCH_OLD not in text:
    test_skipped(
        "malicious-sender-delete-scope: could not find the implied-parent "
        "encoding literal in flist.c -- upstream rename?  Looking for: "
        f"{PATCH_OLD!r}.  Update PATCH_OLD in this test if the assignment "
        "changed shape.")
flist_c.write_text(text.replace(PATCH_OLD, PATCH_NEW, 1))

# Build just the rsync binary in the copied tree.  The copy includes
# config.h / Makefile so configure has already been done.  Suppress
# stdout/stderr because a clean rebuild produces several screens of gcc
# output that aren't useful here.
build = subprocess.run(['make', '-j2', 'rsync'], cwd=str(mal_src),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
mal_rsync = mal_src / 'rsync'
if build.returncode != 0 or not mal_rsync.is_file() or not os.access(mal_rsync, os.X_OK):
    test_skipped(
        "malicious-sender-delete-scope: malicious-sender build failed.  "
        f"`make rsync` rc={build.returncode}.  Tail of build output:\n"
        + '\n'.join(build.stdout.splitlines()[-20:]))


# -- Set up source + dest, with the sentinel ----------------------------------
base = SCRATCHDIR / 'mal-test'
rmtree(base)
src = base / 'src'
dest = base / 'dest'
makepath(src / 'dir')
makepath(dest / 'dir')
(src / 'dir' / 'file').write_text("from server\n")
sentinel = dest / 'dir' / 'keep.txt'
sentinel.write_text("MUST_STAY\n")


# -- Wire up the malicious daemon via RSYNC_CONNECT_PROG ----------------------
# The runtests.py secure-default daemon transport uses RSYNC_CONNECT_PROG
# to fork a daemon over a private stdio pipe instead of binding a TCP
# socket.  That seam lets us point the production rsync (the receiver)
# at the malicious binary as its daemon-sender without ever touching a
# listening port -- which also keeps the test compatible with the
# concurrent-j16 runner.
conf = write_daemon_conf(
    [('m', {'path': str(src),
            'read only': 'yes',
            'use chroot': 'no'})],
    name='mal-rsyncd.conf')
os.environ['RSYNC_CONNECT_PROG'] = f'{shlex.quote(str(mal_rsync))} --config={shlex.quote(str(conf))} --daemon'


# -- The attack pull: -rR --delete from the malicious daemon -----------------
# The receiver is the production rsync (which on the fix branches carries
# the implied-parent downgrade).  Use --debug=del2 so we can grep the
# output for delete_in_dir() if we need to triangulate a failure later.
url = 'rsync://localhost/m/dir/file'
proc = subprocess.run(
    rsync_argv('-rR', '--delete', '--debug=del2', url, str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


# -- The oracle --------------------------------------------------------------
# With the FIX: receiver recognises "dir" as an implied-parent-only entry,
# forces XMIT_NO_CONTENT_DIR back into the xflags before flag mapping, so
# FLAG_CONTENT_DIR is never set; delete_in_dir() never runs on dir;
# sentinel survives.
#
# Without the FIX: receiver trusts the malicious xflags byte, sets
# FLAG_CONTENT_DIR on "dir", generator runs delete_in_dir("dest/dir"),
# sweeps every entry that wasn't in the sender's list, sentinel is gone.
if not sentinel.is_file():
    debug_tail = '\n'.join(proc.stdout.splitlines()[-30:])
    test_fail(
        f"malicious daemon-sender expanded --delete scope: "
        f"{sentinel} was deleted by the receiver.  This means the "
        "implied parent 'dir' was flagged FLAG_CONTENT_DIR on the basis "
        "of the sender's xflags byte (XMIT_TOP_DIR without "
        "XMIT_NO_CONTENT_DIR), and delete_in_dir() ran on dest/dir.  "
        "Fix: the receiver must force XMIT_NO_CONTENT_DIR on any "
        "directory entry that implied_filter_list only allowed as a "
        "parent (flist.c recv_file_entry).  Last 30 lines of receiver "
        f"output:\n{debug_tail}")

# Also assert the file the sender legitimately offered did arrive --
# without this, a daemon that crashed before sending anything would also
# leave the sentinel alone and we'd false-pass.
delivered = dest / 'dir' / 'file'
if not delivered.is_file():
    test_fail(
        f"sentinel survived but the malicious daemon didn't deliver "
        f"{delivered} either -- the test is vacuous.  Receiver rc="
        f"{proc.returncode}.  Output tail:\n"
        + '\n'.join(proc.stdout.splitlines()[-20:]))

print(
    f"malicious-sender-delete-scope: receiver refused to expand --delete "
    f"scope under a malicious sender that omitted XMIT_NO_CONTENT_DIR on "
    f"the implied parent.  {sentinel.name} survived, "
    f"{delivered.relative_to(dest)} delivered.")
