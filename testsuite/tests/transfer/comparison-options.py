#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f3')

def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3, data=True, data_size=4096)
    run_rsync('-a', f'{src}/', f'{TODIR}/')

def stealth_change():
    st = os.stat(TODIR / deep)
    data = bytearray((src / deep).read_bytes())
    data[0] ^= 0xFF
    (src / deep).write_bytes(bytes(data))
    os.utime(src / deep, (st.st_atime, st.st_mtime))

seed()
stealth_change()
run_rsync('-a', f'{src}/', f'{TODIR}/')
if (TODIR / deep).read_bytes() == (src / deep).read_bytes():
    test_fail("default quick check unexpectedly transferred a same-size, "
              "same-mtime change (test setup is wrong)")

run_rsync('-a', '-c', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='-c caught stealth change')

seed()
stealth_change()
run_rsync('-a', '-I', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='-I caught stealth change')

def samesize_newmtime():
    data = bytearray((src / deep).read_bytes())
    data[0] ^= 0xFF
    (src / deep).write_bytes(bytes(data))
    st = os.stat(src / deep)
    os.utime(src / deep, (st.st_atime, st.st_mtime + 100))

seed()
samesize_newmtime()
run_rsync('-a', '--size-only', f'{src}/', f'{TODIR}/')
if (TODIR / deep).read_bytes() == (src / deep).read_bytes():
    test_fail("--size-only transferred a same-size file (should have skipped)")

seed()
samesize_newmtime()
run_rsync('-a', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='default caught mtime change')

seed()
st = os.stat(TODIR / deep)
os.utime(src / deep, (st.st_atime, st.st_mtime + 1))
p = run_rsync('-ain', f'{src}/', f'{TODIR}/', capture_output=True)
if 'f3' not in p.stdout:
    test_fail("a 1s mtime change was not itemized without --modify-window")
p = run_rsync('-ain', '--modify-window=2', f'{src}/', f'{TODIR}/',
              capture_output=True)
if 'f3' in p.stdout:
    test_fail("--modify-window=2 did not absorb a 1s mtime difference")

print("comparison options verified at depth")
