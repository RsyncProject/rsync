#!/usr/bin/env python3

import os

from harness.rsync import (
    FROMDIR, TODIR,
    allocated_size, assert_same, make_data_file, makepath, rmtree, run_rsync, test_fail,
    test_skipped,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f')

rmtree(src)
rmtree(TODIR)
makepath(src)
(src / 'probe').write_text("x\n")
if run_rsync('-a', '--preallocate', f'{src}/', f'{TODIR}/',
             check=False, capture_output=True).returncode != 0:
    test_skipped("--preallocate not supported on this platform", capability='preallocate')

def punch_frees(offset, length, size):
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
        f.write(b'\0' * hole)
        f.write(os.urandom(tail))

seed_plain()
run_rsync('-a', '--preallocate', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--preallocate content')

seed_holey()
run_rsync('-a', '--preallocate', '--sparse', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--preallocate --sparse content')
if can_punch and allocated_size(TODIR / deep) >= os.path.getsize(TODIR / deep):
    test_fail(f"--preallocate --sparse left the file fully allocated "
              f"(allocated {allocated_size(TODIR / deep)} for a "
              f"{os.path.getsize(TODIR / deep)}-byte file); the preallocated "
              "extent's zero run was not punched into a hole")

seed_plain()
run_rsync('-a', f'{src}/', f'{TODIR}/')
data = bytearray((src / deep).read_bytes())
data[200_000:800_000] = b'\0' * 600_000
(src / deep).write_bytes(bytes(data))
st = os.stat(src / deep)
os.utime(src / deep, (st.st_atime, st.st_mtime + 100))
run_rsync('-a', '--inplace', '--sparse', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--inplace --sparse content')
if can_punch and allocated_size(TODIR / deep) >= os.path.getsize(TODIR / deep):
    test_fail(f"--inplace --sparse did not punch the zero run: allocated "
              f"{allocated_size(TODIR / deep)} for a {os.path.getsize(TODIR / deep)}"
              "-byte file")

rmtree(src)
rmtree(TODIR)
makepath(src / 'd1' / 'd2' / 'd3', TODIR / 'd1' / 'd2' / 'd3')
with open(src / deep, 'wb') as source, open(TODIR / deep, 'wb') as dest:
    for _ in range(256):
        block = os.urandom(4096) + b'\0' * 24576 + os.urandom(4096)
        source.write(block)
        dest.write(block)

can_punch_interior = can_punch and punch_frees(4096, 24576, 32768)
if can_punch and not can_punch_interior:
    print("preallocate: interior-hole assertion skipped: this filesystem's "
          "allocation unit cannot free a 24 KiB run inside a 32 KiB block")

matched_size = os.path.getsize(TODIR / deep)
matched_before = allocated_size(TODIR / deep)
run_rsync('-a', '--ignore-times', '--inplace', '--sparse', '--no-whole-file',
          '--block-size=32768', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep,
            label='--inplace --sparse matched-block content')
matched_after = allocated_size(TODIR / deep)
if (can_punch_interior and matched_before >= matched_size
        and matched_after * 2 >= matched_before):
    test_fail(f"--inplace --sparse left matching interior zero runs allocated: "
              f"{matched_after} of {matched_before} bytes remain allocated "
              f"after a {matched_size}-byte matched-block update")

print("preallocate: --preallocate (do_fallocate) + sparse hole-punching "
      "(do_punch_hole) verified at depth")
