import os
from pathlib import Path
import shutil

from harness.comparison import rsync_ls_lR
from harness import environment
from harness.filesystem import (
    acl_set, acls_supported, devices_supported, make_device, make_fifo, make_socket, make_text_file,
    owners_supported, rmtree, walk_dirs, walk_files,
)
from harness.xattrs import set_value as xattr_set, is_supported as xattrs_supported

def build_symlinks() -> None:
    environment.FROMDIR.mkdir(parents=True, exist_ok=True)
    (environment.FROMDIR / 'referent').write_text(
        'symlink fixture\n'
    )
    os.symlink('referent', environment.FROMDIR / 'relative')
    os.symlink(str(environment.FROMDIR / 'referent'), environment.FROMDIR / 'absolute')
    os.symlink('nonexistent', environment.FROMDIR / 'dangling')
    os.symlink(str(environment.SRCDIR / 'rsync.c'), environment.FROMDIR / 'unsafe')

def hands_setup() -> None:
    rmtree(environment.FROMDIR)
    rmtree(environment.TODIR)
    environment.TMPDIR.mkdir(parents=True, exist_ok=True)
    environment.FROMDIR.mkdir(parents=True, exist_ok=True)
    environment.TODIR.mkdir(parents=True, exist_ok=True)

    (environment.FROMDIR / 'empty').touch()
    (environment.FROMDIR / 'emptydir').mkdir(exist_ok=True)

    (environment.FROMDIR / 'filelist').write_text(rsync_ls_lR(environment.SRCDIR))

    (environment.FROMDIR / 'nolf').write_text("This file has no trailing lf")

    old_umask = os.umask(0)
    try:
        os.symlink('nolf', environment.FROMDIR / 'nolf-symlink')
    finally:
        os.umask(old_umask)

    text = bytearray()
    for c in sorted(environment.SRCDIR.glob('*.c')):
        text.extend(c.read_bytes())
    (environment.FROMDIR / 'text').write_bytes(bytes(text))

    (environment.FROMDIR / 'dir').mkdir(exist_ok=True)
    shutil.copy(environment.FROMDIR / 'text', environment.FROMDIR / 'dir')
    (environment.FROMDIR / 'dir' / 'subdir').mkdir(exist_ok=True)
    (environment.FROMDIR / 'dir' / 'subdir' / 'foobar.baz').write_text("some data\n")
    (environment.FROMDIR / 'dir' / 'subdir' / 'subsubdir').mkdir(exist_ok=True)

    make_text_file(environment.FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list', 120)

    (environment.FROMDIR / 'dir' / 'subdir' / 'subsubdir2').mkdir(exist_ok=True)
    make_text_file(environment.FROMDIR / 'dir' / 'subdir' / 'subsubdir2' / 'bin-lt-list', 200)

def _variety_fill(path, size: int, key: str) -> None:
    import hashlib
    buf = bytearray()
    i = 0
    while len(buf) < size:
        buf += hashlib.sha256(f'{key}:{i}'.encode()).digest()
        i += 1
    with open(str(path), 'wb') as f:
        f.write(bytes(buf[:size]))

def _all_entries(root) -> list:
    root = Path(root)
    res = []
    for dp, dns, fns in os.walk(root):
        d = Path(dp)
        res.append(d)
        for n in fns:
            res.append(d / n)
        for n in dns:
            sub = d / n
            if sub.is_symlink():
                res.append(sub)
    return res

def make_variety_tree(root, *, depth: int = 8, with_acls=None, with_xattrs=None,
                      with_devices=None, with_owners=None,
                      seed: int = 0x5A17) -> dict:
    root = Path(root)
    rmtree(root)
    if with_xattrs is None:
        with_xattrs = xattrs_supported()
    if with_acls is None:
        with_acls = acls_supported()
    if with_devices is None:
        with_devices = devices_supported()
    if with_owners is None:
        with_owners = owners_supported()

    root.mkdir(parents=True)
    above = root / 'above'
    above.mkdir()
    troot = root / 'transfer_root'
    troot.mkdir()

    counts = {}
    def bump(t):
        counts[t] = counts.get(t, 0) + 1

    perm_cycle = [0o400, 0o640, 0o644, 0o600, 0o755]

    def reg(p, size, mode=0o644):
        _variety_fill(p, size, f'{seed:x}:{os.path.relpath(p, root)}')
        os.chmod(p, mode)
        bump('file')
        return p

    def mkdir1(p, mode=None):
        p.mkdir()
        if mode is not None:
            os.chmod(p, mode)
        bump('dir')
        return p

    def lnk(target, p):
        os.symlink(target, p)
        bump('symlink')
        return p

    above_targets = {}
    above_targets['dir'] = mkdir1(above / 'a_dir')
    reg(above / 'a_dir' / 'inner', 256)
    above_targets['file'] = reg(above / 'a_file', 8192)
    above_targets['fifo'] = above / 'a_fifo'
    make_fifo(above_targets['fifo'])
    bump('fifo')
    above_targets['sock'] = above / 'a_sock'
    make_socket(above_targets['sock'])
    bump('socket')
    if with_devices:
        above_targets['dev'] = above / 'a_dev_c'
        make_device(above_targets['dev'], 'c', 1, 3)
        bump('device')
        make_device(above / 'a_dev_b', 'b', 7, 0)
        bump('device')
    above_targets['link'] = lnk('a_file', above / 'a_link')

    cur = troot
    for n in range(depth):
        reg(cur / f'f{n}', 1024 * (n + 1))
        if n % 2 == 0:
            os.link(cur / f'f{n}', cur / f'hl{n}')
            bump('hardlink')
        reg(cur / f'perm{n}', 700, perm_cycle[(seed + n) % len(perm_cycle)])
        reg(cur / f'setuid{n}', 512, 0o4755)
        mkdir1(cur / f'setgid{n}', 0o2775)
        mkdir1(cur / f'sticky{n}', 0o1777)
        for k in range(3):
            reg(cur / f'g{n}_{k}', 300)
        make_fifo(cur / f'fifo{n}')
        bump('fifo')
        make_socket(cur / f'sk{n}')
        bump('socket')
        if with_devices:
            make_device(cur / f'cdev{n}', 'c', 1, 5)
            bump('device')
            make_device(cur / f'bdev{n}', 'b', 7, n)
            bump('device')

        lnk(f'f{n}', cur / f'ln2file{n}')
        lnk(f'fifo{n}', cur / f'ln2fifo{n}')
        lnk(f'sk{n}', cur / f'ln2sock{n}')
        if with_devices:
            lnk(f'cdev{n}', cur / f'ln2dev{n}')
        lnk(f'ln2file{n}', cur / f'ln2ln{n}')
        lnk(f'nonexistent_{n}', cur / f'dangling{n}')
        if n < depth - 1:
            nxt = mkdir1(cur / f'd{n + 1}')
            lnk(f'd{n + 1}', cur / f'ln2dir{n}')
            cur = nxt

    absd = mkdir1(troot / 'abs_links')
    lnk(str((troot / 'f0').resolve()), absd / 'abs_file')
    lnk(str(above_targets['file'].resolve()), absd / 'abs_above')

    escd = mkdir1(troot / 'escape')
    lnk('../../above/a_dir', escd / 'esc_dir')
    lnk('../../above/a_file', escd / 'esc_file')
    lnk('../../above/a_fifo', escd / 'esc_fifo')
    lnk('../../above/a_sock', escd / 'esc_sock')
    if with_devices:
        lnk('../../above/a_dev_c', escd / 'esc_dev')
    lnk('../../above/a_link', escd / 'esc_link')

    lnk(f'../../../{root.name}/above/a_file', escd / 'esc_deep')

    if with_xattrs:
        for p in walk_dirs(troot) + walk_files(troot):
            try:
                xattr_set('variety', os.path.basename(str(p)), p)
            except OSError:
                pass

    if with_acls:
        dirs = walk_dirs(troot)
        files = walk_files(troot)
        for i, d in enumerate(dirs):
            if i % 3 == 0:
                acl_set('u:0:rwx', d)
            if i % 5 == 0:
                acl_set('d:u:0:rwx', d)
        for i, f in enumerate(files):
            if i % 4 == 0:
                acl_set('g:0:r-x', f)

    entries = sorted(_all_entries(root), key=lambda x: str(x))

    if with_owners:
        idset = [(0, 0), (1, 1), (2, 2)]
        for i, p in enumerate(entries):
            uid, gid = idset[(seed + i) % len(idset)]
            try:
                os.chown(str(p), uid, gid, follow_symlinks=False)
            except OSError:
                pass

    base = 1_000_000_000
    for i, p in enumerate(entries):
        t = base + (i * 7) % 1_000_000
        try:
            os.utime(str(p), (t, t), follow_symlinks=False)
        except (OSError, NotImplementedError, ValueError):
            if not os.path.islink(str(p)):
                try:
                    os.utime(str(p), (t, t))
                except OSError:
                    pass

    return {'transfer_root': troot, 'above_targets': above_targets,
            'counts': counts}
