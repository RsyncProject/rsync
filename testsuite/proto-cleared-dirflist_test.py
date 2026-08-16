#!/usr/bin/env python3
# Regression test for a receiver NULL-deref on a sub-flist whose dir index
# targets a cleared dir_flist entry (flist.c recv_file_list).
#
# Under inc_recurse the receiver appends every received directory entry to
# dir_flist before flist_sort_and_clean() runs. If the peer sends two top-level
# directories with the SAME name, both are appended (dir_flist->used == 2), then
# the dedup pass clear_file()s one -- but the cleared file_struct stays in
# dir_flist->files[] at an index still < used. A sub-flist tagged with that
# cleared dir index passes the >= used bounds check, and the dirname-validation
# strcmp() then calls f_name() on the cleared entry (returns NULL) and
# dereferences it. The fix refuses an inactive (!F_IS_ACTIVE) dir slot up front.
#
# This drives a real daemon receiver with the pure-Python sender in inc_recurse
# mode: a top-level flist of two same-named dirs "d" plus an UNFINISHED regular
# file (non-zero length, no data sent) so the receiver keeps first_flist alive
# (otherwise the marker hits the separate "after final flist freed" guard); then
# a sub-flist marker for the cleared dir index (1, deterministic) followed by a
# child entry so the pre-fix path reaches the crashing strcmp.
#
# Oracle: fixed -> "refusing flist for cleared dir_ndx 1" (RERR_PROTOCOL). pre-fix
# -> the receiver dereferences the NULL f_name() and crashes, so that message
# never appears (the generator just reports the connection dropped). Normal-build
# crash; only needs a real TCP daemon.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12959
CLEARED_NDX = 1  # flist_sort_and_clean() deterministically clears the 2nd dup dir
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'cd-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'cd.conf'
log = SCRATCHDIR / 'cd.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/cd-rsyncd.pid
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
# Two same-named top-level dirs (one gets clear_file()'d) + an unfinished file
# (keeps first_flist alive so we reach the cleared-slot guard, not the freed one).
d = rp.FileEntry('d', mode=rp.S_IFDIR | 0o755, length=0, extra_flags=rp.XMIT_TOP_DIR)
f = rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=100)
s.send_data(d.encode() + d.encode() + f.encode() + rp.end_of_flist(0, s.protocol))
# Sub-flist marker for the cleared dir slot, then a child entry (so the pre-fix
# receiver reaches the strcmp that dereferences the NULL f_name()).
child = rp.FileEntry('x', mode=rp.S_IFREG | 0o644, length=0)
s.send_data(s.w_ndx(rp.NDX_FLIST_OFFSET - CLEARED_NDX)
            + child.encode() + rp.end_of_flist(0, s.protocol))
s.drain(timeout=2.0)
s.close()

refused = False
for _ in range(50):
    new = log.read_text(errors='replace')[len(before):]
    if 'refusing flist for cleared dir_ndx' in new:
        refused = True
        break
    time.sleep(0.1)

if not refused:
    new = log.read_text(errors='replace')[len(before):]
    test_fail(
        "receiver did not refuse a sub-flist for a cleared (duplicate) dir slot "
        "-- it dereferenced the NULL f_name() and crashed.\n"
        f"daemon log:\n{new}")

print("proto-cleared-dirflist: receiver refused a sub-flist for a cleared "
      "dir_flist slot (no NULL-deref crash).")
