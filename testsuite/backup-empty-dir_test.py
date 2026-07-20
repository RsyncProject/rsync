#!/usr/bin/env python3
# Regression test for issue #842:
#   rsync --backup --backup-dir --delete must preserve empty directories
#   in --backup-dir rather than silently removing them.

import re
import subprocess

from rsyncfns import (
    FROMDIR, TODIR, TMPDIR,
    assert_exists, assert_not_exists,
    makepath, rmtree,
    run_rsync, rsync_argv, test_fail,
)

bakdir = TMPDIR / 'bak'


# ---------------------------------------------------------------------------
# Phase 1: basic reproducer from issue #842.
# src/ is empty; dst/sub/empty_dir/ and dst/sub/file.txt get deleted.
# Both must land in backup-dir, not be silently discarded.
# ---------------------------------------------------------------------------
makepath(FROMDIR, TODIR / 'sub' / 'empty_dir', bakdir)
(TODIR / 'sub' / 'file.txt').write_text('data\n')

proc = subprocess.run(
    rsync_argv('-r', '--delete', '--backup', f'--backup-dir={bakdir}',
               '--info=backup', f'{FROMDIR}/', f'{TODIR}/'),
    capture_output=True, text=True,
)
if proc.returncode != 0:
    test_fail(f'rsync failed (phase 1): {proc.stderr}')
output = proc.stdout + proc.stderr

assert_exists(bakdir / 'sub' / 'file.txt',     label='phase1: file.txt in backup-dir')
assert_exists(bakdir / 'sub' / 'empty_dir',    label='phase1: empty_dir in backup-dir')
if not (bakdir / 'sub' / 'empty_dir').is_dir():
    test_fail('phase1: backup-dir/sub/empty_dir must be a directory')
assert_not_exists(TODIR / 'sub', label='phase1: dst/sub removed after sync')

if not re.search(r'backed up sub/empty_dir to ', output, re.MULTILINE):
    test_fail('phase1: no "backed up sub/empty_dir" log line in rsync output')


# ---------------------------------------------------------------------------
# Phase 2: collision safety (steadytao concern, issue #842).
# File backups create backup_dir/sub/ first. The subsequent attempt to back
# up sub/ itself must not wipe those already-preserved child files.
# ---------------------------------------------------------------------------
rmtree(FROMDIR); rmtree(TODIR); rmtree(bakdir)
makepath(FROMDIR, TODIR / 'sub' / 'empty_dir', bakdir)
(TODIR / 'sub' / 'file1.txt').write_text('file1\n')
(TODIR / 'sub' / 'file2.txt').write_text('file2\n')

run_rsync('-r', '--delete', '--backup', f'--backup-dir={bakdir}',
          f'{FROMDIR}/', f'{TODIR}/')

assert_exists(bakdir / 'sub' / 'file1.txt',  label='phase2: file1.txt survives')
assert_exists(bakdir / 'sub' / 'file2.txt',  label='phase2: file2.txt survives')
assert_exists(bakdir / 'sub' / 'empty_dir',  label='phase2: empty_dir in backup-dir')
assert_not_exists(TODIR / 'sub', label='phase2: dst/sub removed after sync')


# ---------------------------------------------------------------------------
# Phase 3: nested empty directories (a/b/c).
# ---------------------------------------------------------------------------
rmtree(FROMDIR); rmtree(TODIR); rmtree(bakdir)
makepath(FROMDIR, TODIR / 'a' / 'b' / 'c', bakdir)

run_rsync('-r', '--delete', '--backup', f'--backup-dir={bakdir}',
          f'{FROMDIR}/', f'{TODIR}/')

assert_exists(bakdir / 'a' / 'b' / 'c', label='phase3: a/b/c in backup-dir')
if not (bakdir / 'a' / 'b' / 'c').is_dir():
    test_fail('phase3: backup-dir/a/b/c must be a directory')
assert_not_exists(TODIR / 'a', label='phase3: dst/a removed after sync')


# ---------------------------------------------------------------------------
# Phase 4: no regression without --backup-dir.
# Empty dir must still be deleted; no empty_dir~ artefact must appear.
# ---------------------------------------------------------------------------
rmtree(FROMDIR); rmtree(TODIR)
makepath(FROMDIR, TODIR / 'empty_dir')

run_rsync('-r', '--delete', '--backup', f'{FROMDIR}/', f'{TODIR}/')

assert_not_exists(TODIR / 'empty_dir',  label='phase4: empty_dir removed without --backup-dir')
assert_not_exists(TODIR / 'empty_dir~', label='phase4: no empty_dir~ created')


# ---------------------------------------------------------------------------
# Phase 5: --delete-delay variant.
# Deletions are queued and executed after the transfer; the backup path
# in delete_item() must still be reached for empty directories.
# ---------------------------------------------------------------------------
rmtree(FROMDIR); rmtree(TODIR); rmtree(bakdir)
makepath(FROMDIR, TODIR / 'sub' / 'empty_dir', bakdir)
(TODIR / 'sub' / 'file.txt').write_text('data\n')

run_rsync('-r', '--delete-delay', '--backup', f'--backup-dir={bakdir}',
          f'{FROMDIR}/', f'{TODIR}/')

assert_exists(bakdir / 'sub' / 'empty_dir', label='phase5: empty_dir backed up with --delete-delay')
assert_exists(bakdir / 'sub' / 'file.txt',  label='phase5: file.txt backed up with --delete-delay')
assert_not_exists(TODIR / 'sub', label='phase5: dst/sub removed after sync')
