"""Pin the concrete rule behind `--keep-dirlinks` (-K).

The rsync(1) `--keep-dirlinks` entry leads with a clear rule -- "treat a symlink
to a directory as though it were a real directory, but only if it matches a real
directory from the sender" -- but then a vague security caution ("you must trust
all the symlinks ... the user could then (on a subsequent copy) replace the
symlink ...").  This test pins the deterministic part of the behaviour so the
caution can be tied to a concrete mechanism:

  - sender has a real dir of that name  -> dest symlink-to-dir is KEPT and the
    contents land THROUGH it (this is the followed-into-target behaviour the
    caution warns about);
  - sender has a non-dir (file) of that name -> -K does not apply, the dest
    symlink is replaced by the file;
  - default (no -K) -> the dest symlink is deleted and recreated as a real dir.

Pure local client behaviour: no daemon/root/tcp.  Cross-version: expected
identical against --rsync-bin=old_versions/rsync_3.2.7.
"""

import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'keep-dirlinks'
rmtree(base)
base.mkdir(parents=True)


def dest_with_symlink(name):
    """Fresh dest where `name` is a symlink to a real 'target' dir."""
    d = base / name
    rmtree(d)
    (d / 'target').mkdir(parents=True)
    os.symlink('target', d / 'd')
    return d


def run(src, dst, *opts):
    subprocess.run(rsync_argv('-a', *opts, f'{src}/', f'{dst}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# sender with a real directory 'd' containing a file.
sdir = base / 'src_dir'
(sdir / 'd').mkdir(parents=True)
(sdir / 'd' / 'f').write_text('X\n')

# sender with 'd' as a plain FILE.
sfile = base / 'src_file'
sfile.mkdir()
(sfile / 'd').write_text('FILE\n')

# 1) -K + sender dir match: symlink kept, contents land through it.
d = dest_with_symlink('case_match')
run(sdir, d, '--keep-dirlinks')
if not (d / 'd').is_symlink():
    test_fail("--keep-dirlinks: dest symlink-to-dir was replaced despite a sender dir match")
through = d / 'target' / 'f'
if not through.is_file() or through.read_text() != 'X\n':
    test_fail("--keep-dirlinks: contents did not land THROUGH the kept symlink")

# 2) -K + sender is a file: -K doesn't apply, symlink replaced by the file.
d = dest_with_symlink('case_file')
run(sfile, d, '--keep-dirlinks')
if (d / 'd').is_symlink():
    test_fail("--keep-dirlinks: dest symlink kept even though the sender 'd' is a file")
if not (d / 'd').is_file() or (d / 'd').read_text() != 'FILE\n':
    test_fail("--keep-dirlinks: sender file did not replace the dest symlink")

# 3) default (no -K): symlink deleted and recreated as a real directory.
d = dest_with_symlink('case_default')
run(sdir, d)
if (d / 'd').is_symlink():
    test_fail("default: dest symlink-to-dir should have been replaced by a real dir")
if not (d / 'd').is_dir() or not (d / 'd' / 'f').is_file():
    test_fail("default: 'd' was not recreated as a real directory with the file")

print("keep-dirlinks-rule: -K keeps+follows a dest symlink-to-dir only on a "
      "sender DIR match (contents land through it); a sender file replaces it; "
      "the default replaces the symlink with a real directory")
