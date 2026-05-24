#!/usr/bin/env python3
"""Coverage of the file-allocation syscalls in syscall.c at depth:
do_fallocate (--preallocate) and do_punch_hole (sparse writes).

These are receiver-side file operations the resolver restructure also touches.
Where the filesystem lacks fallocate/punch-hole the calls warn and the transfer
still completes, so the content assertions hold regardless; the coverage is
gained wherever the kernel supports them.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_data_file, makepath, rmtree, run_rsync, test_skipped,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f')

# --preallocate needs fallocate/posix_fallocate, and do_punch_hole needs
# FALLOC_FL_PUNCH_HOLE -- both Linux (and Cygwin) features. macOS, the *BSDs and
# Solaris build without preallocation and reject the option outright ("prealloc-
# ation is not supported"), so probe once with a trivial transfer and skip the
# whole test where it's unavailable.
rmtree(src)
rmtree(TODIR)
makepath(src)
(src / 'probe').write_text("x\n")
if run_rsync('-a', '--preallocate', f'{src}/', f'{TODIR}/',
             check=False, capture_output=True).returncode != 0:
    test_skipped("--preallocate not supported on this platform")


def seed_plain(size=1_000_000):
    rmtree(src)
    rmtree(TODIR)
    makepath(src / 'd1' / 'd2' / 'd3')
    make_data_file(src / deep, size)


def seed_holey(head=4096, hole=2 * 1024 * 1024, tail=4096):
    rmtree(src)
    rmtree(TODIR)
    makepath(src / 'd1' / 'd2' / 'd3')
    with open(src / deep, 'wb') as f:
        f.write(os.urandom(head))
        f.write(b'\0' * hole)        # a real zero run for the sparse writer
        f.write(os.urandom(tail))


# --- --preallocate: do_fallocate on the receiver ----------------------------
seed_plain()
run_rsync('-a', '--preallocate', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--preallocate content')

# --- --preallocate --sparse on a holey file: do_fallocate + do_punch_hole ---
seed_holey()
run_rsync('-a', '--preallocate', '--sparse', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--preallocate --sparse content')

# --- --inplace --sparse update that introduces a zero run: do_punch_hole ----
# (sparse_end's updating_basis_or_equiv branch punches the hole in place.)
seed_plain()
run_rsync('-a', f'{src}/', f'{TODIR}/')              # dest starts fully populated
data = bytearray((src / deep).read_bytes())
data[200_000:800_000] = b'\0' * 600_000              # same size, new zero run
(src / deep).write_bytes(bytes(data))
st = os.stat(src / deep)
os.utime(src / deep, (st.st_atime, st.st_mtime + 100))   # force a delta update
run_rsync('-a', '--inplace', '--sparse', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--inplace --sparse content')

print("preallocate: --preallocate (do_fallocate) + sparse hole-punching "
      "(do_punch_hole) verified at depth")
