#!/usr/bin/env python3
# Regression test for a stale-dir_flist use-after-free on a sub-flist marker
# sent after every flist has been freed (flist.c recv_file_list).
#
# In inc_recurse mode the receiver frees each flist as it finishes. When the
# last goes, flist_free() sets first_flist = NULL and pool_destroy()s the shared
# file pool, but the global dir_flist is left pointing into the destroyed pool.
# A malicious sender that then sends an NDX_FLIST_OFFSET sub-flist marker made
# recv_file_list() write FLAG_GOT_DIR_FLIST into the freed dir_flist entry (a
# use-after-free) and re-create dir_flist, before the existing dirname !d guard
# turned the follow-on uninitialised deref into a clean RERR_UNSUPPORTED exit.
# The fix refuses the sub-flist up front when first_flist is NULL.
#
# This drives a real daemon receiver with the pure-Python sender, in
# inc_recurse mode (-r + an -e string containing 'i'): push a tiny top-level
# flist (two directories, so there is nothing to transfer and every flist is
# freed promptly), then send a sub-flist marker.
#
# Oracle: fixed -> the receiver logs "refusing sub-flist after final flist was
# freed" (RERR_PROTOCOL at the up-front guard). pre-fix -> that guard is absent;
# the receiver instead performs the use-after-free and exits later via the !d
# backstop ("requested action not supported"), so the refuse message never
# appears (and under ASan the UAF is a hard report). Normal-build oracle; only
# needs a real TCP daemon.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12958
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'sf-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'sf.conf'
log = SCRATCHDIR / 'sf.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/sf-rsyncd.pid
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
# Two top-level directories: nothing to transfer.
d = rp.FileEntry('d', mode=rp.S_IFDIR | 0o755, length=0, extra_flags=rp.XMIT_TOP_DIR)
s.send_data(d.encode() + d.encode() + rp.end_of_flist(0, s.protocol))
# One NDX_DONE frees the (single) top flist -> first_flist becomes NULL; then a
# sub-flist marker hits the "after final flist freed" guard. (More than one
# would advance phase past max_phase and break recv_files before the marker.)
s.send_ndx_done()
s.send_subflist_marker(0)
s.drain(timeout=2.0)
s.close()

refused = False
for _ in range(50):
    new = log.read_text(errors='replace')[len(before):]
    if 'refusing sub-flist after final flist was freed' in new:
        refused = True
        break
    time.sleep(0.1)

if not refused:
    new = log.read_text(errors='replace')[len(before):]
    test_fail(
        "receiver did not refuse a sub-flist marker sent after all flists were "
        "freed -- it used the stale dir_flist (use-after-free).\n"
        f"daemon log:\n{new}")

print("proto-subflist-freed: receiver refused a sub-flist after the final flist "
      "was freed (no stale-dir_flist use-after-free).")
