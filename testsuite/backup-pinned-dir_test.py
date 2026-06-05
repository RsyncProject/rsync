#!/usr/bin/env python3
# Regression test for the bug in PR #967.
#
# PR #967 makes delete_item() rename a directory into --backup-dir before
# falling back to rmdir, so empty dirs are preserved in the backup tree
# (issue #842).  The rename branch fires whenever DEL_DIR_IS_EMPTY is set,
# but that flag is NOT proof of emptiness: delete_dir_contents() sets it on
# a child even when the child's own content deletion returned DR_NOT_EMPTY
# (a grandchild survived).  do_rmdir_at() was safe there -- it fails with
# ENOTEMPTY -- but do_rename_at() happily moves the whole non-empty subtree
# into the backup-dir.
#
# A "protect" filter (P) pins a file against deletion, leaving its parent
# non-empty.  rsync must leave that file where it is.  With the PR bug, the
# pinned parent gets renamed wholesale into --backup-dir, relocating data
# rsync deliberately chose not to delete.
#
# Correct behaviour (master): the protected file stays in the destination.
# Buggy behaviour (PR #967): the protected file is moved into --backup-dir.

from rsyncfns import (
    FROMDIR, TODIR, TMPDIR,
    assert_exists, assert_not_exists,
    makepath, rmtree, run_rsync, test_fail,
)

bakdir = TMPDIR / 'bak'

# The pinned file must be the ONLY thing in its nested directory, and that
# directory must have no sibling whose own backup would pre-create the
# backup-dir counterpart -- otherwise the rename collides (EEXIST/ENOTEMPTY)
# and harmlessly falls back to rmdir, masking the bug.  'sibling.txt' lives
# one level up so it creates bak/sub/ but not bak/sub/inner/.
rmtree(FROMDIR); rmtree(TODIR); rmtree(bakdir)
makepath(FROMDIR, TODIR / 'sub' / 'inner', bakdir)
(TODIR / 'sub' / 'inner' / 'keep.txt').write_text('keepme\n')
(TODIR / 'sub' / 'sibling.txt').write_text('sib\n')

# src is empty: everything under dst/ is extraneous and would be deleted,
# except sub/inner/keep.txt which the protect filter pins in place.
run_rsync('-r', '--delete', '--backup', f'--backup-dir={bakdir}',
          '--filter=P sub/inner/keep.txt',
          f'{FROMDIR}/', f'{TODIR}/')

# The protected file must remain exactly where it was.
assert_exists(TODIR / 'sub' / 'inner' / 'keep.txt',
              label='protected keep.txt must stay in the destination')

# ...and must NOT have been relocated into the backup-dir.
assert_not_exists(bakdir / 'sub' / 'inner' / 'keep.txt',
                  label='protected keep.txt must NOT be moved into backup-dir')

# Its pinned parent directory must also remain in place.
assert_exists(TODIR / 'sub' / 'inner',
              label='pinned dir sub/inner must stay in the destination')

# The non-pinned sibling should still be backed up normally.
assert_exists(bakdir / 'sub' / 'sibling.txt',
              label='non-pinned sibling.txt backed up as usual')

if not (TODIR / 'sub' / 'inner' / 'keep.txt').is_file():
    test_fail('protected file was relocated by the backup-dir rename (PR #967 bug)')
