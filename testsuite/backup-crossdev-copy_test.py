#!/usr/bin/env python3
"""Coverage: backup.c make_backup() copy-fallback for non-regular files.

make_backup() first tries link_or_rename() -- hard-link, then rename --
into the backup dir.  On Linux that always succeeds for symlinks/FIFOs on
the same filesystem, so the copy-fallback (lines ~261-349: make_file() +
do_symlink_at()/do_mknod_at()/copy_file()) is never reached by the suite.

When --backup-dir is on a DIFFERENT filesystem, both link() and rename()
fail with EXDEV and make_backup() falls through to recreating each item:
do_symlink_at() for symlinks, do_mknod_at() for FIFOs/specials,
copy_file() for regular files.  This test puts the backup dir on tmpfs
(probed via /dev/shm) and asserts each type is backed up correctly.
"""

import os
import shutil
import stat
import subprocess
import tempfile

from rsyncfns import (
    SCRATCHDIR, FROMDIR,
    makepath, rmtree, rsync_argv, test_fail, test_skipped,
)

# Find a writable directory on a different st_dev from SCRATCHDIR (typically
# tmpfs at /dev/shm).  Without one the EXDEV path can't fire -- skip cleanly.
scratch_dev = os.stat(SCRATCHDIR).st_dev
TMPFS = None
for cand in ('/dev/shm', '/run/shm', os.environ.get('TMPDIR', '/tmp')):
    try:
        if os.stat(cand).st_dev != scratch_dev and os.access(cand, os.W_OK):
            TMPFS = cand
            break
    except OSError:
        continue
if TMPFS is None:
    test_skipped("no writable cross-device dir (tmpfs) for --backup-dir EXDEV path")

src = FROMDIR
dst = SCRATCHDIR / 'bak-xdev-dst'
for d in (src, dst):
    rmtree(d)
makepath(src, dst)

bak = tempfile.mkdtemp(prefix='rsync-bak-xdev-', dir=TMPFS)

# dst holds the items that will be BACKED UP; src holds different-typed
# replacements so the generator deletes-with-backup before recreating.
# - reg:  dst regular gen1 -> src regular gen2  (copy_file fallback)
# - lnk:  dst symlink target-gen1 -> src symlink target-gen2
#                                               (do_symlink_at fallback)
# - fifo: dst FIFO -> src REGULAR file          (FIFO is backed up via the
#                                                IS_SPECIAL do_mknod_at fallback,
#                                                then replaced by a regular file)
(dst / 'reg').write_text('gen1\n')
(src / 'reg').write_text('gen1\n' * 2)  # different size -> transfer
os.symlink('target-gen1', dst / 'lnk')
os.symlink('target-gen2', src / 'lnk')
have_fifo = True
try:
    os.mkfifo(dst / 'fifo', 0o644)
except OSError:
    have_fifo = False
(src / 'fifo').write_text('replaces-fifo\n')

have_lnk = (dst / 'lnk').is_symlink()

try:
    # -rlpD: recurse, symlinks, perms (so the FIFO perm-diff triggers
    # replacement), specials/devices.  --debug=backup so the per-type
    # "make_backup: SYMLINK/DEVICE/COPY ... successful" lines fire too.
    r = subprocess.run(
        rsync_argv('-rlpD', '--debug=backup',
                   '--backup', f'--backup-dir={bak}',
                   f'{src}/', f'{dst}/'),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        test_fail(f"--backup --backup-dir=<cross-dev> failed "
                  f"(rc={r.returncode}):\n{r.stderr}")

    # Regular file: copy_file() fallback.
    breg = os.path.join(bak, 'reg')
    if not os.path.isfile(breg):
        test_fail(f"regular-file backup not created at {breg}")
    if open(breg).read() != 'gen1\n':
        test_fail(f"regular-file backup has wrong content "
                  f"(expected dst's original 'gen1\\n', got {open(breg).read()!r})")

    # Symlink: do_symlink_at() fallback.
    if have_lnk:
        blnk = os.path.join(bak, 'lnk')
        if not os.path.islink(blnk):
            test_fail(f"symlink backup not created at {blnk}")
        if os.readlink(blnk) != 'target-gen1':
            test_fail(f"symlink backup target wrong: {os.readlink(blnk)!r} != 'target-gen1'")

    # FIFO: do_mknod_at() fallback.
    if have_fifo:
        bfifo = os.path.join(bak, 'fifo')
        if not (os.path.exists(bfifo) and stat.S_ISFIFO(os.lstat(bfifo).st_mode)):
            test_fail(f"FIFO backup not created at {bfifo}")

    print(f"backup-crossdev-copy: --backup-dir on {TMPFS} (EXDEV) -> "
          f"reg=COPY{', lnk=SYMLINK' if have_lnk else ''}"
          f"{', fifo=MKNOD' if have_fifo else ''}")
finally:
    shutil.rmtree(bak, ignore_errors=True)
