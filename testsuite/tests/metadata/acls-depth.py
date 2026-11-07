#!/usr/bin/env python3

import shutil
import subprocess

from harness.rsync import (
    FROMDIR, TODIR,
    make_tree, rmtree, run_rsync, test_fail, test_skipped,
    walk_dirs, walk_files,
)

vv = run_rsync('-VV', check=True, capture_output=True).stdout
if '"ACLs": true' not in vv:
    test_skipped("rsync built without ACL support", capability='linux_acl')
if not (shutil.which('setfacl') and shutil.which('getfacl')):
    test_skipped("setfacl/getfacl not available", capability='linux_acl')

src = FROMDIR
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3)

entries = [p.relative_to(src) for p in (walk_dirs(src) + walk_files(src))]
entries.sort()

for rel in entries:
    r = subprocess.run(['setfacl', '-m', 'u:0:r-x', str(src / rel)])
    if r.returncode != 0:
        test_skipped("filesystem does not support setting ACLs", capability='linux_acl')

def getfacl(path):
    out = subprocess.check_output(['getfacl', str(path)], text=True,
                                  stderr=subprocess.DEVNULL)
    return ''.join(ln for ln in out.splitlines(keepends=True)
                   if not ln.startswith('#'))

run_rsync('-aA', f'{src}/', f'{TODIR}/')

for rel in entries:
    want = getfacl(src / rel)
    got = getfacl(TODIR / rel)
    if 'user:root:r-x' not in got and 'user:0:r-x' not in got:
        test_fail(f"-A did not reproduce the named-user ACL on {rel}:\n{got}")
    if want != got:
        test_fail(f"-A: ACL of {rel} differs\n--- source ---\n{want}"
                  f"--- dest ---\n{got}")

print("acls-depth: -A reproduced a POSIX ACL on every entry at depth")
