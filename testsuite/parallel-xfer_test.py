#!/usr/bin/env python3
"""Regression coverage for the --parallel (-j) concurrent transfer path.

Exercises multi-file concurrent whole-file and delta transfers against the
serial path, refuses unsupported option combinations, and confirms that -j1
still produces the same result.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_data_file, make_tree, rmtree, rsync_supports,
    run_rsync, test_fail, test_skipped, walk_files, xattr_dump, xattr_set,
    xattrs_supported,
)

if not rsync_supports('--parallel=2'):
    test_skipped("this rsync build has no --parallel support")
else:
    rmtree(FROMDIR)
    rmtree(TODIR)
    dirs, files = make_tree(FROMDIR, depth=3, data=True, data_size=65536)
    big = os.path.join(FROMDIR, 'big.bin')
    make_data_file(big, 2 * 1024 * 1024)
    rels = [p.relative_to(FROMDIR) for p in walk_files(FROMDIR)]

    # 1. Concurrent whole-file transfer of a fresh tree.
    run_rsync('-a', '-j4', f'{FROMDIR}/', f'{TODIR}/')
    for rel in rels:
        assert_same(TODIR / rel, FROMDIR / rel, label=f'fresh {rel}')

    # 2. Idempotent re-run.
    run_rsync('-a', '-j4', f'{FROMDIR}/', f'{TODIR}/')

    # 3. Delta update: modify several files, force delta on, and confirm the
    #    parallel path really did match against the existing basis.
    for rel in rels[:3] + [os.path.relpath(big, FROMDIR)]:
        p = FROMDIR / rel
        data = bytearray(p.read_bytes())
        data[100:108] = b'CHANGED!'
        p.write_bytes(bytes(data))
        os.utime(p, (1_700_000_000, 1_700_000_000))
    body = run_rsync('-a', '-j4', '--no-whole-file', '--stats',
                     f'{FROMDIR}/', f'{TODIR}/', capture_output=True)
    out = (body.stdout or '') + (body.stderr or '')
    if 'Matched data: 0 bytes' in out:
        test_fail("--parallel delta update reported no matched data")
    for rel in rels:
        assert_same(TODIR / rel, FROMDIR / rel, label=f'delta {rel}')

    # 4. Unsupported combinations must be refused, not silently mis-handled.
    for opt in ('--write-devices',):
        proc = run_rsync('-a', '-j4', opt, f'{FROMDIR}/', f'{TODIR}/',
                         check=False, capture_output=True)
        if proc.returncode == 0:
            test_fail(f"-j4 accepted unsupported option {opt}")
        if 'parallel' not in (proc.stderr or ''):
            test_fail(f"-j4 {opt}: expected a --parallel refusal, "
                      f"got {proc.stderr!r}")

    # 4b. Supported receiver-side options: --partial and --sparse transfer
    #     normally, and --backup keeps the previous version around.
    opt_dir = TODIR.parent / 'to_opts'
    rmtree(opt_dir)
    run_rsync('-a', '-j4', '--partial', '--sparse', f'{FROMDIR}/', f'{opt_dir}/')
    for rel in rels:
        assert_same(opt_dir / rel, FROMDIR / rel, label=f'partial/sparse {rel}')

    bks = TODIR.parent / 'bk_src'
    bkd = TODIR.parent / 'bk_dst'
    rmtree(bks)
    rmtree(bkd)
    bks.mkdir(parents=True)
    (bks / 'f').write_text('v1')
    run_rsync('-a', '-j4', f'{bks}/', f'{bkd}/')
    (bks / 'f').write_text('v2')
    os.utime(bks / 'f', (1_700_000_100, 1_700_000_100))
    run_rsync('-a', '-j4', '--backup', f'{bks}/', f'{bkd}/')
    if (bkd / 'f').read_text() != 'v2':
        test_fail("--parallel --backup did not update the destination")
    if not (bkd / 'f~').exists() or (bkd / 'f~').read_text() != 'v1':
        test_fail("--parallel --backup did not keep the previous version")

    # 4c. --partial-dir: an existing partial is used as the delta basis and
    #     consumed once the file is complete.
    pd_src = TODIR.parent / 'pd_src'
    pd_dst = TODIR.parent / 'pd_dst'
    rmtree(pd_src)
    rmtree(pd_dst)
    pd_src.mkdir(parents=True)
    make_data_file(pd_src / 'f', 262144)
    (pd_dst / '.rsync-partial').mkdir(parents=True)
    (pd_dst / '.rsync-partial' / 'f').write_bytes(
        (pd_src / 'f').read_bytes()[:131072])
    run_rsync('-a', '-j4', '--partial-dir=.rsync-partial', '--no-whole-file',
              f'{pd_src}/', f'{pd_dst}/')
    assert_same(pd_dst / 'f', pd_src / 'f', label='partial-dir')
    if (pd_dst / '.rsync-partial').exists():
        test_fail("--parallel --partial-dir left the consumed partial behind")

    # 4d. --delete removes extraneous destination files.
    dd_src = TODIR.parent / 'del_src'
    dd_dst = TODIR.parent / 'del_dst'
    rmtree(dd_src)
    rmtree(dd_dst)
    (dd_src / 'sub').mkdir(parents=True)
    (dd_src / 'f1').write_text('one')
    (dd_src / 'sub' / 'f2').write_text('two')
    (dd_dst / 'sub').mkdir(parents=True)
    (dd_dst / 'stale').write_text('x')
    (dd_dst / 'sub' / 'stale2').write_text('y')
    run_rsync('-a', '-j4', '--delete', f'{dd_src}/', f'{dd_dst}/')
    assert_same(dd_dst / 'f1', dd_src / 'f1', label='delete f1')
    assert_same(dd_dst / 'sub' / 'f2', dd_src / 'sub' / 'f2', label='delete f2')
    if (dd_dst / 'stale').exists() or (dd_dst / 'sub' / 'stale2').exists():
        test_fail("--parallel --delete left extraneous files behind")

    # 4e. -X: extended attributes survive a concurrent transfer, including the
    #     metadata-only update path (no data transfer).
    if xattrs_supported():
        xa_src = TODIR.parent / 'xa_src'
        xa_dst = TODIR.parent / 'xa_dst'
        rmtree(xa_src)
        rmtree(xa_dst)
        xa_src.mkdir(parents=True)
        (xa_src / 'f').write_text('data')
        xattr_set('user.parallel_test', 'v1', xa_src / 'f')
        run_rsync('-a', '-X', '-j4', f'{xa_src}/', f'{xa_dst}/')
        assert_same(xa_dst / 'f', xa_src / 'f', label='xattr content')
        if 'user.parallel_test' not in xattr_dump(xa_dst / 'f'):
            test_fail("--parallel -X did not copy the extended attribute")

        # Update only the xattr: the generator reports ITEM_REPORT_XATTR with
        # no data transfer, which the parent must still apply.
        st = (xa_src / 'f').stat()
        xattr_set('user.parallel_test', 'v2', xa_src / 'f')
        os.utime(xa_src / 'f', (st.st_atime, st.st_mtime))
        run_rsync('-a', '-X', '-j4', f'{xa_src}/', f'{xa_dst}/')
        if 'user.parallel_test="v2"' not in xattr_dump(xa_dst / 'f'):
            test_fail("--parallel -X did not update a metadata-only xattr")

    # 4f. -H: hard links are preserved by a concurrent transfer.
    hl_src = TODIR.parent / 'hl_src'
    hl_dst = TODIR.parent / 'hl_dst'
    rmtree(hl_src)
    rmtree(hl_dst)
    (hl_src / 'd').mkdir(parents=True)
    (hl_src / 'base').write_text('shared')
    os.link(hl_src / 'base', hl_src / 'd' / 'link1')
    os.link(hl_src / 'base', hl_src / 'd' / 'link2')
    run_rsync('-a', '-H', '-j4', f'{hl_src}/', f'{hl_dst}/')
    base_ino = os.stat(hl_dst / 'base').st_ino
    if (base_ino != os.stat(hl_dst / 'd' / 'link1').st_ino
     or base_ino != os.stat(hl_dst / 'd' / 'link2').st_ino):
        test_fail("--parallel -H did not preserve hard links")
    assert_same(hl_dst / 'base', hl_src / 'base', label='hardlink content')

    # 4g. --delay-updates: staged files are moved into place at the end and no
    #     partial dir is left behind.
    du_src = TODIR.parent / 'du_src'
    du_dst = TODIR.parent / 'du_dst'
    rmtree(du_src)
    rmtree(du_dst)
    (du_src / 'sub').mkdir(parents=True)
    make_data_file(du_src / 'f', 131072)
    make_data_file(du_src / 'sub' / 'g', 65536)
    run_rsync('-a', '--delay-updates', '-j4', f'{du_src}/', f'{du_dst}/')
    assert_same(du_dst / 'f', du_src / 'f', label='delay-updates f')
    assert_same(du_dst / 'sub' / 'g', du_src / 'sub' / 'g',
                label='delay-updates g')
    if (du_dst / '.~tmp~').exists():
        test_fail("--parallel --delay-updates left the staging dir behind")

    # 4h. --fuzzy: a renamed near-copy is used as a delta basis.
    fz_src = TODIR.parent / 'fz_src'
    fz_dst = TODIR.parent / 'fz_dst'
    rmtree(fz_src)
    rmtree(fz_dst)
    fz_src.mkdir(parents=True)
    fz_dst.mkdir(parents=True)
    make_data_file(fz_src / 'new', 262144)
    (fz_dst / 'old').write_bytes((fz_src / 'new').read_bytes())
    p = fz_src / 'new'
    d = bytearray(p.read_bytes())
    d[100:108] = b'CHANGED!'
    p.write_bytes(bytes(d))
    os.utime(p, (1_700_000_200, 1_700_000_200))
    run_rsync('-a', '--no-whole-file', '--fuzzy', '-j4',
              f'{fz_src}/', f'{fz_dst}/')
    assert_same(fz_dst / 'new', fz_src / 'new', label='fuzzy')

    # 4i. --link-dest: unchanged files are hard-linked against the basis dir.
    ld_src = TODIR.parent / 'ld_src'
    ld_base = TODIR.parent / 'ld_base'
    ld_dst = TODIR.parent / 'ld_dst'
    rmtree(ld_src)
    rmtree(ld_base)
    rmtree(ld_dst)
    ld_src.mkdir(parents=True)
    ld_base.mkdir(parents=True)
    make_data_file(ld_src / 'a', 131072)
    make_data_file(ld_src / 'b', 131072)
    (ld_base / 'a').write_bytes((ld_src / 'a').read_bytes())
    p = ld_src / 'b'
    d = bytearray(p.read_bytes())
    d[10:16] = b'CHANGE'
    p.write_bytes(bytes(d))
    os.utime(p, (1_700_000_300, 1_700_000_300))
    run_rsync('-a', f'--link-dest={ld_base}', '-j4',
              f'{ld_src}/', f'{ld_dst}/')
    if os.stat(ld_dst / 'a').st_ino != os.stat(ld_base / 'a').st_ino:
        test_fail("--parallel --link-dest did not hard-link an unchanged file")
    assert_same(ld_dst / 'b', ld_src / 'b', label='link-dest b')

    # 4j. --inplace: the destination is updated in place, including a shrink.
    ip_src = TODIR.parent / 'ip_src'
    ip_dst = TODIR.parent / 'ip_dst'
    rmtree(ip_src)
    rmtree(ip_dst)
    ip_src.mkdir(parents=True)
    make_data_file(ip_src / 'f', 262144)
    run_rsync('-a', f'{ip_src}/', f'{ip_dst}/')
    p = ip_src / 'f'
    d = bytearray(p.read_bytes())
    d[100:108] = b'INPLACE!'
    p.write_bytes(bytes(d))
    os.utime(p, (1_700_000_400, 1_700_000_400))
    run_rsync('-a', '--inplace', '-j4', f'{ip_src}/', f'{ip_dst}/')
    assert_same(ip_dst / 'f', ip_src / 'f', label='inplace')
    p.write_bytes(p.read_bytes()[:131072])
    os.utime(p, (1_700_000_500, 1_700_000_500))
    run_rsync('-a', '--inplace', '-j4', f'{ip_src}/', f'{ip_dst}/')
    assert_same(ip_dst / 'f', ip_src / 'f', label='inplace shrink')

    # 4k. --append-verify repairs a corrupted prefix (via the redo phase).
    ap_src = TODIR.parent / 'ap_src'
    ap_dst = TODIR.parent / 'ap_dst'
    rmtree(ap_src)
    rmtree(ap_dst)
    ap_src.mkdir(parents=True)
    ap_dst.mkdir(parents=True)
    make_data_file(ap_src / 'f', 262144)
    prefix = bytearray((ap_src / 'f').read_bytes()[:131072])
    prefix[0:32] = b'\x00' * 32
    (ap_dst / 'f').write_bytes(bytes(prefix))
    run_rsync('-a', '-j4', '--append-verify', '--no-whole-file',
              f'{ap_src}/', f'{ap_dst}/')
    assert_same(ap_dst / 'f', ap_src / 'f', label='append-verify repair')

    # 5. -j1 must still match the concurrent result byte-for-byte.
    d1 = TODIR.parent / 'to_serial'
    rmtree(d1)
    run_rsync('-a', '-j1', '--no-inc-recursive', f'{FROMDIR}/', f'{d1}/')
    for rel in rels:
        assert_same(d1 / rel, TODIR / rel, label=f'j1-vs-j4 {rel}')

    print("parallel: concurrent whole-file + delta transfers; refusals; "
          "-j1 parity")
