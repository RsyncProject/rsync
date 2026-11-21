#!/usr/bin/env python3
"""The compiled algorithm list must stay honest about sha256.

sha256 is available only when the build's OpenSSL provides it: both the
transfer list (valid_checksums_items[]) and the daemon auth list
(valid_auth_checksums_items[]) wrap their sha256 entry in
`#ifdef SHA256_DIGEST_LENGTH`.  A build without OpenSSL SHA-256 -- rsync
configured --disable-openssl, or a platform whose CI installs no openssl at
all (the FreeBSD and Solaris jobs) -- must therefore not offer sha256
anywhere, and must refuse a request for it instead of advertising something
it cannot compute.

This test never skips: it asserts the contract that holds on *either* build
shape, so the sha256-absent branch is genuinely exercised wherever such a
build runs, and the sha256-present branch elsewhere.

  * the two lists agree -- sha256 is in both or in neither, since one #ifdef
    governs both;
  * where sha256 is advertised it must actually work;
  * where it is not, asking for it must fail closed with a clear error, and
    ordinary negotiation must still succeed;
  * the rejection path itself works, checked with a name no build has.

checksum-sha256 covers what a working sha256 transfer must get right.
"""

import json
import re

from rsyncfns import (
    FROMDIR, TODIR, assert_same, make_data_file, makepath, rmtree, run_rsync,
    test_fail,
)

vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
checksums = vv.get('checksum_list', [])
auth = vv.get('daemon_auth_list', [])

in_transfer = 'sha256' in checksums
in_auth = 'sha256' in auth

# --- 1. one #ifdef governs both tables, so they cannot disagree -------------

if in_auth and not in_transfer:
    test_fail(
        "this build's OpenSSL provides SHA-256 -- daemon_auth_list offers "
        "sha256 -- but the transfer checksum list does not, so the guarded "
        "sha256 entry is missing from valid_checksums_items[]; --version "
        f"reports checksum_list={checksums} daemon_auth_list={auth}")
if in_transfer and not in_auth:
    test_fail(
        "the transfer checksum list offers sha256 while daemon_auth_list does "
        "not; one #ifdef SHA256_DIGEST_LENGTH governs both entries, so they "
        "cannot legitimately disagree; --version reports "
        f"checksum_list={checksums} daemon_auth_list={auth}")

src, dst = FROMDIR, TODIR
rmtree(src)
makepath(src)
make_data_file(src / 'payload.bin', 40000)


def copy(*extra: str):
    rmtree(dst)
    return run_rsync('-a', '--debug=NSTR', *extra, f'{src}/', f'{dst}/',
                     check=False, capture_output=True)


# --- 2. the rejection path works at all -------------------------------------
# Checked on every build with a name no rsync can ever have, so the assertion
# the sha256-absent branch relies on is itself proven here.

proc = copy('--checksum-choice=nosuchalgo')
if proc.returncode == 0:
    test_fail('--checksum-choice=nosuchalgo was accepted; an unknown '
              'algorithm name must be refused')
if 'unknown checksum name' not in proc.stderr:
    test_fail('--checksum-choice with an unknown name should report "unknown '
              f'checksum name", got: {proc.stderr!r}')

# --- 3. whichever way this build was compiled, it must be consistent --------

if in_transfer:
    proc = copy('--checksum', '--checksum-choice=sha256')
    if proc.returncode != 0:
        test_fail("sha256 is advertised in checksum_list but a transfer "
                  f"asking for it failed: rc={proc.returncode} {proc.stderr}")
    if not re.search(r'checksum: sha256\b', proc.stdout):
        test_fail("sha256 is advertised but --checksum-choice=sha256 did not "
                  f"select it: {proc.stdout!r}")
    assert_same(src / 'payload.bin', dst / 'payload.bin', 'advertised sha256')
    verdict = ('sha256 advertised in both lists and usable')
else:
    proc = copy('--checksum', '--checksum-choice=sha256')
    if proc.returncode == 0:
        test_fail('this build does not advertise sha256, yet '
                  '--checksum-choice=sha256 succeeded; the '
                  '#ifdef SHA256_DIGEST_LENGTH guard is not holding')
    if 'unknown checksum name' not in proc.stderr:
        test_fail('a build without OpenSSL SHA-256 must refuse '
                  '--checksum-choice=sha256 with "unknown checksum name", '
                  f'got: rc={proc.returncode} {proc.stderr!r}')

    # Losing sha256 must not cost the build ordinary negotiation.
    proc = copy()
    if proc.returncode != 0:
        test_fail("a build without sha256 could not complete an ordinary "
                  f"negotiated transfer: rc={proc.returncode} {proc.stderr}")
    assert_same(src / 'payload.bin', dst / 'payload.bin', 'no-sha256 build')
    verdict = ('sha256 absent from both lists, refused when asked for, '
               'negotiation still fine')

print(f"checksum-sha256-absent: {verdict}; unknown names rejected")
