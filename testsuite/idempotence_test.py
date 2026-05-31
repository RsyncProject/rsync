#!/usr/bin/env python3
"""Workstream-1 invariant group E -- idempotence / round-trip.

  E1: a second identical `rsync -a` run transfers nothing. With -i the
      itemized-change output for an already-synced tree must be empty (modulo
      the documented dir-time restamping precision -- a directory line whose
      only change is a sub-second time is not a real transfer). We assert no
      file/content/metadata item is emitted on the second leg.

  E2: sync A->B, then reverse-sync B->A, is a no-op on the second leg -- the
      reverse transfer itemizes nothing and leaves both trees structurally
      equivalent. Diffs are PARTITIONED via equiv_fns.partition_diffs so an
      unprivileged uid/gid mapping is tolerated rather than false-failing.

Both legs reuse equiv_fns.capture_tree/diff_trees/partition_diffs for the
structural comparison rather than reinventing tree-walking.
"""

import os

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    make_tree, rmtree, run_rsync, test_fail,
)
from equiv_fns import capture_tree, diff_trees, partition_diffs


# An itemized line is a "real transfer" unless it is purely a directory whose
# only changed attribute is a sub-second time. rsync emits dir lines like
# "cd+++++++++ d/" on creation and ".d..t...... d/" for a pure time restamp.
# We treat a line as a real change unless it is a '.d' line with no change
# code other than 't' (time) / '.' in the attribute field -- i.e. a dir whose
# content/perms/owner did NOT change. Everything else is a genuine transfer.
def _real_changes(itemized: str):
    real = []
    for line in itemized.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # Itemized change strings are 11 chars (YXcstpoguax) then a space then
        # the name. Anything that isn't an itemized line (warnings, etc.) is
        # conservatively treated as a real change so we never hide a problem.
        if len(line) < 12 or line[11] != ' ':
            real.append(line)
            continue
        code = line[:11]
        update, ftype = code[0], code[1]
        attrs = code[2:]
        # A directory whose only "change" is a timestamp (or nothing) is the
        # documented dir-time restamp, not a transfer. update char is '.' (no
        # data/checksum change) and ftype is 'd'.
        if update == '.' and ftype == 'd':
            non_time = attrs.replace('t', '.').replace('T', '.')
            if set(non_time) <= {'.', '+'}:
                continue  # pure dir-time restamp: tolerated
        real.append(line)
    return real


def _structural_fatal(tree_a, tree_b):
    """diff_trees(tree_a, tree_b), partition it, and return the FATAL strings.

    Beyond equiv_fns.partition_diffs (which tolerates dir-time nsec and the
    unprivileged uid/gid mapping), we also tolerate a SYMLINK's mtime_nsec:
    Linux preserves symlink sub-second mtime via utimensat(AT_SYMLINK_NOFOLLOW),
    but platforms such as macOS use lutimes(3) which has only whole-second
    resolution, so the nanosecond remainder of a symlink's mtime may not survive
    a round-trip on those systems. The whole-second symlink mtime is still
    enforced as strict via the 'mtime' field.

    Membership ('only_in_*') diffs are KEPT fatal: every caller here compares
    two trees that an idempotent/round-trip sync is supposed to have made
    set-identical (FROMDIR vs TODIR after a sync; A before vs after a reverse
    leg; A vs B after a round-trip). A file present in one tree but absent in
    the other is therefore a real divergence -- a dropped or spuriously-created
    entry -- not an artifact of walking two unrelated roots, so it MUST surface.
    """
    diff = diff_trees(tree_a, tree_b)
    sym_nsec = {
        d.path for d in diff['strict']
        if d.field == 'mtime_nsec'
        and tree_a.entries.get(d.path)
        and tree_a.entries[d.path].ftype == 'symlink'
    }
    fatal, _tolerated = partition_diffs(diff)
    out = []
    for m in fatal:
        if any(m.startswith(f'{p}: mtime_nsec ') for p in sym_nsec):
            continue
        out.append(m)
    return out


def _itemized_second_leg(src, dst, *opts):
    """Run rsync twice; return the real-change lines of the SECOND run."""
    run_rsync('-a', *opts, f'{src}/', f'{dst}/')
    out = run_rsync('-ai', *opts, f'{src}/', f'{dst}/',
                    check=True, capture_output=True).stdout
    return _real_changes(out), out


# --------------------------------------------------------------------------
# E1 -- a second identical `rsync -a` run transfers nothing.
# --------------------------------------------------------------------------
# A representative tree: nested dirs, regular files at depth, a hard-link pair,
# and a symlink -- so the idempotence claim covers every entry kind.
rmtree(FROMDIR)
rmtree(TODIR)
make_tree(FROMDIR, depth=3, data=True)
os.link(FROMDIR / 'f0', FROMDIR / 'f0_hl')
os.symlink('f0', FROMDIR / 'sl')

real, raw = _itemized_second_leg(FROMDIR, TODIR, '-H')
if real:
    test_fail('E1: a second identical -aH run transferred items (expected '
              'none beyond tolerated dir-time restamps):\n  '
              + '\n  '.join(real)
              + f'\n--- full itemized output ---\n{raw}')

# Structural confirmation: src and dst trees are equivalent after the no-op
# (partitioned so an unprivileged owner mapping and symlink-time sub-second
# precision are tolerated, not fatal).
fatal = _structural_fatal(capture_tree(FROMDIR), capture_tree(TODIR))
if fatal:
    test_fail('E1: src and dst trees diverge after idempotent sync:\n  '
              + '\n  '.join(fatal))


# --------------------------------------------------------------------------
# E2 -- A->B then reverse B->A is a no-op on the reverse leg.
# --------------------------------------------------------------------------
# Build A, sync to B, then reverse-sync B back to A. The reverse leg must
# itemize nothing (beyond tolerated dir-time restamps) and leave A unchanged.
A = SCRATCHDIR / 'rt_a'
B = SCRATCHDIR / 'rt_b'
rmtree(A)
rmtree(B)
make_tree(A, depth=3, data=True)
os.link(A / 'f0', A / 'f0_hl')
os.symlink('f0', A / 'sl')

# Forward leg: A -> B.
run_rsync('-aH', f'{A}/', f'{B}/')

# Snapshot A before the reverse leg so we can prove the reverse changed nothing.
a_before = capture_tree(A)

# Reverse leg with -i: B -> A must transfer nothing.
out = run_rsync('-aHi', f'{B}/', f'{A}/', check=True,
                capture_output=True).stdout
real = _real_changes(out)
if real:
    test_fail('E2: reverse-sync B->A transferred items (expected a no-op '
              'beyond tolerated dir-time restamps):\n  ' + '\n  '.join(real)
              + f'\n--- full itemized output ---\n{out}')

# A must be byte/metadata-identical before vs after the reverse leg.
fatal = _structural_fatal(a_before, capture_tree(A))
if fatal:
    test_fail('E2: the reverse leg mutated A:\n  ' + '\n  '.join(fatal))

# And A and B must be equivalent (partitioned for owner mapping + precision).
fatal = _structural_fatal(capture_tree(A), capture_tree(B))
if fatal:
    test_fail('E2: A and B diverge after round-trip:\n  ' + '\n  '.join(fatal))

print('idempotence: E1 second -a run is a no-op; E2 A->B->A reverse leg is a '
      'no-op (dir-time precision tolerated, owner mapping partitioned)')
