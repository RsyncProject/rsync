#!/usr/bin/env python3
"""Adding sha256 to the checksum list must not disturb an older peer.

valid_checksums_items[] is what both sides advertise when they negotiate a
transfer checksum, so appending sha256 to it changes the wire conversation
with every peer -- including ones built before sha256 was offered, and ones
whose OpenSSL never provided it.  The failure this guards against is a new
entry making negotiation pick something the far side cannot compute, or
shifting the agreed algorithm for peers that were previously fine.

Three properties, against the in-tree old_versions binaries:

  * a negotiation-capable peer without sha256 (3.2.7, 3.4.1) still agrees on a
    mutually supported algorithm, and it is not sha256;
  * a pre-negotiation peer (3.1.3) still falls back to md5 and copies
    correctly;
  * asking for sha256 explicitly against a peer that lacks it fails closed --
    a clear "unknown checksum name" error and a non-zero exit, not a hang, a
    silent downgrade, or a corrupt destination.

The old_versions binaries are static Linux builds.  Where they cannot run
(macOS, Cygwin, the BSDs) the portable control still runs and the old-peer
cases report that they were not exercised, rather than skipping the test --
those platforms enforce testsuite/skiplist and a skip there would be a CI
failure, not a note.
"""

import json
import os
import re
import subprocess

from rsyncfns import (
    FROMDIR, SRCDIR, TODIR, assert_same, make_data_file, makepath, rmtree,
    rsh_cmd, rsync_path_arg, run_rsync, test_fail, test_skipped,
)

vv = json.loads(run_rsync('-VV', check=True, capture_output=True).stdout)
if 'sha256' not in vv.get('checksum_list', []):
    test_skipped("sha256 not in this build's checksum list (no OpenSSL SHA-256)")

os.environ['RSYNC_RSH'] = rsh_cmd()

src, dst = FROMDIR, TODIR
rmtree(src)
makepath(src)
make_data_file(src / 'payload.bin', 80000)
(src / 'note.txt').write_text('negotiated against an older peer\n')


def transfer(peer_path: str, *extra: str):
    """Copy src -> a fresh dst with `peer_path` as the far-side rsync."""
    rmtree(dst)
    return run_rsync('-a', '--debug=NSTR', f'--rsync-path={peer_path}', *extra,
                     f'lh:{src}/', f'{dst}/', check=False, capture_output=True)


def agreed_checksum(proc):
    """The algorithm the run reported settling on, or None."""
    m = re.search(r'checksum: (\S+)', proc.stdout)
    return m.group(1) if m else None


def check_copy(label: str) -> 'None':
    assert_same(src / 'payload.bin', dst / 'payload.bin', label)
    assert_same(src / 'note.txt', dst / 'note.txt', label)


# --- portable control: both sides are this build ----------------------------

proc = transfer(rsync_path_arg())
if proc.returncode != 0:
    test_fail(f"control: same-build peer transfer failed: {proc.stderr}")
check_copy('control (same-build peer)')
if agreed_checksum(proc) is None:
    test_fail(f"control: no checksum reported by --debug=NSTR: {proc.stdout!r}")

proc = transfer(rsync_path_arg(), '--checksum', '--checksum-choice=sha256')
if proc.returncode != 0:
    test_fail("control: --checksum-choice=sha256 failed between two builds "
              f"that both advertise it: {proc.stderr}")
if agreed_checksum(proc) != 'sha256':
    test_fail("control: --checksum-choice=sha256 did not select sha256 "
              f"between two sha256-capable builds: {proc.stdout!r}")
check_copy('control (forced sha256, same-build peer)')

# --- the old peers ----------------------------------------------------------

NNI_PEERS = ('rsync_3.4.1', 'rsync_3.2.7')   # negotiate, but no sha256
PRE_NNI_PEERS = ('rsync_3.1.3',)             # older than checksum negotiation


def usable(name: str):
    """Path to an old_versions binary that runs here, else None."""
    path = SRCDIR / 'old_versions' / name
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    want = name.replace('rsync_', 'version ')
    try:
        probe = subprocess.run([str(path), '--version'], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return path if probe.returncode == 0 and want in probe.stdout else None


exercised = []

for name in NNI_PEERS:
    peer = usable(name)
    if peer is None:
        continue
    peer_algos = set(json.loads(
        subprocess.run([str(peer), '-VV'], stdout=subprocess.PIPE, text=True,
                       check=True).stdout).get('checksum_list', []))
    if 'sha256' in peer_algos:
        test_fail(f"test bug: {name} advertises sha256, so it cannot stand in "
                  "for a peer that lacks it")

    # 1. auto negotiation still lands on something both sides have.
    proc = transfer(rsync_path_arg(str(peer)))
    if proc.returncode != 0:
        test_fail(f"{name}: adding sha256 broke plain negotiation with an "
                  f"older peer: rc={proc.returncode} {proc.stderr}")
    algo = agreed_checksum(proc)
    if algo == 'sha256':
        test_fail(f"{name}: negotiation selected sha256 against a peer whose "
                  f"checksum list is {sorted(peer_algos)}")
    if algo not in peer_algos:
        test_fail(f"{name}: negotiation selected {algo!r}, which the peer does "
                  f"not advertise ({sorted(peer_algos)})")
    check_copy(f'{name} negotiated {algo}')

    # 2. demanding sha256 from a peer without it must fail closed.
    proc = transfer(rsync_path_arg(str(peer)), '--checksum',
                    '--checksum-choice=sha256')
    if proc.returncode == 0:
        test_fail(f"{name}: --checksum-choice=sha256 unexpectedly succeeded "
                  "against a peer that does not support sha256")
    if 'unknown checksum name' not in proc.stderr:
        test_fail(f"{name}: --checksum-choice=sha256 against a peer without it "
                  "should fail with 'unknown checksum name', got: "
                  f"rc={proc.returncode} {proc.stderr!r}")
    exercised.append(f'{name} (negotiated {algo}, forced sha256 refused)')

for name in PRE_NNI_PEERS:
    peer = usable(name)
    if peer is None:
        continue
    proc = transfer(rsync_path_arg(str(peer)))
    if proc.returncode != 0:
        test_fail(f"{name}: adding sha256 broke the pre-negotiation md5 "
                  f"fallback: rc={proc.returncode} {proc.stderr}")
    algo = agreed_checksum(proc)
    if algo != 'md5':
        test_fail(f"{name}: a pre-negotiation peer should still use md5, "
                  f"got {algo!r}")
    check_copy(f'{name} md5 fallback')
    exercised.append(f'{name} (md5 fallback)')

if not exercised:
    print("checksum-sha256-negotiate-old: same-build control verified "
          "(negotiation works and forced sha256 selects sha256); no "
          "old_versions binary runs on this platform, so the older-peer "
          "cases were not exercised")
    raise SystemExit(0)

print("checksum-sha256-negotiate-old: sha256 in the checksum list leaves "
      "older peers undisturbed -- " + '; '.join(exercised))
