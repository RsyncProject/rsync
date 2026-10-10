#!/usr/bin/env python3

import re
import shutil
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12915
DATA_SIZE = 40000

mod = SCRATCHDIR / 'bakmod'
src = SCRATCHDIR / 'src915'
rmtree(mod)
rmtree(src)
makepath(mod / '01', src)
make_data_file(src / 'f.dat', DATA_SIZE)
shutil.copy2(src / 'f.dat', mod / '01' / 'f.dat')

conf = write_daemon_conf([
    ('bak', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def push(opt):
    rmtree(mod / '00')
    proc = subprocess.run(
        rsync_argv('-a', '--stats', opt, f'{src}/', f'{url}bak/00/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, (proc.stdout or '')

def same_inode(a, b):
    sa, sb = a.stat(), b.stat()
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)

def literal_bytes(out):
    m = re.search(r'Literal data:\s*([\d,]+)', out)
    return int(m.group(1).replace(',', '')) if m else -1

regressions = []
basis = mod / '01' / 'f.dat'

rc, out = push('--link-dest=../01')
if rc != 0:
    test_fail(f"--link-dest push failed unexpectedly (rc={rc}):\n{out}")
dest = mod / '00' / 'f.dat'
if not dest.is_file():
    test_fail(f"--link-dest: destination file missing ({dest})")
if not same_inode(dest, basis):
    regressions.append("--link-dest=../01 did not hard-link to the basis "
                       "(file re-transferred)")

rc, out = push('--copy-dest=../01')
if rc != 0:
    test_fail(f"--copy-dest push failed unexpectedly (rc={rc}):\n{out}")
dest = mod / '00' / 'f.dat'
if not dest.is_file():
    test_fail(f"--copy-dest: destination file missing ({dest})")
lit = literal_bytes(out)
if lit > DATA_SIZE // 2:
    regressions.append(f"--copy-dest=../01 re-sent the data over the wire "
                       f"(Literal data={lit}, basis not used)")

rc, out = push('--compare-dest=../01')
if rc != 0:
    test_fail(f"--compare-dest push failed unexpectedly (rc={rc}):\n{out}")
if (mod / '00' / 'f.dat').is_file():
    regressions.append("--compare-dest=../01 created the file in the dest "
                       "(basis not matched, so the file was transferred)")

if regressions:
    test_fail(
        "#915/#930: a daemon receiver ignored a RELATIVE alt-basis dir (../01) -- "
        "the secure resolver must honour an in-module `..` climb to the sibling "
        "basis on every platform:\n  - " + "\n  - ".join(regressions))
