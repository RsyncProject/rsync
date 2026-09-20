#!/usr/bin/env python3
"""Coverage of -A (acls) at depth.

acls_test.py exercises a shallow tree; this companion sets a distinctive POSIX
ACL on a file AND a directory at every level of a >=3-deep tree and checks that
-A reproduces them (the ACL is applied per entry, on a name whose parent chain
the resolver restructure rewrites).
"""

import os
import shutil
import subprocess

from rsyncfns import (
    FROMDIR, TODIR,
    make_tree, rmtree, run_rsync, test_fail, test_skipped,
    walk_dirs, walk_files,
)

vv = run_rsync('-VV', check=True, capture_output=True).stdout
if '"ACLs": true' not in vv:
    test_skipped("rsync built without ACL support")
if not (shutil.which('setfacl') and shutil.which('getfacl')):
    test_skipped("setfacl/getfacl not available")

src = FROMDIR
rmtree(src)
rmtree(TODIR)
make_tree(src, depth=3)

entries = [p.relative_to(src) for p in (walk_dirs(src) + walk_files(src))]
entries.sort()

# Grant uid 0 an explicit r-x ACL on every entry (valid for a non-root owner to
# set on its own files; needs no extra accounts).
for rel in entries:
    r = subprocess.run(['setfacl', '-m', 'u:0:r-x', str(src / rel)])
    if r.returncode != 0:
        test_skipped("filesystem does not support setting ACLs")


def getfacl(path):
    # Strip the comment header (# file:/# owner:/# group:) so the comparison
    # is path-independent; this getfacl doesn't accept the GNU -c flag.
    out = subprocess.check_output(['getfacl', str(path)], text=True,
                                  stderr=subprocess.DEVNULL)
    return ''.join(ln for ln in out.splitlines(keepends=True)
                   if not ln.startswith('#'))


run_rsync('-aA', f'{src}/', f'{TODIR}/')

for rel in entries:
    want = getfacl(src / rel)
    got = getfacl(TODIR / rel)
    # getfacl renders uid 0 as "root"; accept either spelling.
    if 'user:root:r-x' not in got and 'user:0:r-x' not in got:
        test_fail(f"-A did not reproduce the named-user ACL on {rel}:\n{got}")
    if want != got:
        test_fail(f"-A: ACL of {rel} differs\n--- source ---\n{want}"
                  f"--- dest ---\n{got}")

print("acls-depth: -A reproduced a POSIX ACL on every entry at depth")
