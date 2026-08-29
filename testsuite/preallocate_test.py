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

def punch_frees(offset, length, size):
    """True where punching [offset, offset+length) out of a `size`-byte file
    really deallocates blocks -- the mechanism do_punch_hole uses for --sparse.

    Two separate things can leave st_blocks untouched, so each assertion below
    probes the exact shape it relies on.  A filesystem may report seek-based
    sparseness yet still keep every block on a punch (e.g. where rsync's punch
    falls back to writing zeros), which a whole-file probe catches.  And a punch
    only frees storage in whole allocation units, which are not always 4 KiB: a
    tmpfs frees whole pages, 16 KiB on loongarch/loong64 (and 64 KiB on a
    64k-page ppc64el or arm64 kernel), and a filesystem may be formatted with a
    block size above the page size.  An interior run spanning no whole unit is
    zeroed rather than deallocated, so st_blocks does not move and an assertion
    phrased in st_blocks would report a hole-punching regression that is really
    just the filesystem's granularity.

    fallocate64() rather than fallocate(): where off_t is 32 bits the latter
    takes 32-bit offsets, so ctypes' 64-bit arguments do not line up with what
    it reads (on i386 it takes the high half of `offset` as its `length`) and
    every probe fails with EINVAL -- which is why every assertion below has
    silently done nothing on all the 32-bit ports.  fallocate64() takes off64_t
    everywhere and is a plain alias of fallocate() where off_t is already 64
    bits wide.

    The probe data has to be incompressible: a filesystem that compresses
    (btrfs with compress=) stores a run of one repeated byte in almost no
    blocks, leaving a successful punch with nothing to free."""
    import ctypes
    import ctypes.util
    KEEP_SIZE, PUNCH_HOLE = 0x01, 0x02
    p = src / 'punch-probe'
    fd = -1
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6',
                           use_errno=True)
        libc.fallocate64.argtypes = [ctypes.c_int, ctypes.c_int,
                                     ctypes.c_longlong, ctypes.c_longlong]
        fd = os.open(p, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o644)
        os.write(fd, os.urandom(size))
        before = os.fstat(fd).st_blocks
        ret = libc.fallocate64(fd, PUNCH_HOLE | KEEP_SIZE, offset, length)
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


can_punch = punch_frees(0, 65536, 65536)


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

# --- --inplace --sparse must punch interior holes in matched blocks ----------
# Make source and destination byte-identical and densely allocated.  With
# --block-size matching the pattern size, every block match is at the same
# offset, so skip_matched() sends it through write_sparse(use_seek=1).  The
# interior zero run must still be scanned and punched rather than merely
# seeked over with the rest of the matching block.
rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3', TODIR / 'd1' / 'd2' / 'd3')
with open(src / deep, 'wb') as source, open(TODIR / deep, 'wb') as dest:
    for _ in range(256):
        block = os.urandom(4096) + b'\0' * 24576 + os.urandom(4096)
        source.write(block)
        dest.write(block)

# Only assert the interior punch where the filesystem can free a 24 KiB run
# sitting 4 KiB into a 32 KiB block -- the exact shape written just above.
can_punch_interior = can_punch and punch_frees(4096, 24576, 32768)
if can_punch and not can_punch_interior:
    print("preallocate: interior-hole assertion skipped: this filesystem's "
          "allocation unit cannot free a 24 KiB run inside a 32 KiB block")

matched_size = os.path.getsize(TODIR / deep)
matched_before = allocated(TODIR / deep)
run_rsync('-a', '--ignore-times', '--inplace', '--sparse', '--no-whole-file',
          '--block-size=32768', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep,
            label='--inplace --sparse matched-block content')
matched_after = allocated(TODIR / deep)
if (can_punch_interior and matched_before >= matched_size
        and matched_after * 2 >= matched_before):
    test_fail(f"--inplace --sparse left matching interior zero runs allocated: "
              f"{matched_after} of {matched_before} bytes remain allocated "
              f"after a {matched_size}-byte matched-block update")

print("preallocate: --preallocate (do_fallocate) + sparse hole-punching "
      "(do_punch_hole) verified at depth")
