#!/usr/bin/env python3
# Regression test for a wild-pointer read via the first inc_recurse flist's
# parent_ndx when dir_flist is empty (flist.c recv_file_list).
#
# Under inc_recurse the first flist (ndx_start == 1) has no parent entry of its
# own: recv_file_list() trusts the peer's "." entry to be the transfer root and
# leaves parent_ndx at the flist_new() default of 0, meaning dir_flist->files[0].
# Only S_ISDIR entries are appended to dir_flist, so a peer that sends a "."
# entry with a NON-directory mode keeps dir_flist->used at 0 while the basename
# strcmp() still passes -- parent_ndx stays 0 and the consumers (generate_files,
# recv_files, touch_up_dirs) index a never-written slot of the freshly allocated
# dir_flist->files[], i.e. uninitialised heap, and dereference it.
#
# This drives a real daemon receiver with the pure-Python sender: an inc_recurse
# push whose first (and only) flist is a "." REGULAR file plus a second regular
# file "a" -- no directory anywhere, so dir_flist->used stays 0, and "." sorts
# lowest so the basename test passes. ("a" is there so file_total != 1 and the
# receiver doesn't divert into recv_additional_file_list.)
#
# The file list ALONE is what does it: the GENERATOR (the connection parent;
# do_recv() forks the receiver as the child) crashes in generate_files() before
# any transfer phase. The follow-on token for ndx 0 is sent only to exercise
# recv_files()'s parent_ndx path on a build that survives the list; on a
# vulnerable one the generator is already gone by then, so this test does not
# demonstrate the receiver-side consumer.
#
# Three independent layers now stop this, in wire order: recv_file_entry()
# refuses a non-directory transfer-root entry outright; recv_file_list() clears
# parent_ndx to -1 when dir_flist is empty; and the parent_ndx consumers bounds-
# check against dir_flist->used. Only the first fires on a current build, so
# THIS TEST GATES THE ATTACK SHAPE, NOT THE parent_ndx CLAUSE: removing that one
# clause alone leaves the test passing. Isolating it needs the other two layers
# disabled as well (done once by hand: clause out = SIGSEGV, clause in =
# orderly refusal), which is why the clause is still worth backporting alone.
#
# The dereferenced slot is not guaranteed to be NULL -- dir_flist->files[] comes
# from realloc(), not calloc(). It reads as NULL in an ordinary run (si_addr
# 0x14 = file->mode), but a peer that grooms the heap first (a large freed
# filter list) can put its own bytes there. Either way the observed outcome is
# a crash. Normal-build oracle; only needs a real TCP daemon.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12967
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'pn-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'pn.conf'
log = SCRATCHDIR / 'pn.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/pn-rsyncd.pid
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = no
""")
start_rsyncd(conf, PORT)

before = log.read_text(errors='replace') if log.exists() else ''
s = rp.DaemonSender('127.0.0.1', PORT)
s.handshake('mod', ['--server', '-re.iLsfxCIu', '.', 'mod/'], greeting_version=30)
# The transfer root "." as a regular file: it never lands in dir_flist, so
# dir_flist->used stays 0 while "." still sorts lowest.
dot = rp.FileEntry('.', mode=rp.S_IFREG | 0o644, length=100)
other = rp.FileEntry('a', mode=rp.S_IFREG | 0o644, length=100)
s.send_data(dot.encode() + other.encode() + rp.end_of_flist(0, s.protocol))
time.sleep(0.5)
# ndx 0 == first_flist->ndx_start - 1.  A FIXED daemon has already refused the
# file list and gone by now, so this send and the drain may hit a closed socket:
# Linux and FreeBSD swallow it, other stacks raise EPIPE/ECONNRESET.  That is an
# expected outcome, not a result -- the verdict comes from the daemon log below.
try:
    s.send_transfer_ndx(0, rp.ITEM_TRANSFER)
    s.drain(timeout=3.0)
except (OSError, rp.ProtocolError):
    pass
s.close()

# Two things must both be true, and a bare "rsync error:" is NOT enough: any
# later wire error would satisfy that, so the test would pass without the
# crafted list ever reaching the parser (proved by sending a bogus file index
# against a build where "." was a valid directory -- it "passed").
#   * positive control: the daemon reached recv_file_list() at all; and
#   * the refusal names THIS condition.
reached = refused = False
for _ in range(50):
    new = log.read_text(errors='replace')[len(before):]
    reached = 'receiving file list' in new
    refused = 'rejecting non-directory transfer-root entry' in new
    if reached and refused:
        break
    time.sleep(0.1)

new = log.read_text(errors='replace')[len(before):]
if not reached:
    test_fail("the daemon never logged 'receiving file list', so the crafted "
              "transfer root never reached recv_file_list() -- this run proves "
              f"nothing either way.\ndaemon log:\n{new}")
if not refused:
    test_fail(
        "the daemon did not refuse the non-directory transfer root of a first "
        "inc_recurse flist.  Either it died dereferencing dir_flist->files[0] "
        "from an empty dir_flist (no further log output), or it failed for some "
        f"unrelated reason.\ndaemon log:\n{new}")

print("proto-parent-ndx-empty-dirflist: a non-directory transfer root in the "
      "first inc_recurse flist is refused, not turned into a dir_flist->files[0] "
      "deref.")
