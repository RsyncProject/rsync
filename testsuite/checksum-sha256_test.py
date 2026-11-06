#!/usr/bin/env python3
"""Focused coverage for sha256 as the transfer checksum.

sha256 is in valid_checksums_items[], so --checksum-choice=sha256 (and
automatic negotiation) can select it on a build whose OpenSSL provides
SHA-256.  compress-options already walks *every* advertised algorithm for the
basic "was it selected, did the copy land" property, so this test covers what
is specific to sha256: its digest is 32 bytes, i.e. *longer* than SUM_LENGTH
(16), the legacy MD4/MD5 size the block-checksum paths were written around.

append-shortsum guards the short end of that range (xxh64, 8 bytes, which used
to make the sender die on an over-stated s2length).  This guards the long end,
with the delta algorithm actually engaged (--no-whole-file; a local transfer
defaults to --whole-file and would never compute a block checksum at all) and
on the --append-verify redo path, plus the stealth-change detection that -c
exists for.

Skipped on a build whose OpenSSL lacks SHA-256; checksum-sha256-absent covers
that build shape.
"""

import json
import os
import re

from rsyncfns import (
    FROMDIR, TODIR, assert_same, make_data_file, makepath, rmtree, run_rsync,
    test_fail, test_skipped,
)

vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'sha256' not in vv.get('checksum_list', []):
    test_skipped("sha256 not in this build's checksum list (no OpenSSL SHA-256)")

CHOICE = '--checksum-choice=sha256'
src, dst = FROMDIR, TODIR


def selected_sha256(proc, what: str) -> 'None':
    """Fail unless the run reported sha256 as the checksum it settled on."""
    if not re.search(r'checksum: sha256\b', proc.stdout):
        test_fail(f"{what}: sha256 was not the selected checksum; "
                  f"--debug=NSTR said: {proc.stdout!r}")


# --- 1. whole-file -c transfer at depth -------------------------------------

rmtree(src)
rmtree(dst)
makepath(src / 'a' / 'b' / 'c')
make_data_file(src / 'a' / 'b' / 'c' / 'deep.bin', 120000)
(src / 'a' / 'b' / 'top.txt').write_text('sha256 at depth\n')

proc = run_rsync('-a', '-c', CHOICE, '--debug=NSTR',
                 f'{src}/', f'{dst}/', capture_output=True)
selected_sha256(proc, '-c transfer')
assert_same(src / 'a' / 'b' / 'c' / 'deep.bin', dst / 'a' / 'b' / 'c' / 'deep.bin',
            'sha256 -c transfer at depth')
assert_same(src / 'a' / 'b' / 'top.txt', dst / 'a' / 'b' / 'top.txt',
            'sha256 -c transfer at depth')

# --- 2. stealth change: same size, same mtime, different bytes ---------------
# -c must re-send on the digest alone.  Without a working sha256 comparison the
# quick check would call these identical and the file would silently stay stale.

stealth_src = src / 'a' / 'b' / 'c' / 'deep.bin'
stealth_dst = dst / 'a' / 'b' / 'c' / 'deep.bin'
before = stealth_src.stat()
data = bytearray(stealth_src.read_bytes())
data[len(data) // 2] ^= 0xff          # one flipped bit, size unchanged
stealth_src.write_bytes(bytes(data))
os.utime(stealth_src, (before.st_atime, before.st_mtime))   # mtime unchanged too

if stealth_src.stat().st_size != before.st_size:
    test_fail('test bug: the stealth edit changed the file size')

proc = run_rsync('-a', '-c', CHOICE, '--debug=NSTR',
                 f'{src}/', f'{dst}/', capture_output=True)
selected_sha256(proc, 'stealth-change -c transfer')
assert_same(stealth_src, stealth_dst,
            'sha256 -c did not re-send a same-size same-mtime edit')

# --- 3. delta transfer with the block checksum actually engaged -------------
# --no-whole-file makes the receiver generate block checksums and the sender
# match against them, so the 32-byte digest goes through get_checksum2() and
# the s2length plumbing rather than being skipped by a whole-file copy.

rmtree(src)
rmtree(dst)
makepath(src, dst)
make_data_file(src / 'delta.bin', 400000)
run_rsync('-a', f'{src}/delta.bin', f'{dst}/delta.bin')

data = bytearray((src / 'delta.bin').read_bytes())
data[150000:150100] = bytes(100)      # rewrite a middle chunk, same total size
(src / 'delta.bin').write_bytes(bytes(data))

proc = run_rsync('-a', '-c', '--no-whole-file', CHOICE, '--debug=NSTR',
                 f'{src}/delta.bin', f'{dst}/delta.bin', capture_output=True)
selected_sha256(proc, 'delta transfer')
assert_same(src / 'delta.bin', dst / 'delta.bin',
            'sha256 delta transfer (--no-whole-file)')

# --- 4. --append-verify redo with a >16-byte digest -------------------------
# The dest is a *corrupted* prefix, so --append-verify's check of the existing
# bytes fails and the file is redone with a full checksum -- the same path
# append-shortsum drives with an 8-byte digest, here with a 32-byte one.

make_data_file(src / 'append.bin', 60000)
full = (src / 'append.bin').read_bytes()
bad_prefix = bytearray(full[:30000])
bad_prefix[10000] ^= 0xff
(dst / 'append.bin').write_bytes(bytes(bad_prefix))

proc = run_rsync('-a', '--append-verify', '--no-whole-file', CHOICE,
                 '--debug=NSTR', f'{src}/append.bin', f'{dst}/append.bin',
                 capture_output=True)
selected_sha256(proc, '--append-verify redo')
assert_same(src / 'append.bin', dst / 'append.bin',
            'sha256 --append-verify redo of a corrupted prefix')

print("checksum-sha256: sha256 selected and correct for -c at depth, a "
      "stealth same-size same-mtime edit, a --no-whole-file delta transfer "
      "and an --append-verify redo")
