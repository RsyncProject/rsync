#!/usr/bin/env python3
# Regression test for a NULL-pointer deref in hash_search() (match.c): the
# check_want_i adjacent-match optimisation substitutes i = want_i after matching
# sum1 and sum2, but -- unlike the main chain loop and the aligned_i path -- did
# NOT re-check s->sums[want_i].len == l.
#
# A hostile generator pulling from a daemon-as-sender forges a sum header against
# a served regular file: count=2, blength=len+1, remainder=len, s2length=0 (so
# the sum2 memcmp is a no-op), with BOTH block sum1 set to the file's rolling
# checksum get_checksum1(file, len). At offset 0 the chain skips block 0
# (.len=blength=len+1 != l=len) and matches block 1 (.len=remainder=len); then
# check_want_i substitutes want_i=0 (.len=len+1) unchecked. After matched(),
# offset advances to len, k = MIN(blength, len-offset) = 0, map_ptr(...,0)
# returns NULL, and match.c:323 dereferences map[0] -- a remote unauthenticated
# SEGV of the per-connection daemon child against any non-empty module file.
#
# We use a 1-byte file so get_checksum1 is trivial. The fix adds the missing
# `l == s->sums[want_i].len` gate (matching the chain loop / aligned_i path).
#
# Oracle: pre-fix -> the daemon child hits an ASan SEGV report (NULL deref);
# fixed -> the forged want_i substitution is rejected and no report appears.
# Needs an ASan build + a real TCP daemon, and is skipped otherwise.

import glob
import os

from rsyncfns import (
    SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail,
)
import rsync_proto as rp

PORT = 12951
require_tcp("the pure-Python generator needs a real TCP daemon; run with --use-tcp")
require_asan("the hash_search want_i NULL-deref is observed via the daemon's "
            "AddressSanitizer SEGV report")
claim_ports(PORT)

mod = SCRATCHDIR / 'wanti-mod'
mod.mkdir(parents=True, exist_ok=True)
CONTENT = b'A'                       # 1-byte file => trivial rolling checksum
(mod / 'f').write_bytes(CONTENT)
LEN = len(CONTENT)

conf = SCRATCHDIR / 'wanti.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/wanti-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = no
""")

asan_log = SCRATCHDIR / 'wanti-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

c = rp.DaemonClient('127.0.0.1', PORT)
# Pull the single file: we are the receiver/generator, the daemon is the sender.
c.handshake('mod', ['--server', '--sender', '-e.LsfxCIu', '.', 'mod/f'],
            greeting_version=30)
entries = rp.sort_entries(c.recv_flist(preserve_links=False))
ndx = next(i for i, e in enumerate(entries) if e.name == b'f')

# Forged sum header: count=2, blength=len+1, remainder=len, s2length=0, both
# block sum1 = get_checksum1(file). No sum2 bytes follow (s2length=0).
sum1 = rp.get_checksum1(CONTENT)
req = (c.w_ndx(ndx) + rp.w_shortint(rp.ITEM_TRANSFER)
       + rp.w_sum_head(2, LEN + 1, 0, LEN)
       + rp.w_int(sum1) + rp.w_int(sum1))
c.send_data(req)
# Trailing data so the sender's last block-sum read completes (its read-ahead
# wants more than the lone request frame), after which it runs the vulnerable
# match_sums/hash_search.
c.send_data(c.w_ndx(rp.NDX_DONE))
c.drain(timeout=3.0)
c.close()

reports = glob.glob(f"{asan_log}.*")
text = ''.join(open(r, errors='replace').read() for r in reports)
if 'AddressSanitizer' in text:
    test_fail(
        "daemon sender hit an AddressSanitizer error on a forged adjacent-match "
        "sum header -- check_want_i substituted want_i without re-checking its "
        "length, so map_ptr() returned NULL and hash_search dereferenced it:\n"
        + text[:1500])

print("match-want-i-nolen: forged want_i adjacent match is length-checked; no "
      "NULL-deref in hash_search.")
