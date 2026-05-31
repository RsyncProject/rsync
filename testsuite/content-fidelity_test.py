#!/usr/bin/env python3
"""Workstream-1 invariant group A -- content fidelity.

These tests assert properties the spec + source promise INDEPENDENTLY of any
known bug:

  A1: after a transfer, the destination bytes are exactly the source bytes.
      receiver.c receive_data() (~478-483) computes a whole-file checksum and
      returns 0 (failure) on mismatch, so a completed transfer that rsync
      reports as successful must be byte-correct. We assert byte-correctness
      of the result ONLY -- never a specific digest algorithm, whose strength
      is protocol-dependent.

  A2: quick-check semantics. generator.c quick_check_ok() (~623-646):
        - default (size+mtime): a dest with matching size+mtime is SKIPPED
          even if its content differs;
        - --size-only: matching size alone SKIPS;
        - -c/--checksum (always_checksum): content is compared, so a same
          size+mtime-but-different-content dest is RE-SENT;
        - -I/--ignore-times: the mtime fast-path is forced off, so the file
          is RE-SENT regardless of size+mtime.
      We assert each documented behavior by checking whether the dest content
      was (or was not) overwritten with the source content.
"""

import os

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_same, make_data_file, makepath, rmtree, run_rsync, test_fail,
)


# --------------------------------------------------------------------------
# A1 -- post-transfer whole-file content is byte-correct.
# --------------------------------------------------------------------------
# A mix of sizes (including 0 and a multi-block file) and both whole-file and
# delta paths. For the delta path we seed the destination with a slightly
# different prior version so rsync must reconstruct via block matching, then
# verify the reconstructed bytes equal the source exactly.

rmtree(FROMDIR)
rmtree(TODIR)
makepath(FROMDIR)

sizes = {
    'empty': 0,
    'tiny': 3,
    'oneblock': 700,
    'multiblock': 300000,
}
for name, sz in sizes.items():
    make_data_file(FROMDIR / name, sz)

# Whole-file transfer (-W forces no delta): every dest byte must equal source.
run_rsync('-aW', f'{FROMDIR}/', f'{TODIR}/')
for name in sizes:
    assert_same(FROMDIR / name, TODIR / name, label=f'A1 whole-file {name}')

# Delta transfer: pre-seed the dest with a perturbed copy of the multiblock
# file, then change the source and re-sync WITHOUT -W so the delta algorithm
# runs. The reconstructed file must still be byte-identical to the new source.
rmtree(TODIR)
makepath(TODIR)
# Seed dest from an OLDER source content.
make_data_file(TODIR / 'multiblock', 300000)
# Now make the real source a fresh, different multiblock file.
make_data_file(FROMDIR / 'multiblock', 305000)
run_rsync('-a', '--no-whole-file', f'{FROMDIR}/multiblock', f'{TODIR}/multiblock')
assert_same(FROMDIR / 'multiblock', TODIR / 'multiblock',
            label='A1 delta reconstruct')


# --------------------------------------------------------------------------
# A2 -- quick-check semantics across default / --size-only / -c / -I.
# --------------------------------------------------------------------------
# Build a destination file that has IDENTICAL size and mtime to the source but
# DIFFERENT content. quick_check_ok() must skip it by default and under
# --size-only, but re-send it under -c and under -I.

A2 = SCRATCHDIR / 'a2'


def _setup_same_size_mtime_diff_content():
    """Create src/f and dst/f: same size, same mtime, different bytes."""
    rmtree(A2)
    src = A2 / 'src'
    dst = A2 / 'dst'
    makepath(src, dst)
    # Same length, different content. Fixed bytes (not urandom) so the two
    # differ deterministically while sharing an exact size.
    (src / 'f').write_bytes(b'A' * 4096)
    (dst / 'f').write_bytes(b'B' * 4096)
    # Force identical mtime (and atime) on both, to the nanosecond.
    st = os.stat(src / 'f')
    os.utime(dst / 'f', ns=(st.st_atime_ns, st.st_mtime_ns))
    # Sanity: sizes equal, contents differ, mtimes equal.
    assert os.stat(src / 'f').st_size == os.stat(dst / 'f').st_size
    if (src / 'f').read_bytes() == (dst / 'f').read_bytes():
        test_fail('A2 setup: src and dst content unexpectedly equal')
    return src, dst


def _dst_matches_src(src, dst) -> bool:
    return (src / 'f').read_bytes() == (dst / 'f').read_bytes()


# Default quick-check: size+mtime match -> SKIP -> dst keeps its OWN content.
src, dst = _setup_same_size_mtime_diff_content()
run_rsync('-a', f'{src}/', f'{dst}/')
if _dst_matches_src(src, dst):
    test_fail('A2 default: dest with same size+mtime+different content was '
              're-sent, but quick-check should have SKIPPED it')

# --size-only: size match alone -> SKIP -> dst keeps its own content.
src, dst = _setup_same_size_mtime_diff_content()
run_rsync('-a', '--size-only', f'{src}/', f'{dst}/')
if _dst_matches_src(src, dst):
    test_fail('A2 --size-only: dest with same size was re-sent, but '
              '--size-only should have SKIPPED it')

# -c / --checksum: content is compared -> mismatch -> RE-SEND -> dst == src.
src, dst = _setup_same_size_mtime_diff_content()
run_rsync('-ac', f'{src}/', f'{dst}/')
if not _dst_matches_src(src, dst):
    test_fail('A2 -c: dest with same size+mtime but different content was '
              'NOT re-sent, but --checksum must compare content and re-send')
assert_same(src / 'f', dst / 'f', label='A2 -c result')

# -I / --ignore-times: mtime fast-path forced off -> RE-SEND -> dst == src.
src, dst = _setup_same_size_mtime_diff_content()
run_rsync('-aI', f'{src}/', f'{dst}/')
if not _dst_matches_src(src, dst):
    test_fail('A2 -I: dest with same size+mtime but different content was '
              'NOT re-sent, but --ignore-times must force a re-send')
assert_same(src / 'f', dst / 'f', label='A2 -I result')

print('content-fidelity: A1 byte-correctness + A2 quick-check semantics '
      'verified')
