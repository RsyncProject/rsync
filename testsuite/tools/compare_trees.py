#!/usr/bin/env python3

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

def _resolve_tooldir(opt):
    for d in (opt, os.path.dirname(_HERE), os.getcwd()):
        if d and os.path.exists(os.path.join(d, 'tls')):
            return d
    return None

def main():
    ap = argparse.ArgumentParser(description='Compare two directory trees')
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

    import tempfile
    os.environ.setdefault('scratchdir', tempfile.gettempdir())
    os.environ.setdefault('srcdir', os.getcwd())
    os.environ.setdefault('TOOLDIR', tooldir)
    os.environ.setdefault('RSYNC', 'rsync')
    from ..harness import rsync as R

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
