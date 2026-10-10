#!/usr/bin/env python3

import argparse
import os
import stat
import subprocess
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))

def _resolve_rsync(opt):
    if opt:
        return os.path.abspath(opt)
    cand = os.path.join(os.path.dirname(_HERE), 'rsync')
    if os.path.exists(cand):
        return cand
    from shutil import which
    return which('rsync') or 'rsync'

def _detect(flag, prober, default):
    if flag is not None:
        return flag
    try:
        return prober()
    except (OSError, subprocess.SubprocessError):
        return default

def list_tree(root):
    rootp = Path(root)
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for name in sorted(dns + fns):
            p = Path(dp) / name
            m = p.lstat().st_mode
            tgt = ' -> ' + os.readlink(p) if stat.S_ISLNK(m) else ''
            print(f"{stat.filemode(m)} {p.relative_to(rootp)}{tgt}")

def main():
    ap = argparse.ArgumentParser(description='Create an rsync filesystem fixture')
    ap.add_argument('target', help='directory to replace')
    ap.add_argument('--depth', type=int, default=8, help='backbone depth (default 8)')
    ap.add_argument('--seed', default='0x5A17',
                    help='integer seed for the fixed choices (default 0x5A17)')
    ap.add_argument('--rsync', default=None,
                    help='rsync binary for xattr/ACL capability probes '
                         '(default: ./rsync in the build tree, else PATH rsync)')
    ap.add_argument('--list', action='store_true',
                    help='print a recursive listing of the tree afterwards')
    for cap, helptext in (('xattrs', 'user xattrs'), ('acls', 'POSIX ACLs'),
                          ('devices', 'character and block device nodes (needs root)'),
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

    os.environ.setdefault('scratchdir', str(target.parent))
    os.environ.setdefault('srcdir', os.getcwd())
    os.environ.setdefault('TOOLDIR', os.path.dirname(rsync) or os.getcwd())
    os.environ.setdefault('RSYNC', rsync)
    from ..harness import rsync as R

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
    print(f'transfer root: {tr}/')

    if args.list:
        print()
        list_tree(target)

if __name__ == '__main__':
    main()
