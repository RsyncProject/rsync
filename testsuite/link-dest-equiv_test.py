#!/usr/bin/env python3
"""--link-dest transport-equivalence test (regression guard for #915).

Asserts that ``rsync -a --link-dest=BASIS`` hard-links unchanged files into
the destination *identically* across all four transports: local, ssh (via
support/lsh.sh), a pipe-mode rsync:// daemon, and -- under --use-tcp -- a
real loopback-bound rsync:// daemon.

This is the exact regression that shipped as #915: ``--link-dest`` silently
stopped hard-linking across a daemon, so a "incremental" backup over rsyncd
re-copied every byte and shared no inodes with the basis. The bug is a
*silent* divergence: the transfer still succeeds, the tree still verifies
byte-for-byte, only the inode-sharing (and thus disk usage) regresses. A
content/tree diff alone cannot see it; the inode-grouping partition can.

Privilege branch (plan C2): with ``-o`` (preserve owner, implied by ``-a``)
an unprivileged daemon receiver cannot reproduce a foreign owner, which
makes unchanged_attrs() return false and legitimately suppresses the hard
link. Here every file is owned by the test user on both ends, so ``-o`` is
satisfiable and linking MUST happen on every leg; we still partition any
uid/gid difference as a documented mapping rather than a failure when
unprivileged, so the test never false-positives on owner mapping.

Path note: a daemon sanitizes ``--link-dest`` against the module root, so
the basis must be expressed as a module-relative path on the daemon legs
(``/basis``) and as the real on-disk path on the local/ssh legs. Both spell
the same directory; ``extra_opts_for`` carries the per-transport spelling.
"""

import os
import subprocess

import rsyncfns
from rsyncfns import RSYNC, SCRATCHDIR, USE_TCP, rmtree, test_fail
from equiv_fns import (
    LSH, TRANSPORTS, SkipLeg,
    _bin_argv, _join_slash, _start_daemon_for_bin,
    capture_tree, diff_trees, numeric_ids_only, partition_diffs,
    write_daemon_conf,
)

DAEMON_PORT = 12890

# Skip the TCP leg cleanly when no real socket is available, but still run
# local + ssh + daemon_pipe under plain `make check`.
transports = list(TRANSPORTS)
if not USE_TCP:
    transports = [t for t in transports if t != 'daemon_tcp']


def build_fixture():
    """Create ONE shared src/ + basis/ used by every transport leg.

    Sharing a single source is what makes cross-transport mtime/nsec
    comparison meaningful: each leg copies from the *same* bytes and times,
    so any absolute mtime divergence in a dst is a real transport defect, not
    an artifact of two separate fixture creations. The basis is a
    byte-identical, identical-mtime (incl. nsec) copy so every file is a
    valid --link-dest candidate (quick_check_ok size+mtime match).

    Layout (root == the daemon module root):
        <root>/src/{a.txt,b.txt,sub/c.txt}
        <root>/basis/{a.txt,b.txt,sub/c.txt}   (link-dest source)
        <root>/<transport>/                    (each leg's dst, created later)
    """
    root = SCRATCHDIR / 'equiv-fixture'
    rmtree(root)
    src = root / 'src'
    basis = root / 'basis'
    (src / 'sub').mkdir(parents=True)
    (basis / 'sub').mkdir(parents=True)

    def seed(p, text):
        sp = src / p
        sp.write_text(text)
        bp = basis / p
        bp.write_text(text)
        st = os.stat(sp)
        os.utime(bp, ns=(st.st_atime_ns, st.st_mtime_ns))

    seed('a.txt', 'hello world content\n' * 4)
    seed('b.txt', 'second file body here\n' * 4)
    seed('sub/c.txt', 'nested candidate file\n' * 4)
    return root


def run_link_dest_leg(root, transport, *, rsync_bin=None, port=DAEMON_PORT):
    """Run the link-dest scenario over one transport against the shared
    fixture ``root``, returning (dst_tree, basis_tree).

    The destination is ``<root>/<transport>/`` so daemon legs (module rooted
    at ``root``) can reach both the dst and the basis. ``--link-dest`` is
    spelled as the real path for local/ssh and module-relative (``/basis``)
    for the daemon legs, since a daemon sanitizes the option against the
    module root.
    """
    if rsync_bin is None:
        rsync_bin = RSYNC
    if transport == 'daemon_tcp' and not rsyncfns.USE_TCP:
        raise SkipLeg('daemon_tcp needs --use-tcp')

    basis = root / 'basis'
    dst = root / transport
    rmtree(dst)
    dst.mkdir(parents=True)
    src = _join_slash(root, 'src/')
    base_argv = _bin_argv(rsync_bin) + ['-a']

    if transport == 'local':
        argv = base_argv + [f'--link-dest={basis}', src, f'{dst}/']
    elif transport == 'ssh':
        argv = base_argv + ['-e', LSH, f'--rsync-path={rsync_bin}',
                            f'--link-dest={basis}',
                            src, f'localhost:{dst}/']
    elif transport in ('daemon_pipe', 'daemon_tcp'):
        use_tcp = (transport == 'daemon_tcp')
        conf = write_daemon_conf(
            [('equiv', {'path': str(root), 'read only': 'no'})],
            name=f'equiv-{transport}.conf',
        )
        prefix = _start_daemon_for_bin(rsync_bin, conf, port, use_tcp=use_tcp)
        argv = base_argv + ['--link-dest=/basis', src, f'{prefix}equiv/{transport}/']
    else:
        raise ValueError(transport)

    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode not in (0, 23):
        test_fail(f'[{transport}] rsync exited {proc.returncode}: '
                  f'{" ".join(argv)}\n{proc.stderr}')
    return capture_tree(dst), capture_tree(basis)


root = build_fixture()
results = {}
for t in transports:
    try:
        dst_tree, basis_tree = run_link_dest_leg(root, t)
    except SkipLeg as e:
        print(f'[{t}] skipped: {e}')
        continue
    results[t] = (dst_tree, basis_tree)

if not results:
    test_fail('no transport legs ran')

# 1) Tree-structural equivalence across legs (content/mode/mtime/...),
#    partitioning owner mapping. Reference = local if present.
trees = {t: dst for t, (dst, _b) in results.items()}
ref = 'local' if 'local' in trees else next(iter(trees))
ref_tree = trees[ref]
tolerated_all = []
for t, tree in trees.items():
    if t == ref:
        continue
    diff = diff_trees(ref_tree, tree)
    fatal, tolerated = partition_diffs(diff)
    tolerated_all += [f'[{ref} vs {t}] {m}' for m in tolerated]
    if fatal:
        test_fail(f'tree divergence {ref} vs {t}:\n  ' + '\n  '.join(fatal))

# 2) The load-bearing assertion: every leg's destination must SHARE
#    inodes with its basis (SRC ⊆ DST grouping -- each linked file's dst
#    inode equals the basis inode). This is what #915 broke over a daemon.
rel_files = ['a.txt', 'b.txt', 'sub/c.txt']
for t, (dst_tree, basis_tree) in results.items():
    for rel in rel_files:
        d = dst_tree.entries.get(rel)
        b = basis_tree.entries.get(rel)
        if d is None or b is None:
            test_fail(f'[{t}] missing {rel} in dst or basis')
        dst_ino = os.stat(dst_tree.root / rel).st_ino
        dst_dev = os.stat(dst_tree.root / rel).st_dev
        bas_ino = os.stat(basis_tree.root / rel).st_ino
        bas_dev = os.stat(basis_tree.root / rel).st_dev
        shared = (dst_ino, dst_dev) == (bas_ino, bas_dev)
        # C2: a non-root daemon that cannot satisfy -o would legitimately
        # break linking. Here owner is identical on both ends, so the
        # documented mapping holds and linking MUST occur. If it does not,
        # that is exactly the #915 silent divergence -- fail loudly.
        if not shared:
            test_fail(
                f'[{t}] --link-dest did NOT share inode for {rel} '
                f'(dst ino={dst_ino} dev={dst_dev}, basis ino={bas_ino} '
                f'dev={bas_dev}). This is the #915 link-dest-over-'
                f'transport regression: the file was re-copied instead '
                f'of hard-linked. privileged={numeric_ids_only()}'
            )

for m in tolerated_all:
    print(f'tolerated (documented mapping): {m}')
legs = ', '.join(sorted(results))
print(f'link-dest-equiv: shared inodes verified across [{legs}] '
      f'({len(rel_files)} files/leg)')
