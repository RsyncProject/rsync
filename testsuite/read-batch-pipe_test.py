#!/usr/bin/env python3
"""--read-batch process substitution /dev/fd/ pipe must not crash with strict file-type checks."""

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped

# We require bash specifically because standard POSIX /bin/sh does not
# guarantee support for <(...) process substitution syntax.
bash = shutil.which('bash')
if bash is None:
    test_skipped('bash is unavailable, cannot test process substitution')

# Verify the host bash actually supports process substitution
probe = subprocess.run(
    [bash, '-c', 'cat <(echo "probe")'],
    capture_output=True)

if probe.returncode != 0:
    test_skipped('bash process substitution is not supported on this system')

base = Path(SCRATCHDIR / 'rsync-batch-fifo')
src = base / 'src'
dest = base / 'dest'
batch_file = base / 'update.batch'
makepath(src, dest)

# 1. Create dummy data
(src / 'payload.txt').write_text('batch payload data\n')

# 2. ---> THE MISSING STEP <---
# Generate a valid batch file so `cat` actually has a real file to read.
subprocess.run([*rsync_argv('-a', f'--write-batch={batch_file}'), f'{src}/', f'{dest}/'], check=True)


# 3. Now we can test reading it via bash process substitution
rsync_base_cmd = shlex.join(rsync_argv('-a'))
batch_path = shlex.quote(str(batch_file))
dest_path = shlex.quote(str(dest) + '/')

# Construct the bash command: rsync -a --read-batch=<(cat /path/to/batch) /dest/
bash_script = f"{rsync_base_cmd} --read-batch=<(cat {batch_path}) {dest_path}"

try:
    proc_read = subprocess.run(
        [bash, '-c', bash_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    rmtree(base)
    test_fail('process substitution batch test timed out')

ctx = f'rc={proc_read.returncode}, stderr={proc_read.stderr.strip()!r}'

# Evaluate result against the strict S_ISREG check bug
if proc_read.returncode != 0:
    rmtree(base)
    test_fail(f'rsync crashed reading batch file from pipe ({ctx})')

if not (dest / 'payload.txt').is_file():
    rmtree(base)
    test_fail(f'rsync exited successfully but payload is missing in target ({ctx})')

rmtree(base)
print('rsync successfully parsed batch stream via process substitution pseudo-path')
raise SystemExit(0)

