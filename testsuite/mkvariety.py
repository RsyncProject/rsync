#!/usr/bin/env python3
"""Generate a "variety tree" for manual inspection and ad-hoc rsync testing.

Builds exactly the tree that rsyncfns.make_variety_tree() produces for the
regression suite (every inode type rsync handles, with heavy symlink, perm,
xattr, ACL, hard-link and -- as root -- ownership coverage), so you can drive
rsync at it by hand. Transfer the transfer_root/ subdir; the escape/ links
deliberately point up into the sibling above/ tree.

Examples:
    testsuite/mkvariety.py /tmp/vt
    testsuite/mkvariety.py /tmp/vt --list
    sudo testsuite/mkvariety.py /tmp/vt          # adds device nodes + owners
    testsuite/mkvariety.py /tmp/vt --no-acls --depth 4

    rsync -aHAX --specials --devices /tmp/vt/transfer_root/ /tmp/copy/
    rsync -a --safe-links            /tmp/vt/transfer_root/ /tmp/copy2/
"""

import argparse
import os
import stat
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_rsync(opt):
    """Find an rsync binary to probe xattr/ACL capability with."""
    if opt:
        return os.path.abspath(opt)
    cand = os.path.join(os.path.dirname(_HERE), 'rsync')   # build-tree ./rsync
    if os.path.exists(cand):
        return cand
    from shutil import which
    return which('rsync') or 'rsync'


def _detect(flag, prober, default):
    if flag is not None:
        return flag
    try:
        return prober()
    except Exception:
        return default


def list_tree(root):
    rootp = Path(root)
    for dp, dns, fns in os.walk(root):       # followlinks=False
        dns.sort()
        for name in sorted(dns + fns):
            p = Path(dp) / name
            m = p.lstat().st_mode
            tgt = ' -> ' + os.readlink(p) if stat.S_ISLNK(m) else ''
            print(f"{stat.filemode(m)} {p.relative_to(rootp)}{tgt}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='directory to create (REMOVED first if it exists)')
    ap.add_argument('--depth', type=int, default=8, help='backbone depth (default 8)')
    ap.add_argument('--seed', default='0x5A17',
                    help='int seed for the fixed choices (default 0x5A17)')
    ap.add_argument('--rsync', default=None,
                    help='rsync binary for xattr/ACL capability probes '
                         '(default: ./rsync in the build tree, else PATH rsync)')
    ap.add_argument('--list', action='store_true',
                    help='print a recursive listing of the tree afterwards')
    for cap, helptext in (('xattrs', 'user xattrs'), ('acls', 'POSIX ACLs'),
                          ('devices', 'char/block device nodes (needs root)'),
                          ('owners', 'mixed uid/gid (needs root)')):
        g = ap.add_mutually_exclusive_group()
        g.add_argument(f'--{cap}', dest=cap, action='store_true', default=None,
                       help=f'force {helptext} on')
        g.add_argument(f'--no-{cap}', dest=cap, action='store_false',
                       help=f'force {helptext} off')
    args = ap.parse_args()

    target = Path(args.target).resolve()
    for bad in (Path('/'), Path.home(), Path.cwd()):
        if target == bad:
            ap.error(f"refusing to use {target} as the target (it is wiped first)")

    rsync = _resolve_rsync(args.rsync)

    # rsyncfns reads these at import time; set them before importing it.
    os.environ.setdefault('scratchdir', str(target.parent))
    os.environ.setdefault('srcdir', os.getcwd())
    os.environ.setdefault('TOOLDIR', os.path.dirname(rsync) or os.getcwd())
    os.environ.setdefault('RSYNC', rsync)
    sys.path.insert(0, _HERE)
    import rsyncfns as R

    caps = dict(
        with_xattrs=_detect(args.xattrs, R.xattrs_supported, False),
        with_acls=_detect(args.acls, R.acls_supported, False),
        with_devices=args.devices if args.devices is not None
        else R.devices_supported(),
        with_owners=args.owners if args.owners is not None
        else R.owners_supported(),
    )

    info = R.make_variety_tree(target, depth=args.depth,
                               seed=int(args.seed, 0), **caps)

    tr = info['transfer_root']
    print(f"created {sum(info['counts'].values())} entries under {target}")
    print(f"  counts: {info['counts']}")
    print(f"  caps:   xattrs={caps['with_xattrs']} acls={caps['with_acls']} "
          f"devices={caps['with_devices']} owners={caps['with_owners']}")
    if not caps['with_devices'] or not caps['with_owners']:
        print("  (run as root to add device nodes and mixed ownership)")
    print(f"\ntransfer this:  {tr}/")
    print(f"e.g.  rsync -aHAX --specials --devices {tr}/ /tmp/copy/")

    if args.list:
        print()
        list_tree(target)


if __name__ == '__main__':
    main()
