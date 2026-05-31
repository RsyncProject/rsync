#!/usr/bin/env python3
"""Workstream-1 invariant group F -- transport-equivalence meta-invariants.

The general guard: the SAME scenario produces the SAME final destination tree
across every transport (local / ssh / daemon_pipe / daemon_tcp). This is
transport-blindness: rsync's result must not depend on how the peers are
connected. We drive several representative scenarios through equiv_fns'
run_matrix + assert_equivalent, which structurally diffs the trees and
partitions every difference into fatal (content/mode/mtime/linktarget/inode-
grouping/membership) vs tolerated-when-unprivileged (uid/gid owner mapping,
directory mtime nanoseconds) -- so an unprivileged run never false-fails on
owner mapping.

Cases (all derived from rsync.1.md + compat.c, NOT from a known bug):

  F-general:  a mixed tree (regular files of several sizes, symlinks, nested
              dirs, a hardlink group, varied perms) must land byte/structure
              identical on every transport, including the shared-inode
              partition of the hardlink group.

  F-clamp:    the negotiated protocol is the MINIMUM of the two peers
              (compat.c setup_protocol ~606 clamps to min, not average).
              Forcing --protocol=N (a lower version) must be honored and the
              transfer must still succeed with an equivalent final tree on
              every transport.

  F-negotiate: checksum/compress negotiation is intersection-or-error. We do
              NOT assert a specific digest/compressor (those are version- and
              build-dependent). We assert (a) a valid negotiation succeeds and
              yields a correct tree, and (b) an EMPTY intersection (an
              unsupported forced --compress-choice) ERRORS CLEANLY rather than
              silently mis-transferring.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from rsyncfns import (
    RSYNC, USE_TCP, rsync_argv, test_fail,
)
from equiv_fns import (
    TRANSPORTS, Scenario, assert_equivalent, diff_trees, partition_diffs,
    run_matrix,
)


# Skip the TCP leg cleanly without --use-tcp; local + ssh + daemon_pipe still
# run under plain `make check`.
transports = list(TRANSPORTS)
if not USE_TCP:
    transports = [t for t in transports if t != 'daemon_tcp']


# --------------------------------------------------------------------------
# Shared mixed-tree builder. Run fresh per transport leg (each leg starts
# from identical inputs) so any cross-leg divergence is a real transport
# defect, not a fixture artifact.
# --------------------------------------------------------------------------
# Fixed epoch for every source mtime so each per-leg fixture is byte- AND
# time-identical: that makes any cross-leg mtime/content divergence in the
# RESULT a real transport defect, not an artifact of two wall-clock fixture
# creations. (The link-dest-equiv test shares one fixture for the same
# reason; here setup() must run per leg, so we pin determinism instead.)
FIXED_MTIME = 1_600_000_000  # 2020-09-13, well in the past, sub-second = 0


def _det_bytes(n, seed):
    """Deterministic pseudo-random-looking bytes (LCG); identical every run
    so every transport leg copies the SAME content."""
    out = bytearray(n)
    x = seed & 0xFFFFFFFF
    for i in range(n):
        x = (1103515245 * x + 12345) & 0xFFFFFFFF
        out[i] = (x >> 16) & 0xFF
    return bytes(out)


def build_mixed_tree(work, *, with_symlinks=False):
    """Populate <work>/src with a representative mixed tree.

    Regular files of several sizes (incl. empty + multi-block), nested dirs, a
    3-member hardlink group, and varied perms. Content is deterministic and
    every mtime is pinned to FIXED_MTIME so the fixture is identical across
    legs.

    Symlinks are gated behind ``with_symlinks`` because a daemon MUNGES
    symlink targets by default (``munge symlinks`` -- a documented daemon
    security mapping, prefixing ``/rsyncd-munged/``). That munge makes symlink
    targets legitimately NON-equivalent daemon-vs-local, so symlinks are
    excluded from the all-transport byte-equality matrix and verified
    separately across the non-munging transports (local, ssh).
    """
    src = work / 'src'
    (src / 'nested' / 'deep').mkdir(parents=True)

    (src / 'empty').write_text('')
    (src / 'small.txt').write_text('a small file body\n' * 4)
    (src / 'nested' / 'mid.txt').write_text('mid file in a subdir\n' * 200)
    (src / 'nested' / 'deep' / 'leaf.bin').write_bytes(_det_bytes(40000, 12345))

    # Hardlink group (3 members across two dirs).
    h = src / 'h1'
    h.write_text('hardlink group payload across transports\n' * 8)
    os.link(h, src / 'h2')
    os.link(h, src / 'nested' / 'h3')

    if with_symlinks:
        # A relative (in-tree) and a dangling symlink.
        os.symlink('small.txt', src / 'rel_link')
        os.symlink('does-not-exist', src / 'dangling_link')

    # Varied perms.
    os.chmod(src / 'small.txt', 0o600)
    os.chmod(src / 'nested' / 'mid.txt', 0o644)
    os.chmod(src / 'nested', 0o755)
    # An executable bit somewhere to exercise the perm-preservation path.
    (src / 'run.sh').write_text('#!/bin/sh\necho hi\n')
    os.chmod(src / 'run.sh', 0o755)

    # Pin every mtime (files, dirs, symlinks) to FIXED_MTIME, deepest first so
    # writing a parent's children doesn't re-stamp the parent afterwards.
    paths = []
    for dirpath, dirnames, filenames in os.walk(src):
        for nm in filenames + dirnames:
            paths.append(os.path.join(dirpath, nm))
    paths.append(str(src))
    # Sort by depth descending so children are stamped before parents.
    for p in sorted(paths, key=lambda q: q.count(os.sep), reverse=True):
        os.utime(p, (FIXED_MTIME, FIXED_MTIME), follow_symlinks=False)


# --------------------------------------------------------------------------
# F-general -- mixed tree is transport-blind.
# --------------------------------------------------------------------------
# -aHl preserves perms/times/owner/group + hardlinks + symlinks: the full
# structure must be reproduced identically on every transport.
general = Scenario(
    opts=['-aH'],
    rel_src='src/',
    rel_dst='dst/',
    setup=build_mixed_tree,
)
trees = run_matrix(general, transports=transports)
tolerated = assert_equivalent(trees)
for m in tolerated:
    print(f'F-general tolerated (documented mapping): {m}')

# Symlinks: verified across the NON-munging transports only (local, ssh).
# A daemon munges symlink targets by default, a documented mapping that makes
# them non-equivalent over a daemon; including them in the all-transport
# matrix would false-fail on that documented behavior.
sym_transports = [t for t in transports if t in ('local', 'ssh')]
if len(sym_transports) >= 2:
    sym = Scenario(
        opts=['-aHl'],
        rel_src='src/',
        rel_dst='dst/',
        setup=lambda w: build_mixed_tree(w, with_symlinks=True),
    )
    sym_trees = run_matrix(sym, transports=sym_transports)
    for m in assert_equivalent(sym_trees):
        print(f'F-symlink tolerated (documented mapping): {m}')


# --------------------------------------------------------------------------
# F-clamp -- forcing a lower --protocol is honored and stays transport-blind.
# --------------------------------------------------------------------------
# The negotiated version is min(local, remote); forcing a lower --protocol=N
# on the client must be honored (and the daemon/ssh peer clamps to it) with
# the SAME final tree across transports. We pick a version below the build's
# max but at/above the minimum. 30 is the modern floor with full feature set
# (varint flist, etc.) and is universally supported by current rsync.
FORCE_PROTOCOL = 30

clamp = Scenario(
    opts=['-aH', f'--protocol={FORCE_PROTOCOL}'],
    rel_src='src/',
    rel_dst='dst/',
    setup=build_mixed_tree,
)
clamp_trees = run_matrix(clamp, transports=transports)
clamp_tolerated = assert_equivalent(clamp_trees)
for m in clamp_tolerated:
    print(f'F-clamp tolerated (documented mapping): {m}')

# The clamped result must also match the UN-clamped result's structure (the
# protocol floor must not change the final bytes/structure of this tree).
# Compare each clamp leg against the general local reference.
ref = 'local' if trees.get('local') is not None else next(
    t for t, v in trees.items() if v is not None)
for t, ct in clamp_trees.items():
    if ct is None:
        continue
    d = diff_trees(trees[ref], ct)
    fatal, _tol = partition_diffs(d)
    if fatal:
        test_fail(f'F-clamp: --protocol={FORCE_PROTOCOL} changed the final '
                  f'tree vs the unclamped transfer ({ref} vs clamp/{t}):\n  '
                  + '\n  '.join(fatal))


# --------------------------------------------------------------------------
# F-negotiate -- intersection-or-error, no specific algorithm asserted.
# --------------------------------------------------------------------------
# (a) A valid negotiation succeeds and produces a correct tree. We let rsync
#     negotiate freely (default) -- already covered by F-general -- and ALSO
#     pin a checksum the build is known to support to confirm an explicit
#     valid choice negotiates. We do NOT assert WHICH algorithm wins.
# (b) An empty intersection (an unsupported forced --compress-choice) must
#     ERROR CLEANLY (non-zero exit, no destructive/silent mis-transfer),
#     never silently fall back.

# (b) -- the load-bearing negative case. A bogus compressor name has no
# mutual option, so negotiation must fail with a clean non-zero exit.
negroot = Path(tempfile.mkdtemp(prefix='fneg-', dir=os.environ['scratchdir']))
(negroot / 'src').mkdir()
(negroot / 'src' / 'f.txt').write_text('negotiation payload\n' * 4)
(negroot / 'dst').mkdir()

bogus = subprocess.run(
    rsync_argv('-a', '--compress', '--compress-choice=no-such-algo-xyz',
               f'{negroot}/src/', f'{negroot}/dst/'),
    capture_output=True, text=True,
)
if bogus.returncode == 0:
    test_fail('F-negotiate: an unsupported --compress-choice was accepted '
              '(exit 0). An empty negotiation intersection must error cleanly, '
              'not silently transfer.')
# Clean error: nothing should have been silently transferred under the bogus
# choice (the run aborted before/at negotiation). The dst must be empty.
if (negroot / 'dst' / 'f.txt').exists():
    test_fail('F-negotiate: a file was transferred despite the failed '
              'compress negotiation -- the error was not clean.')

# (a) -- a valid explicit checksum choice negotiates and transfers correctly.
# Discover an algorithm the build actually supports rather than hard-coding
# one (build-dependent: md5/md4/sha1/xxh*/...).
vv = subprocess.run(rsync_argv('--version'), capture_output=True, text=True)
# rsync --version lists "Checksum list:\n    <algos>"; parse the algo line.
algos = []
lines = vv.stdout.splitlines()
for i, ln in enumerate(lines):
    if 'Checksum list' in ln:
        # algorithms are on the following indented line(s)
        for j in range(i + 1, min(i + 3, len(lines))):
            for tok in lines[j].split():
                if tok and not tok.endswith(':'):
                    algos.append(tok)
        break
# Filter to real algorithm tokens (drop stray words).
algos = [a for a in algos if a.replace('-', '').isalnum()]
if not algos:
    # Fall back to the universally-present md5; if even that fails the test
    # will catch it below.
    algos = ['md5']

valid_choice = algos[0]
(negroot / 'dst2').mkdir()
good = subprocess.run(
    rsync_argv('-a', f'--checksum-choice={valid_choice}',
               f'{negroot}/src/', f'{negroot}/dst2/'),
    capture_output=True, text=True,
)
if good.returncode != 0:
    test_fail(f'F-negotiate: a valid --checksum-choice={valid_choice} failed '
              f'to negotiate: {good.stderr}')
if (negroot / 'dst2' / 'f.txt').read_text() != 'negotiation payload\n' * 4:
    test_fail('F-negotiate: valid checksum-choice transfer produced wrong '
              'content.')


legs = ', '.join(sorted(t for t, v in trees.items() if v is not None))
print(f'transport-equiv-meta: F-general + F-clamp (--protocol={FORCE_PROTOCOL}) '
      f'transport-blind across [{legs}]; F-negotiate intersection-or-error '
      f'(bogus compress rejected cleanly, valid checksum={valid_choice} '
      f'negotiated) verified')
