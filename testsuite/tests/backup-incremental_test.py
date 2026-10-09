#!/usr/bin/env python3
# Normal-operation test for --backup / --backup-dir, modelling the maintainer's
# long-standing incremental rsync backup script.  Each run mirrors SRC -> MIRROR
# with  -a --sparse --exclude-from=FILE --delete --delete-excluded  and
# --backup --backup-dir=IDIR, where IDIR is a rotating incremental dir (wiped at
# the start of each run; two slots).  Effect: the mirror is brought back in sync
# with the source, and every file about to be overwritten or deleted (including
# by --delete-excluded) is first relocated into that run's IDIR at its relative
# path.  Local transfer, runs unprivileged.

import os

from rsyncfns import (
    SCRATCHDIR, assert_not_exists, assert_trees_equal, make_data_file,
    make_text_file, makepath, rmtree, run_rsync, test_fail,
)

base = SCRATCHDIR / 'backup-incremental'
rmtree(base)
base.mkdir()

src = base / 'src'                                  # the "home server" data
backup_root = base / 'backup'                       # BACKUP_DIR
mirror = backup_root / 'home'                       # BACKUP_DIR/$name
incr = backup_root / 'incremental' / 'home'         # BACKUP_DIR/incremental/$name
idir0 = incr / '0'                                  # the two rotating slots
idir1 = incr / '1'
exclude_file = base / 'backup_excludes'
exclude_file.write_text("*.tmp\n")


def make_sparse(path, size=128 * 1024):
    """A sparse file: a head, a hole, then a tail."""
    with open(path, 'wb') as f:
        f.write(b'SPARSE-HEAD\n')
        f.seek(size - 4)
        f.write(b'END\n')


def build_src():
    rmtree(src)
    makepath(src / 'sub')
    make_text_file(src / 'file_a', lines=40)
    make_data_file(src / 'file_b', 8192)
    make_text_file(src / 'sub' / 'file_c', lines=30)
    make_data_file(src / 'sub' / 'file_d', 8192)
    os.symlink('file_a', src / 'link')
    make_sparse(src / 'sparse')


def is_empty(d):
    return not any(d.iterdir())


def backup_run(idir):
    """One incremental backup, mirroring the script's backup_one()."""
    rmtree(idir)            # script: rm -rf $IDIR
    makepath(idir)          # script: mkdir -p $IDIR
    makepath(mirror)        # script: mkdir -p $BACKUP_DIR/$name
    run_rsync('-a', '--sparse', f'--exclude-from={exclude_file}',
              '--delete', '--delete-excluded',
              '--backup', f'--backup-dir={idir}',
              f'{src}/', f'{mirror}/')


# --- Phase 1: initial full backup into slot 0 -------------------------------
build_src()
backup_run(idir0)
assert_trees_equal(src, mirror, label='phase1 mirror')
if not is_empty(idir0):
    test_fail(f"phase1: backup dir {idir0} is not empty after the first run "
              f"(nothing should have been backed up): "
              f"{[p.name for p in idir0.iterdir()]}")

# --- Phase 2: mutate the source, second backup into slot 1 ------------------
# Capture the about-to-change mirror contents (what --backup must preserve).
old_b = (mirror / 'file_b').read_bytes()
old_c = (mirror / 'sub' / 'file_c').read_bytes()

make_data_file(src / 'file_b', 9000)            # overwrite (content changes)
(src / 'sub' / 'file_c').unlink()               # delete from source -> --delete
make_text_file(src / 'newfile', lines=10)       # brand-new file
# file_a is left unchanged on purpose.
stale = b"STALE-EXCLUDED-CONTENT\n"
(mirror / 'stale.tmp').write_bytes(stale)       # excluded file already in mirror

backup_run(idir1)

assert_trees_equal(src, mirror, label='phase2 mirror')

if (idir1 / 'file_b').read_bytes() != old_b:
    test_fail("phase2: overwritten file_b was not backed up with its old content "
              f"into {idir1}/file_b")
if (idir1 / 'sub' / 'file_c').read_bytes() != old_c:
    test_fail("phase2: deleted sub/file_c was not backed up at its relative path "
              f"into {idir1}/sub/file_c")
if not (idir1 / 'stale.tmp').exists() or (idir1 / 'stale.tmp').read_bytes() != stale:
    test_fail("phase2: --delete-excluded removal of stale.tmp was not captured by "
              f"--backup into {idir1}/stale.tmp")
assert_not_exists(idir1 / 'newfile', label='phase2: new file must not be backed up')
assert_not_exists(idir1 / 'file_a', label='phase2: unchanged file must not be backed up')
if not is_empty(idir0):
    test_fail(f"phase2: the previous slot {idir0} was disturbed by this run "
              "(each run's backup dir must be independent)")

# --- Phase 3: no-op third run, rotating back to slot 0 ----------------------
backup_run(idir0)
assert_trees_equal(src, mirror, label='phase3 mirror')
if not is_empty(idir0):
    test_fail(f"phase3: a no-change run backed something up into {idir0}: "
              f"{[p.name for p in idir0.iterdir()]}")

print("incremental --backup/--backup-dir round-trip: mirror tracks source and "
      "each run's backup dir captures exactly the overwritten/deleted files")
