#!/usr/bin/env python3

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped

if not sys.platform.startswith('linux'):
    test_skipped('This test requires Linux platform', capability='proc_fd')

bash = shutil.which('bash')
if bash is None:
    test_skipped('bash is unavailable, cannot test process substitution', capability='proc_fd')

bash_env = {k: v for k, v in os.environ.items() if k != 'POSIXLY_CORRECT'}
probe = subprocess.run(
    [bash, '-c', 'cat <(echo "probe")'],
    env=bash_env,
    capture_output=True)

if probe.returncode != 0:
    test_skipped('bash process substitution is not supported on this system', capability='proc_fd')

base = Path(SCRATCHDIR / 'rsync-batch-fifo')
src = base / 'src'
dest = base / 'dest'
batch_file = base / 'update.batch'
makepath(src, dest)

(src / 'payload.txt').write_text('batch payload data\n')

subprocess.run([*rsync_argv('-a', f'--write-batch={batch_file}'), f'{src}/', f'{dest}/'], check=True)

rmtree(dest)
makepath(dest)

rsync_base_cmd = shlex.join(rsync_argv('-a'))
batch_path = shlex.quote(str(batch_file))
dest_path = shlex.quote(str(dest) + '/')

bash_script = f"{rsync_base_cmd} --read-batch=<(cat {batch_path}) {dest_path}"

try:
    proc_read = subprocess.run(
        [bash, '-c', bash_script],
        capture_output=True,
        env=bash_env,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    rmtree(base)
    test_fail('process substitution batch test timed out')

ctx = f'rc={proc_read.returncode}, stderr={proc_read.stderr.strip()!r}'

if proc_read.returncode != 0:
    rmtree(base)
    test_fail(f'rsync crashed reading batch file from pipe ({ctx})')

if not (dest / 'payload.txt').is_file():
    rmtree(base)
    test_fail(f'rsync exited successfully but payload is missing in target ({ctx})')

rmtree(base)
print('rsync successfully parsed batch stream via process substitution pseudo-path')
raise SystemExit(0)
