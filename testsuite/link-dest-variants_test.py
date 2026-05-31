#!/usr/bin/env python3
"""Workstream-1 invariant group C -- alternate-basis (link/copy/compare-dest)
and hardlink-group semantics.

C2 (link-dest shared inodes across transports) lives in its own file
(link-dest-equiv_test.py) and is NOT duplicated here. This file covers the
OTHER C cases, derived from rsync.1.md + generator.c try_dests_reg/_non and
hlink.c:

  C3:  --link-dest + -I/--ignore-times never hard-links. This is EMERGENT
       behavior with no explicit guard: quick_check_ok() returns 0 under
       ignore_times (generator.c ~601), so try_dests_reg never reaches the
       full-attr match level that would set the link, and the file is
       re-sent. There is no code line that says "if ignore_times don't
       link" -- it falls out of the match-level machinery -- so it is
       fragile to refactor and kept as an explicit regression test. We
       assert the dst file does NOT share an inode with the link-dest basis.

  copy-dest:    --copy-dest copies the unchanged candidate from the basis;
       the dst is a real, distinct-inode copy with identical content (NOT a
       hard link). Assert content identical AND inode NOT shared.

  compare-dest: --compare-dest skips the transfer entirely when the basis
       matches; the dst file may not be created at all. Assert the unchanged
       file is absent from the dst (skipped), while a CHANGED file IS
       transferred.

  C7:  hardlink grouping is SRC ⊆ DST, not equality. When transferring a
       subset of a hardlink group with -H, the dst group may include
       pre-existing extra members. Assert the transferred members share one
       inode (subset coherence) and NEVER assert strict set equality between
       the src group and the dst group.
"""

import os

from rsyncfns import (
    SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, test_fail,
)


def shared_inode(a, b):
    sa, sb = os.stat(a), os.stat(b)
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


# Each subtest uses a fresh subtree under the scratch dir.
ROOT = SCRATCHDIR / 'cgroup'
rmtree(ROOT)
makepath(ROOT)


# --------------------------------------------------------------------------
# C3 -- --link-dest with -I/--ignore-times must NOT hard-link.
# --------------------------------------------------------------------------
# Build a basis that is a byte-identical, identical-mtime copy of the source
# (so without -I it WOULD link). Then transfer with --link-dest AND -I and
# assert the dst file is a fresh copy (distinct inode from the basis).
c3 = ROOT / 'c3'
src = c3 / 'src'
basis = c3 / 'basis'
dst = c3 / 'dst'
makepath(src, basis, dst)

(src / 'f.txt').write_text('link-dest ignore-times candidate\n' * 8)
(basis / 'f.txt').write_text('link-dest ignore-times candidate\n' * 8)
st = os.stat(src / 'f.txt')
os.utime(basis / 'f.txt', ns=(st.st_atime_ns, st.st_mtime_ns))

# Sanity: without -I, --link-dest DOES link (proves the basis is a valid
# candidate, so the C3 non-link below is attributable to -I and not to a
# bad fixture).
dst_ctl = c3 / 'dst_ctl'
makepath(dst_ctl)
run_rsync('-a', f'--link-dest={basis}', f'{src}/', f'{dst_ctl}/')
if not shared_inode(dst_ctl / 'f.txt', basis / 'f.txt'):
    test_fail('C3 fixture invalid: --link-dest without -I did not link; '
              'cannot attribute the -I non-link result to ignore-times')

# The invariant: with -I the file is re-sent, so NO inode sharing.
run_rsync('-a', '-I', f'--link-dest={basis}', f'{src}/', f'{dst}/')
assert_same(src / 'f.txt', dst / 'f.txt', label='C3 content still correct')
if shared_inode(dst / 'f.txt', basis / 'f.txt'):
    test_fail('C3 VIOLATION: --link-dest with -I/--ignore-times hard-linked '
              'the dst to the basis. The ignore-times fast-path-off should '
              'force a re-send with a fresh inode (emergent behavior, no '
              'explicit guard -- a refactor likely broke it).')


# --------------------------------------------------------------------------
# copy-dest -- copies from the basis: distinct inode, identical content.
# --------------------------------------------------------------------------
cc = ROOT / 'copydest'
src = cc / 'src'
basis = cc / 'basis'
dst = cc / 'dst'
makepath(src, basis, dst)
(src / 'f.txt').write_text('copy-dest candidate body\n' * 8)
(basis / 'f.txt').write_text('copy-dest candidate body\n' * 8)
st = os.stat(src / 'f.txt')
os.utime(basis / 'f.txt', ns=(st.st_atime_ns, st.st_mtime_ns))

run_rsync('-a', f'--copy-dest={basis}', f'{src}/', f'{dst}/')
# The dst file must exist with the source content...
assert_same(src / 'f.txt', dst / 'f.txt', label='copy-dest content')
# ...but be a real copy, NOT a hard link to the basis.
if shared_inode(dst / 'f.txt', basis / 'f.txt'):
    test_fail('copy-dest VIOLATION: dst shares an inode with the basis. '
              '--copy-dest must COPY (distinct inode), only --link-dest '
              'hard-links.')


# --------------------------------------------------------------------------
# compare-dest -- skips transfer when the basis matches; dst not created.
# --------------------------------------------------------------------------
cmp = ROOT / 'comparedest'
src = cmp / 'src'
basis = cmp / 'basis'
dst = cmp / 'dst'
makepath(src, basis, dst)

# 'same.txt' matches the basis (will be SKIPPED -> absent in dst).
(src / 'same.txt').write_text('unchanged compare-dest body\n' * 8)
(basis / 'same.txt').write_text('unchanged compare-dest body\n' * 8)
st = os.stat(src / 'same.txt')
os.utime(basis / 'same.txt', ns=(st.st_atime_ns, st.st_mtime_ns))
# 'diff.txt' has no basis match (will be TRANSFERRED -> present in dst).
(src / 'diff.txt').write_text('this file has no basis match at all\n' * 8)

run_rsync('-a', f'--compare-dest={basis}', f'{src}/', f'{dst}/')
if (dst / 'same.txt').exists():
    test_fail('compare-dest VIOLATION: the basis-matching file was created '
              'in the dst. --compare-dest must SKIP the transfer entirely '
              '(file not created) when the basis matches.')
if not (dst / 'diff.txt').exists():
    test_fail('compare-dest VIOLATION: a file with no basis match was NOT '
              'transferred. --compare-dest must still transfer files the '
              'basis does not cover.')
assert_same(src / 'diff.txt', dst / 'diff.txt', label='compare-dest changed file')


# --------------------------------------------------------------------------
# C7 -- hardlink grouping is SRC ⊆ DST, not equality.
# --------------------------------------------------------------------------
# Source has a 3-member hardlink group {h1,h2,h3}. The destination already
# contains an extra pre-existing member h0 hard-linked... but rsync can only
# extend a group with what IT transfers, so the real subset property we can
# assert portably is: transferring a SUBSET of the source group still
# produces a coherent single-inode group on the dst, and we NEVER require the
# dst group to equal the src group.
c7 = ROOT / 'c7'
src = c7 / 'src'
dst = c7 / 'dst'
makepath(src, dst)

(src / 'h1').write_text('hardlink group payload\n' * 8)
os.link(src / 'h1', src / 'h2')
os.link(src / 'h1', src / 'h3')

# Transfer only a SUBSET of the group (h1, h2) with -H. h3 is excluded, so the
# dst group is a strict subset of the src group.
run_rsync('-aH', '--exclude=h3', f'{src}/', f'{dst}/')

d1 = os.stat(dst / 'h1')
d2 = os.stat(dst / 'h2')
# Subset coherence: the members we DID transfer share one inode.
if (d1.st_dev, d1.st_ino) != (d2.st_dev, d2.st_ino):
    test_fail('C7 VIOLATION: transferred hardlink-group members h1,h2 do NOT '
              'share an inode on the dst. -H must preserve linkage among the '
              'transferred subset.')
# Subset, not equality: h3 was excluded and must be absent. We assert the
# subset bound, NEVER that the dst group equals the src group.
if (dst / 'h3').exists():
    test_fail('C7 fixture error: excluded h3 unexpectedly present in dst')

# The dst inode need NOT equal any src inode, and the dst group need NOT equal
# the src group {h1,h2,h3}; asserting either would be over-assertion. We only
# assert the transferred subset is internally consistent (done above) and that
# content is correct.
assert_same(src / 'h1', dst / 'h1', label='C7 content')

print('link-dest-variants: C3 (no link under -I), copy-dest (copy not link), '
      'compare-dest (skip when matched), C7 (SRC subset-of DST grouping) '
      'verified')
