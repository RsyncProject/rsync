#!/usr/bin/env python3
"""Coverage of -C / --cvs-exclude at depth.

-C ignores the usual CVS cruft (object files, core, editor backups, VCS dirs,
...) and also honours a per-directory .cvsignore. Verify both the built-in
patterns and a deep .cvsignore on a >=3-level tree.
"""

from rsyncfns import (
    FROMDIR, TODIR,
    assert_exists, assert_not_exists, makepath, rmtree, run_rsync,
)

src = FROMDIR
rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3')

# A real file plus default-CVS-cruft at every level.
cur = src
for lvl in range(4):
    (cur / f'real{lvl}.c').write_text('code\n')
    (cur / f'obj{lvl}.o').write_text('obj\n')        # *.o is built-in cruft
    (cur / f'back{lvl}~').write_text('backup\n')     # *~ is built-in cruft
    cur = cur / f'd{lvl + 1}'

# A per-directory .cvsignore deep in the tree adds "*.junk" for that subtree.
(src / 'd1' / 'd2' / '.cvsignore').write_text('*.junk\n')
(src / 'd1' / 'd2' / 'local.junk').write_text('j\n')
(src / 'top.junk').write_text('j\n')                 # not covered by that .cvsignore

run_rsync('-aC', f'{src}/', f'{TODIR}/')

cur = TODIR
for lvl in range(4):
    assert_exists(cur / f'real{lvl}.c', label=f'-C kept real L{lvl}')
    assert_not_exists(cur / f'obj{lvl}.o', label=f'-C dropped *.o L{lvl}')
    assert_not_exists(cur / f'back{lvl}~', label=f'-C dropped *~ L{lvl}')
    cur = cur / f'd{lvl + 1}'

# .cvsignore is scoped to its directory subtree.
assert_not_exists(TODIR / 'd1' / 'd2' / 'local.junk',
                  label='-C deep .cvsignore applied')
assert_exists(TODIR / 'top.junk', label='-C deep .cvsignore not applied above')

print("cvs-exclude: built-in patterns + deep .cvsignore honoured at depth")
