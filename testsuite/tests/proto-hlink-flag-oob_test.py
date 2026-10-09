#!/usr/bin/env python3
# Regression test for the heap out-of-bounds WRITE in recv_file_entry()
# (flist.c): a malicious sender that sets XMIT_HLINKED on a file even though
# the receiver was not invoked with -H. recv_file_entry() reserves the
# hard-link extras slots only under preserve_hard_links, but used to set
# FLAG_HLINKED straight from the peer's xflags. HLINK_BUMP() then offsets
# F_SUM() past the start of the pool_alloc()'d region, and the subsequent
# read_buf(f, F_SUM(file), flist_csum_len) writes attacker-controlled checksum
# bytes below the allocation. The fix gates FLAG_HLINKED on preserve_hard_links.
#
# The shift is HLINK_BUMP()*EXTRA_LEN = (inc_recurse+1)*EXTRA_LEN. With
# inc_recurse OFF the 8-byte shift is absorbed by extra-slot rounding and the
# write stays in bounds; with inc_recurse ON the shift is 16 bytes and the
# write underflows the chunk for real. So this drives the daemon with -r and an
# inc-recurse-capable -e string (the 'i' flag), no -H, and --checksum so the
# receiver reaches the F_SUM read.
#
# The corruption stays inside one 32 KiB pool malloc, so vanilla ASan can't see
# it; lib/pool_alloc.c fences each chunk with a poisoned redzone (under ASan).
# This test therefore needs an ASan build and a real TCP daemon, and is skipped
# otherwise.
#
# Entry: one regular file flagged XMIT_HLINKED|XMIT_HLINK_FIRST (HLINK_FIRST
# makes recv_file_entry skip the gnum read, keeping the stream in sync) plus a
# checksum trailer.
#
# Oracle: pre-fix -> the receiver aborts with an ASan use-after-poison report
# (captured via ASAN_OPTIONS=log_path). fixed -> no report.

import glob
import os

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12953
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
require_asan("the FLAG_HLINKED pool underflow is only observable under "
            "AddressSanitizer + the lib/pool_alloc.c redzone")
claim_ports(PORT)

mod = SCRATCHDIR / 'hlink-oob-mod'
mod.mkdir(parents=True, exist_ok=True)
conf = SCRATCHDIR / 'hlink-oob.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/hlink-oob-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
""")

# Route the daemon's ASan reports to files we can scan (start_rsyncd sends the
# daemon's stderr to /dev/null). The daemon inherits this env.
asan_log = SCRATCHDIR / 'hlink-oob-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

s = rp.DaemonSender('127.0.0.1', PORT)
# -r + 'i' => inc_recurse on the daemon (HLINK_BUMP == 2); --checksum =>
# always_checksum; NO -H => preserve_hard_links off.
s.handshake('mod', ['--server', '-re.iLsfxCIu', '--checksum', '.', 'mod/'],
            greeting_version=30)
evil = rp.FileEntry('h.txt', mode=rp.S_IFREG | 0o644, length=0,
                    extra_flags=rp.XMIT_HLINKED | rp.XMIT_HLINK_FIRST,
                    csum=b'\x41' * 64)
s.send_flat_flist([evil])
s.drain(timeout=3.0)
s.close()

reports = glob.glob(f"{asan_log}.*")
text = ''.join(open(r, errors='replace').read() for r in reports)
if 'AddressSanitizer' in text:
    test_fail(
        "receiver hit an AddressSanitizer error parsing an XMIT_HLINKED entry "
        "sent without -H -- the FLAG_HLINKED pool underflow is unguarded:\n"
        + text[:1500])

print("proto-hlink-flag-oob: XMIT_HLINKED-without-H entry parsed with no "
      "pool underflow (FLAG_HLINKED gated on preserve_hard_links).")
