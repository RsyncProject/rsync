import os
import shlex
import subprocess
import sys
from pathlib import Path

from rsyncfns import (
    FROMDIR, RSYNC, SCRATCHDIR,
    makepath, test_fail, test_skipped
)

rsync_args = shlex.split(str(RSYNC))
for arg in rsync_args:
    if arg.startswith('--protocol='):
        prot_version = int(arg.split('=')[1])
        if prot_version < 33:
            test_skipped(f"Skipping write-touched-blocks: feature requires protocol 33, but CI forced {prot_version}")

src = FROMDIR
makepath(src)

base_file = src / 'base.bin'

# Generate 4 MiB of random data in memory
data = os.urandom(4 * 1024 * 1024)

def setup_test(dest_name):
    """Resets the base file and creates a clean destination file."""
    dest_path = SCRATCHDIR / dest_name
    base_file.write_bytes(data)
    dest_path.write_bytes(data)
    return dest_path

def run_client(src_path, dest_path):
    rsync_cmd = shlex.split(str(RSYNC))
    argv = rsync_cmd + ['-a', '--stats', '--inplace', '-I', '--no-whole-file',
            str(src_path), str(dest_path)]
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)

# TEST 1: Contiguous Write (1 Block)
dest_contig = setup_test('dest_contiguous.bin')
with open(base_file, 'r+b') as f:
    f.write(b'\x00' * 3000)

proc = run_client(base_file, dest_contig)
if proc.returncode != 0:
    test_fail(f"rsync failed on contiguous test:\n{proc.stdout}")
if "Number of 4 KiB logical blocks touched: 1\n" not in proc.stdout:
    test_fail(f"Contiguous check failed! Expected 1 block. Output:\n{proc.stdout}")

# TEST 2: Scattered Write (10 Blocks)
dest_scatter = setup_test('dest_scattered.bin')
with open(base_file, 'r+b') as f:
    for i in range(1, 11):
        f.seek(i * 4096)
        old_byte = f.read(1)[0]
        f.seek(i * 4096)
        f.write(bytes([old_byte ^ 0xFF]))

proc = run_client(base_file, dest_scatter)
if proc.returncode != 0:
    test_fail(f"rsync failed on scattered test:\n{proc.stdout}")
if "Number of 4 KiB logical blocks touched: 10\n" not in proc.stdout:
    test_fail(f"Scattered check failed! Expected 10 blocks. Output:\n{proc.stdout}")

# TEST 3: Identical Files (0 Blocks Edge Case)
dest_zero = setup_test('dest_zero.bin')

proc = run_client(base_file, dest_zero)
if proc.returncode != 0:
    test_fail(f"rsync failed on zero-block test:\n{proc.stdout}")
if "Number of 4 KiB logical blocks touched: 0\n" not in proc.stdout:
    test_fail(f"Zero check failed! Expected 0 blocks. Output:\n{proc.stdout}")

# TEST 4: Full File Write (1,024 Blocks)
dest_full = setup_test('dest_full.bin')
# Overwrite the entire 4 MiB base file with brand new random data
# This forces the delta algorithm to find 0 matches and write all 1,024 blocks.
base_file.write_bytes(os.urandom(4 * 1024 * 1024))

proc = run_client(base_file, dest_full)
if proc.returncode != 0:
    test_fail(f"rsync failed on full write test:\n{proc.stdout}")
if "Number of 4 KiB logical blocks touched: 1,024\n" not in proc.stdout:
    test_fail(f"Full write check failed! Expected 1,024 blocks. Output:\n{proc.stdout}")

# TEST 5: Sparse File Write (Hole skipping)
sparse_src = src / 'sparse.bin'
sparse_dest = SCRATCHDIR / 'sparse_dest.bin'

# Create a file with written data on the ends, but a massive 4 MiB hole in the middle.
# 1 block data + 1,024 blocks hole + 1 block data = 1,026 blocks total size.
with open(sparse_src, 'wb') as f:
    f.write(os.urandom(4096))             # Block 1 (Data)
    f.seek(4 * 1024 * 1024, os.SEEK_CUR)  # The Hole (4 MiB of nothing)
    f.write(os.urandom(4096))             # Block 1026 (Data)

# We must run this WITH --sparse (-S) and WITHOUT --inplace to force
# the receiver to create a brand new sparse file from scratch using write_sparse().
rsync_cmd = shlex.split(str(RSYNC))
argv_sparse = rsync_cmd + ['-a', '--stats', '--sparse', str(sparse_src), str(sparse_dest)]
proc = subprocess.run(argv_sparse, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

if proc.returncode != 0:
    test_fail(f"rsync failed on sparse test:\n{proc.stdout}")

# Even though the file is over 4 MiB in size, only 2 logical 4K blocks
# should be written because the rest was skipped via lseek.
if "Number of 4 KiB logical blocks touched: 2\n" not in proc.stdout:
    test_fail(f"Sparse check failed! Expected 2 logical blocks written. Output:\n{proc.stdout}")

# TEST 6: Multiple Files
# Creates two separate 4KB files. If the tracker fails to reset between
# files due to FD recycling, it will report 1 block instead of 2.
fd_src_dir = src / 'fd_test'
fd_dest_dir = SCRATCHDIR / 'fd_dest'
makepath(fd_src_dir)
makepath(fd_dest_dir)

# Create two distinct 1-block files
(fd_src_dir / 'fileA.bin').write_bytes(os.urandom(4096))
(fd_src_dir / 'fileB.bin').write_bytes(os.urandom(4096))

# Sync the whole directory so rsync processes both in one process lifespan
rsync_cmd = shlex.split(str(RSYNC))
argv_fd = rsync_cmd + ['-a', '--stats', '--inplace', str(fd_src_dir) + '/', str(fd_dest_dir) + '/']
proc = subprocess.run(argv_fd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

if proc.returncode != 0:
    test_fail(f"rsync failed on multiple files test:\n{proc.stdout}")

if "Number of 4 KiB logical blocks touched: 2\n" not in proc.stdout:
    test_fail(f"FD Reuse bug confirmed! Expected 2 blocks (1 per file). Output:\n{proc.stdout}")

# TEST 7: Batch Mode State Reset (--read-batch)
batch_src_dir = src / 'batch_src'
batch_dest_dir = SCRATCHDIR / 'batch_dest'
batch_file = SCRATCHDIR / 'test_batch.rsync'
makepath(batch_src_dir)
makepath(batch_dest_dir)

# Create two 4KB source files with random data
(batch_src_dir / 'fileA.bin').write_bytes(os.urandom(4096))
(batch_src_dir / 'fileB.bin').write_bytes(os.urandom(4096))

# Create two 4KB zeroed destination files (forces the delta algorithm to write exactly 1 block per file)
(batch_dest_dir / 'fileA.bin').write_bytes(b'\x00' * 4096)
(batch_dest_dir / 'fileB.bin').write_bytes(b'\x00' * 4096)

# Step 1: Generate the batch file (Sender side)
# We MUST use '-I' because the files have identical sizes and timestamps.
rsync_cmd = shlex.split(str(RSYNC))
argv_write_batch = rsync_cmd + ['-a', '-I', '--only-write-batch=' + str(batch_file),
                    str(batch_src_dir) + '/', str(batch_dest_dir) + '/']
subprocess.run(argv_write_batch, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

# Step 2: Apply the batch file and collect stats (Receiver side)
argv_read_batch = rsync_cmd + ['-a', '-I', '--inplace', '--stats',
                   '--read-batch=' + str(batch_file), str(batch_dest_dir) + '/']

proc = subprocess.run(argv_read_batch, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

if proc.returncode != 0:
    test_fail(f"rsync failed on read-batch test:\n{proc.stdout}")

# If the state leaked across the batch read, this would output 1 block.
if "Number of 4 KiB logical blocks touched: 2\n" not in proc.stdout:
    test_fail(f"Batch Mode tracker check failed! Expected 2 blocks (1 per file). Output:\n{proc.stdout}")

print("write-touched-blocks: cleanly distinguishes contiguous, scattered, zero, full, sparse, multi-file, and batch writes")
