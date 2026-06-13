#!/usr/bin/env python3
"""Coverage of the file-allocation syscalls in syscall.c at depth:
do_fallocate (--preallocate) and do_punch_hole (sparse writes).

These are receiver-side file operations the resolver restructure also touches.
Content must survive everywhere; in addition, where the filesystem stores holes,
--preallocate --sparse must end up sparse (st_blocks below the apparent size).
That is a regression guard: do_fallocate() must report the preallocated length
so write_sparse() punches holes in the reserved extent instead of lseek'ing over
it -- a stray 0 there silently left the file fully allocated.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_data_file, makepath, rmtree, run_rsync, test_fail,
    test_skipped,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f')


def allocated(path):
    return os.stat(path).st_blocks * 512

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

def fs_can_punch_holes():
    """True only where the kernel can deallocate blocks via FALLOC_FL_PUNCH_HOLE
    -- the mechanism do_punch_hole uses for --sparse. A filesystem may report
    seek-based sparseness yet still keep every block on a punch (e.g. where
    rsync's punch falls back to writing zeros), so probe the real capability and
    assert the hole only where it actually frees blocks."""
    import ctypes
    import ctypes.util
    KEEP_SIZE, PUNCH_HOLE = 0x01, 0x02
    p = src / 'punch-probe'
    fd = -1
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6',
                           use_errno=True)
        libc.fallocate.argtypes = [ctypes.c_int, ctypes.c_int,
                                   ctypes.c_longlong, ctypes.c_longlong]
        fd = os.open(p, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o644)
        os.write(fd, b'\xff' * 65536)
        before = os.fstat(fd).st_blocks
        ret = libc.fallocate(fd, PUNCH_HOLE | KEEP_SIZE, 0, 65536)
        return ret == 0 and os.fstat(fd).st_blocks < before
    except (OSError, AttributeError, ValueError):
        return False
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(p)
        except OSError:
            pass


can_punch = fs_can_punch_holes()


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
# rsync.1 promises sparse blocks for this combination where the FS supports
# holes. Assert it: do_fallocate reserves the whole extent, then the zero run
# must be punched back out (st_blocks well below the apparent size).
seed_holey()
run_rsync('-a', '--preallocate', '--sparse', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--preallocate --sparse content')
if can_punch and allocated(TODIR / deep) >= os.path.getsize(TODIR / deep):
    test_fail(f"--preallocate --sparse left the file fully allocated "
              f"(allocated {allocated(TODIR / deep)} for a "
              f"{os.path.getsize(TODIR / deep)}-byte file); the preallocated "
              "extent's zero run was not punched into a hole")

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
if can_punch and allocated(TODIR / deep) >= os.path.getsize(TODIR / deep):
    test_fail(f"--inplace --sparse did not punch the zero run: allocated "
              f"{allocated(TODIR / deep)} for a {os.path.getsize(TODIR / deep)}"
              "-byte file")

print("preallocate: --preallocate (do_fallocate) + sparse hole-punching "
      "(do_punch_hole) verified at depth")
