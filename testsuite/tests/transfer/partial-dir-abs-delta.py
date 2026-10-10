#!/usr/bin/env python3

import filecmp
import shutil
import subprocess

from harness.rsync import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, test_fail,
)

src = SCRATCHDIR / 'pdsrc'
dst = SCRATCHDIR / 'pddst'
partial = SCRATCHDIR / 'pdpartial'
for d in (src, dst, partial):
    rmtree(d)
makepath(src, dst, partial)

make_data_file(src / 'big.dat', 1200 * 1024)
shutil.copy2(src / 'big.dat', partial / 'big.dat')
with open(partial / 'big.dat', 'r+b') as fh:
    fh.seek(600000)
    fh.write(b'PARTIALDIR_BASIS_DELTA')

proc = subprocess.run(
    rsync_argv('-a', '--no-whole-file', '--partial',
               f'--partial-dir={partial}', f'{src}/', f'{dst}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
print(out)

if 'failed verification' in out or 'lseek returned' in out:
    test_fail(
        '#724/#725: absolute --partial-dir delta resume failed:\n' + out)

got = dst / 'big.dat'
if proc.returncode != 0:
    test_fail(f"absolute --partial-dir delta transfer failed (rc="
              f"{proc.returncode}); the data was likely stranded in the "
              f"partial-dir:\n{out}")
if not got.is_file():
    test_fail(f"destination file {got} was not created -- data stranded in the "
              f"absolute --partial-dir (the #724/#725 symptom)")
if not filecmp.cmp(str(src / 'big.dat'), str(got), shallow=False):
    test_fail("destination content differs from source after an absolute "
              "--partial-dir delta resume")
