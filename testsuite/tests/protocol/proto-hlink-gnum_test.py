#!/usr/bin/env python3
# Regression test for a peer-controlled assert() abort in match_gnums()
# (hlink.c). With inc_recurse, a non-first hard-link entry carries
# first_hlink_ndx as its gnum. recv_file_entry() only bounds it as
# [0, ndx_start + used) but does not require that a value below ndx_start was
# ever declared via XMIT_HLINK_FIRST in an earlier flist. Such a gnum is absent
# from prior_hlinks, so the new-node branch in match_gnums() hit
# assert(gnum >= hlink_flist->ndx_start) -- a remotely-reachable abort() of the
# generator on default (assert-enabled) builds. The fix replaces the assert
# with a clean RERR_PROTOCOL exit.
#
# A first-flist back-reference would use gnum 0, which the hashtable rejects as
# an illegal key before the assert, so we put the hard-link entry in a SUB-flist
# (ndx_start >= 2) with a small but non-zero gnum. This drives a real daemon
# receiver with the pure-Python sender, -H + inc_recurse: a top-level content
# dir "d" plus an unfinished file (keeps first_flist alive), then a sub-flist
# for "d" containing a regular-file hard-link entry whose gnum (1) is below the
# sub-flist's ndx_start and was never declared.
#
# Oracle: fixed -> "hard-link gnum 1 precedes flist start N" (RERR_PROTOCOL).
# pre-fix -> the generator SIGABRTs on the assert, so that message never
# appears. Asserts are compiled in on the default debug build, so this is a
# normal-build RED/GREEN; only needs a real TCP daemon.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12960
BACKREF_GNUM = 1
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'hg-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'hg.conf'
log = SCRATCHDIR / 'hg.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/hg-rsyncd.pid
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = no
""")
start_rsyncd(conf, PORT)

before = log.read_text(errors='replace') if log.exists() else ''
s = rp.DaemonSender('127.0.0.1', PORT)
s.handshake('mod', ['--server', '-rHe.iLsfxCIu', '.', 'mod/'], greeting_version=30)
# Top flist: a content dir "d" + an unfinished file (used=2 -> the sub-flist's
# ndx_start is > 1, so our small gnum is below it; the file keeps first_flist
# alive so the sub-flist is received rather than refused as "after free").
d = rp.FileEntry('d', mode=rp.S_IFDIR | 0o755, length=0, extra_flags=rp.XMIT_TOP_DIR)
f = rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=100)
s.send_data(d.encode() + f.encode() + rp.end_of_flist(0, s.protocol))
# Sub-flist for dir 0 ("d") with a hard-link entry carrying a back-ref gnum that
# was never declared XMIT_HLINK_FIRST.
h = rp.FileEntry('d/h', mode=rp.S_IFREG | 0o644, length=0, hlink_ndx=BACKREF_GNUM)
s.send_data(s.w_ndx(rp.NDX_FLIST_OFFSET - 0) + h.encode() + rp.end_of_flist(0, s.protocol))
s.drain(timeout=2.0)
s.close()

refused = False
needle = 'hard-link gnum %d precedes flist start' % BACKREF_GNUM
for _ in range(50):
    new = log.read_text(errors='replace')[len(before):]
    if needle in new:
        refused = True
        break
    time.sleep(0.1)

if not refused:
    new = log.read_text(errors='replace')[len(before):]
    test_fail(
        "generator did not reject an undeclared back-reference hard-link gnum "
        "-- it hit the match_gnums() assert(gnum >= ndx_start) and aborted.\n"
        f"daemon log:\n{new}")

print("proto-hlink-gnum: generator rejected an undeclared back-reference "
      "hard-link gnum (no assert abort).")
