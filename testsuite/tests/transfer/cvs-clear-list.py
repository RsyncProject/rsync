#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, TODIR,
    assert_exists, makepath, rmtree, run_rsync,
)

src = FROMDIR
rmtree(src)
rmtree(TODIR)
makepath(src)

(src / 'keep1.txt').write_text('one\n')
(src / 'keep2.txt').write_text('two\n')

(src / '.cvsignore').write_text('!\n')

run_rsync('-a', '-C', f'{src}/', f'{TODIR}/')

assert_exists(TODIR / 'keep1.txt', label='clear-list .cvsignore kept keep1')
assert_exists(TODIR / 'keep2.txt', label='clear-list .cvsignore kept keep2')

print("cvs-exclude .cvsignore '!' clear-list token no longer aborts")
