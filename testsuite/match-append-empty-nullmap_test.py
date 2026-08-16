#!/usr/bin/env python3
# Regression test for a NULL-pointer deref in match_sums() (match.c): in
# daemon-as-sender mode, send_files() leaves mbuf == NULL for an empty served
# file (st.st_size == 0). For append_mode == 2 (--append-verify), match_sums()
# runs a verify loop driven by s->flength, which receive_sums() derives from the
# peer's sum header (count * blength) -- NOT the daemon's own flist size. The
# diminished-file guard compares st.st_size against F_LENGTH(file) (both 0), so
# it does not catch a peer that claims a non-empty length.
#
# A hostile client pulls an EMPTY module file with --append-verify and forges a
# sum header of count=1, blength=1 (remainder=0) -> s->flength = 1. The
# append_mode==2 loop then calls sum_update(map_ptr(NULL, 0, 1), 1), and
# map_ptr() (len != 0) dereferences the NULL map -- a remote unauthenticated SEGV
# of the per-connection daemon child.  (receive_sums() returns early in append
# mode, so no block sums follow the header.)  The fix clamps s->flength to len
# before the verify loop.
#
# Oracle: pre-fix -> the daemon child hits an ASan SEGV report; fixed -> none.
# Needs an ASan build + a real TCP daemon, and is skipped otherwise.

import glob
import os

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12952
require_tcp("the pure-Python generator needs a real TCP daemon; run with --use-tcp")
require_asan("the append-mode NULL map_ptr deref is observed via the daemon's "
            "AddressSanitizer SEGV report")
claim_ports(PORT)

mod = SCRATCHDIR / 'append-mod'
mod.mkdir(parents=True, exist_ok=True)
(mod / 'f').write_bytes(b'')          # empty file => mbuf is NULL on the sender

conf = SCRATCHDIR / 'append.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/append-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
""")

asan_log = SCRATCHDIR / 'append-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

c = rp.DaemonClient('127.0.0.1', PORT)
# --append-verify => the daemon sender runs with append_mode == 2.
c.handshake('mod', ['--server', '--sender', '-e.LsfxCIu', '--append-verify',
                    '.', 'mod/f'], greeting_version=30)
entries = rp.sort_entries(c.recv_flist(preserve_links=False))
ndx = next(i for i, e in enumerate(entries) if e.name == b'f')

# Forged sum header claiming a non-empty verified prefix (flength = 1*1 = 1)
# against the empty file.  In append mode receive_sums() returns after the
# header, so no block sums follow.
req = (c.w_ndx(ndx) + rp.w_shortint(rp.ITEM_TRANSFER)
       + rp.w_sum_head(1, 1, 0, 0))
c.send_data(req)
c.send_data(c.w_ndx(rp.NDX_DONE))     # trailing data so the header read completes
c.drain(timeout=3.0)
c.close()

reports = glob.glob(f"{asan_log}.*")
text = ''.join(open(r, errors='replace').read() for r in reports)
if 'AddressSanitizer' in text:
    test_fail(
        "daemon sender hit an AddressSanitizer error on a forged append-mode sum "
        "header against an empty file -- s->flength was taken from the peer and "
        "the append verify loop dereferenced the NULL map:\n" + text[:1500])

print("match-append-empty-nullmap: forged append-mode flength is clamped to the "
      "local length; no NULL-deref in match_sums.")
