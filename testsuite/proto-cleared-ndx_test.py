#!/usr/bin/env python3
# Regression test for a receiver NULL-deref on a transfer-phase index that
# targets a cleared file-list entry (receiver.c recv_files).
#
# A peer can send duplicate file-list entries; flist_sort_and_clean() then
# clear_file()s one of them (basename and mode zeroed) but leaves its slot
# indexable. If the transfer phase sends an ndx for that slot, recv_files()
# resolves it to the cleared file_struct and f_name() returns NULL, which flows
# into the daemon filter check / set_file_attrs() -> full_fname() and crashes
# the receiver child (NULL deref). The fix refuses an inactive entry
# (!F_IS_ACTIVE) at the ndx->file_struct resolution with a clean RERR_PROTOCOL.
#
# This drives a real daemon receiver with the pure-Python sender: push a file
# list with the SAME name twice (so flist_sort_and_clean clears the second
# slot, index 1), then send a transfer-phase ndx for that cleared slot.
#
# Oracle: fixed -> the receiver refuses with "refusing transfer of cleared file
# index 1" and exits RERR_PROTOCOL. pre-fix -> the receiver SIGSEGVs before
# logging anything, so that message never appears (the generator just reports
# the connection dropped). This is a normal-build crash (no ASan needed); it
# only needs a real TCP daemon.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12957
CLEARED_NDX = 1  # flist_sort_and_clean() deterministically clears the 2nd dup
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

mod = SCRATCHDIR / 'cn-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'cn.conf'
log = SCRATCHDIR / 'cn.log'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/cn-rsyncd.pid
use chroot = no
log file = {log}

[mod]
    path = {mod}
    read only = no
""")
start_rsyncd(conf, PORT)

before = log.read_text(errors='replace') if log.exists() else ''
s = rp.DaemonSender('127.0.0.1', PORT)
s.handshake('mod', ['--server', '-e.LsfxCIu', '.', 'mod/'], greeting_version=30)
# Same name twice -> the 2nd entry's slot is clear_file()'d.
dup = rp.FileEntry('dup.txt', mode=rp.S_IFREG | 0o644, length=0)
s.send_flat_flist([dup, dup])
# Transfer-phase index for the cleared slot.
s.send_transfer_ndx(CLEARED_NDX, iflags=0)
s.drain(timeout=2.0)
s.close()

refused = False
for _ in range(50):
    new = log.read_text(errors='replace')[len(before):]
    if 'refusing transfer of cleared file index' in new:
        refused = True
        break
    time.sleep(0.1)

if not refused:
    new = log.read_text(errors='replace')[len(before):]
    test_fail(
        "receiver did not refuse a transfer ndx targeting a cleared (dup) "
        "file-list entry -- it dereferenced the NULL f_name() and crashed.\n"
        f"daemon log:\n{new}")

print("proto-cleared-ndx: receiver refused a transfer ndx for a cleared "
      "file-list slot (no NULL-deref crash).")
