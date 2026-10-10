#!/usr/bin/env python3

import os
import shutil
import stat
import subprocess
import tempfile

from harness.rsync import (
    SCRATCHDIR, FROMDIR,
    makepath, rmtree, rsync_argv, test_fail, test_skipped,
)

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
    test_skipped("no writable cross-device dir (tmpfs) for --backup-dir EXDEV path",
                 capability='cross_device')

src = FROMDIR
dst = SCRATCHDIR / 'bak-xdev-dst'
for d in (src, dst):
    rmtree(d)
makepath(src, dst)

bak = tempfile.mkdtemp(prefix='rsync-bak-xdev-', dir=TMPFS)

(dst / 'reg').write_text('gen1\n')
(src / 'reg').write_text('gen1\n' * 2)
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
    r = subprocess.run(
        rsync_argv('-rlpD', '--debug=backup',
                   '--backup', f'--backup-dir={bak}',
                   f'{src}/', f'{dst}/'),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        test_fail(f"--backup --backup-dir=<cross-dev> failed "
                  f"(rc={r.returncode}):\n{r.stderr}")

    breg = os.path.join(bak, 'reg')
    if not os.path.isfile(breg):
        test_fail(f"regular-file backup not created at {breg}")
    if open(breg).read() != 'gen1\n':
        test_fail(f"regular-file backup has wrong content "
                  f"(expected dst's original 'gen1\\n', got {open(breg).read()!r})")

    if have_lnk:
        blnk = os.path.join(bak, 'lnk')
        if not os.path.islink(blnk):
            test_fail(f"symlink backup not created at {blnk}")
        if os.readlink(blnk) != 'target-gen1':
            test_fail(f"symlink backup target wrong: {os.readlink(blnk)!r} != 'target-gen1'")

    if have_fifo:
        bfifo = os.path.join(bak, 'fifo')
        if not (os.path.exists(bfifo) and stat.S_ISFIFO(os.lstat(bfifo).st_mode)):
            test_fail(f"FIFO backup not created at {bfifo}")

    print(f"backup-crossdev-copy: --backup-dir on {TMPFS} (EXDEV) -> "
          f"reg=COPY{', lnk=SYMLINK' if have_lnk else ''}"
          f"{', fifo=MKNOD' if have_fifo else ''}")
finally:
    shutil.rmtree(bak, ignore_errors=True)
