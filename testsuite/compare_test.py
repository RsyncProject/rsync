#!/usr/bin/env python3
"""Coverage of the comparison/skip options at depth: -c, -I, --size-only,
--modify-window.

These decide WHETHER a file is transferred. Each is checked on a file several
levels deep using a "stealth change" (same size, same mtime, different content)
that the default quick check deliberately skips.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f3')


def seed():
    rmtree(src)
    rmtree(TODIR)
    make_tree(src, depth=3, data=True, data_size=4096)
    run_rsync('-a', f'{src}/', f'{TODIR}/')          # dest == src


def stealth_change():
    """Change the deep source file's content but restore the destination's
    size+mtime, so the quick check sees them as equal."""
    st = os.stat(TODIR / deep)
    data = bytearray((src / deep).read_bytes())
    data[0] ^= 0xFF                                   # same length, new content
    (src / deep).write_bytes(bytes(data))
    os.utime(src / deep, (st.st_atime, st.st_mtime))


# --- the default quick check skips a stealth change; -c and -I catch it ------
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

# --- --size-only skips a same-size change even when the mtime differs --------
def samesize_newmtime():
    data = bytearray((src / deep).read_bytes())
    data[0] ^= 0xFF                                  # same size, new content
    (src / deep).write_bytes(bytes(data))
    st = os.stat(src / deep)
    os.utime(src / deep, (st.st_atime, st.st_mtime + 100))   # mtime differs


seed()
samesize_newmtime()
run_rsync('-a', '--size-only', f'{src}/', f'{TODIR}/')
if (TODIR / deep).read_bytes() == (src / deep).read_bytes():
    test_fail("--size-only transferred a same-size file (should have skipped)")

# Contrast on a fresh tree: the default DOES transfer it (mtime differs).
# (Re-seed because --size-only above updated the dest mtime to match.)
seed()
samesize_newmtime()
run_rsync('-a', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='default caught mtime change')

# --- --modify-window absorbs a small mtime difference -----------------------
# Both runs are dry-run (-ain): a real run would update the dest mtime to match
# the source, leaving the --modify-window run nothing to absorb (vacuous).
seed()
st = os.stat(TODIR / deep)
os.utime(src / deep, (st.st_atime, st.st_mtime + 1))  # 1s newer, same content
p = run_rsync('-ain', f'{src}/', f'{TODIR}/', capture_output=True)
if 'f3' not in p.stdout:
    test_fail("a 1s mtime change was not itemized without --modify-window")
p = run_rsync('-ain', '--modify-window=2', f'{src}/', f'{TODIR}/',
              capture_output=True)
if 'f3' in p.stdout:
    test_fail("--modify-window=2 did not absorb a 1s mtime difference")

print("compare: -c / -I / --size-only / --modify-window verified at depth")
