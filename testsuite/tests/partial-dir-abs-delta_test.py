#!/usr/bin/env python3
# Regression guard: a --no-whole-file delta whose basis is reached via an
# ABSOLUTE --partial-dir must reconstruct correctly, not spuriously fail
# verification and strand the data in the partial-dir.
#
# This locks in the fix for the "failed verification" family reported as #724
# (and the FreeBSD lseek variant #725): an interrupted/delta transfer resumed
# against an absolute --partial-dir looped on exit code 23
# ("failed verification -- update put into partial-dir"), leaving the correct
# data in the partial-dir and never populating the destination.
#
# Root cause (same as #897): the receiver opened the basis with
# secure_relative_open(NULL, fnamecmp, ...), whose front door rejects an
# ABSOLUTE relpath with EINVAL. An absolute --partial-dir makes fnamecmp
# absolute, so the basis fd was -1, no basis was mapped, and the matched-block
# sum_update() (guarded by `if (mapbuf)`) was skipped -- so the whole-file
# verification checksum was computed over the literal data only and always
# mismatched, even though the in-place bytes were correct. Fixed by
# secure_basis_open() (commit 31fbb17d), which treats an operator-trusted
# absolute basis as (trusted dir + O_NOFOLLOW leaf).
#
# Deterministic (the partial-dir file IS the delta basis, so matched blocks are
# guaranteed) and portable: no daemon, no ZFS, any uid. Unlike the flaky
# --copy-dest/--backup-dir manifestations of the same bug, this reproduces every
# run. PASS on the fixed base; on a broken rsync (3.4.0..3.4.3) it FAILS with
# rc=23 + "failed verification" and the destination file missing/wrong.

import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, test_fail,
)

src = SCRATCHDIR / 'pdsrc'
dst = SCRATCHDIR / 'pddst'
partial = SCRATCHDIR / 'pdpartial'      # an ABSOLUTE --partial-dir
for d in (src, dst, partial):
    rmtree(d)
makepath(src, dst, partial)

# The file to transfer, and a near-copy in the partial-dir to serve as the
# delta basis (a few changed bytes mid-file => a real delta with matched blocks).
make_data_file(src / 'big.dat', 1200 * 1024)
import shutil
shutil.copy2(src / 'big.dat', partial / 'big.dat')
with open(partial / 'big.dat', 'r+b') as fh:
    fh.seek(600000)
    fh.write(b'PARTIALDIR_BASIS_DELTA')

# Absolute --partial-dir => the receiver's basis path (fnamecmp) is absolute.
proc = subprocess.run(
    rsync_argv('-a', '--no-whole-file', '--partial',
               f'--partial-dir={partial}', f'{src}/', f'{dst}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
print(out)

if 'failed verification' in out or 'lseek returned' in out:
    test_fail(
        "#724/#725: absolute --partial-dir delta resume spuriously failed "
        "verification (the receiver opened the absolute basis via "
        "secure_relative_open(NULL, abs) -> EINVAL -> no basis mapped -> the "
        "matched blocks were dropped from the verify checksum). Expected the "
        "secure_basis_open() fix (31fbb17d) to reconstruct correctly:\n" + out)

got = dst / 'big.dat'
if proc.returncode != 0:
    test_fail(f"absolute --partial-dir delta transfer failed (rc="
              f"{proc.returncode}); the data was likely stranded in the "
              f"partial-dir:\n{out}")
if not got.is_file():
    test_fail(f"destination file {got} was not created -- data stranded in the "
              f"absolute --partial-dir (the #724/#725 symptom)")
if not __import__('filecmp').cmp(str(src / 'big.dat'), str(got), shallow=False):
    test_fail("destination content differs from source after an absolute "
              "--partial-dir delta resume")
