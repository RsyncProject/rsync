#!/usr/bin/env python3

import grp
import os

from harness.rsync import (
    FROMDIR, TODIR,
    get_rootuid, get_testuid, make_tree, rmtree, rsync_getgroups, run_rsync,
    test_fail, test_skipped, walk_dirs, walk_files,
)

src = FROMDIR
is_root = get_testuid() == get_rootuid()
prim = os.getgid()

def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3)
    entries = [p.relative_to(src) for p in (walk_dirs(src) + walk_files(src))]
    for rel in entries:
        if os.stat(src / rel).st_gid != prim:
            os.chown(src / rel, -1, prim)
    return entries

def assert_all(entries, *, gid=None, uid=None, label=''):
    for rel in entries:
        st = os.stat(TODIR / rel)
        if gid is not None and st.st_gid != gid:
            test_fail(f"{label}: group of {rel} is {st.st_gid}, expected {gid}")
        if uid is not None and st.st_uid != uid:
            test_fail(f"{label}: owner of {rel} is {st.st_uid}, expected {uid}")

try:
    grp.getgrgid(prim)
    prim_has_name = True
except KeyError:
    prim_has_name = False

if is_root:
    target_gid = 1 if prim == 0 else 0
    target_uid = 1 if get_testuid() == 0 else 0

    entries = seed()
    run_rsync('-a', f'--groupmap={prim}:{target_gid}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=target_gid, label='--groupmap (root)')

    entries = seed()
    run_rsync('-a', f'--groupmap=*:{target_gid}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=target_gid, label='--groupmap wildcard (root)')

    if prim_has_name:
        entries = seed()
        run_rsync('-a', f'--groupmap=:{target_gid}', f'{src}/', f'{TODIR}/')
        assert_all(entries, gid=prim, label='--groupmap empty named group (root)')

    entries = seed()
    run_rsync('-a', '--numeric-ids', f'--groupmap=:{target_gid}',
              f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=target_gid, label='--groupmap empty nameless group (root)')

    entries = seed()
    run_rsync('-a', f'--chown=:{target_gid}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=target_gid, label='--chown group (root)')

    entries = seed()
    run_rsync('-a', f'--usermap=*:{target_uid}', f'{src}/', f'{TODIR}/')
    assert_all(entries, uid=target_uid, label='--usermap (root)')

    entries = seed()
    run_rsync('-a', f'--chown={target_uid}:{target_gid}', f'{src}/', f'{TODIR}/')
    assert_all(entries, uid=target_uid, gid=target_gid,
               label='--chown user:group (root)')
    print("ownership-depth: --groupmap/--chown/--usermap verified at depth (root)")
else:
    groups = [int(g) for g in rsync_getgroups()]
    secs = [g for g in groups if g != prim]
    if not secs:
        test_skipped("non-root with no secondary group to remap to")
    sec = secs[0]

    entries = seed()
    run_rsync('-a', f'--groupmap={prim}:{sec}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=sec, label='--groupmap')

    entries = seed()
    run_rsync('-a', f'--groupmap=*:{sec}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=sec, label='--groupmap wildcard')

    if prim_has_name:
        entries = seed()
        run_rsync('-a', f'--groupmap=:{sec}', f'{src}/', f'{TODIR}/')
        assert_all(entries, gid=prim, label='--groupmap empty named group')

    entries = seed()
    run_rsync('-a', '--numeric-ids', f'--groupmap=:{sec}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=sec, label='--groupmap empty nameless group')

    entries = seed()
    run_rsync('-a', f'--chown=:{sec}', f'{src}/', f'{TODIR}/')
    assert_all(entries, gid=sec, label='--chown group')
    print("ownership-depth: --groupmap/--chown group remap verified at depth "
          "(-o/--usermap user remap needs root -- skipped)")
