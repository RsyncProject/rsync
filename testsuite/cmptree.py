#!/usr/bin/env python3
"""Compare two directory trees and print any differences.

Reuses rsyncfns.compare_trees(), so it is special-file aware (never
opens a fifo/socket/device as a stream) and reports differences in: the tls
listing (type, mode, owner, size, mtime, symlink target for every inode),
regular-file contents, user xattrs, POSIX ACLs, and hard-link grouping.

Works on any two trees, not just ones built by mkvariety.py. Exit status is 0
when the trees match, 1 when they differ.

Examples:
    testsuite/cmptree.py /tmp/vt/transfer_root /tmp/copy
    testsuite/cmptree.py --no-acls --no-xattrs treeA treeB
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_tooldir(opt):
    """Directory holding the `tls` helper (needed for the listing comparison)."""
    for d in (opt, os.path.dirname(_HERE), os.getcwd()):
        if d and os.path.exists(os.path.join(d, 'tls')):
            return d
    return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('tree_a')
    ap.add_argument('tree_b')
    ap.add_argument('--no-xattrs', dest='xattrs', action='store_false',
                    help='skip user-xattr comparison')
    ap.add_argument('--no-acls', dest='acls', action='store_false',
                    help='skip POSIX ACL comparison')
    ap.add_argument('--tooldir', default=None,
                    help='directory holding the tls helper '
                         '(default: the build tree, else cwd)')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='print nothing; only set the exit status')
    args = ap.parse_args()

    for t in (args.tree_a, args.tree_b):
        if not os.path.isdir(t):
            ap.error(f"not a directory: {t}")

    tooldir = _resolve_tooldir(args.tooldir)
    if tooldir is None:
        ap.error("cannot find the 'tls' helper (needed for the listing "
                 "comparison); build it with `make check-progs` or pass "
                 "--tooldir DIR")

    # rsyncfns reads these at import time; compare_trees only needs
    # TOOLDIR (for tls). scratchdir must be an existing dir but is unused here.
    import tempfile
    os.environ.setdefault('scratchdir', tempfile.gettempdir())
    os.environ.setdefault('srcdir', os.getcwd())
    os.environ.setdefault('TOOLDIR', tooldir)
    os.environ.setdefault('RSYNC', 'rsync')
    sys.path.insert(0, _HERE)
    import rsyncfns as R

    diffs = R.compare_trees(args.tree_a, args.tree_b,
                                    with_acls=args.acls, with_xattrs=args.xattrs)
    if diffs:
        if not args.quiet:
            print(f"trees DIFFER ({len(diffs)} difference(s)):")
            print('\n'.join(diffs))
        return 1
    if not args.quiet:
        print("trees are identical")
    return 0


if __name__ == '__main__':
    sys.exit(main())
