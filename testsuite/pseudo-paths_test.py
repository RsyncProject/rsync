"""Process substitution /dev/fd/ write pipe pseudo-paths for --log-file must not crash and must successfully write logs, but must be rejected if confined root."""

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, test_skipped,
)
if not sys.platform.startswith('linux'):
    test_skipped('Kernel pseudo-path string is a Linux-specific procfs feature')
    raise SystemExit(0)

# We require bash specifically because standard POSIX /bin/sh does not 
# guarantee support for >(...) process substitution syntax.
bash = shutil.which('bash')
if bash is None:
    test_skipped('bash is unavailable, cannot test process substitution')

# Verify the host bash actually supports process substitution
probe = subprocess.run(
    [bash, '-c', 'echo "probe" > >(cat > /dev/null)'],
    capture_output=True
)
if probe.returncode != 0:
    test_skipped('bash process substitution is not supported on this system')

base = Path(SCRATCHDIR / 'rsync-pseudo-path').resolve()
src = base / 'src'
dest = base / 'dest'
log_out = base / 'test_log.txt'
log_out_confined = base / 'test_log_confined.txt'
makepath(src, dest)

(src / 'transfer_me.txt').write_text('sync this\n')

rsync_base_cmd = shlex.join(rsync_argv('-a'))
src_path = shlex.quote(str(src) + '/')
dest_path = shlex.quote(str(dest) + '/')

log_path = shlex.quote(str(log_out))
log_path_confined = shlex.quote(str(log_out_confined))

# -------------------------------------------------------------------------
# TEST 1: Unconfined process substitution (Should Succeed)
# -------------------------------------------------------------------------
bash_script = f"{rsync_base_cmd} -v --log-file=>(cat > {log_path}) {src_path} {dest_path}"

try:
    proc = subprocess.run(
        [bash, '-c', bash_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    rmtree(base)
    test_fail('process substitution test timed out')

ctx = f'rc={proc.returncode}, stderr={proc.stderr.strip()!r}'

if proc.returncode != 0:
    rmtree(base)
    test_fail(f'rsync crashed writing to a pseudo-path log pipe ({ctx})')

if not (dest / 'transfer_me.txt').is_file():
    rmtree(base)
    test_fail(f'rsync failed to transfer the allowed file ({ctx})')

if not log_out.exists() or log_out.stat().st_size == 0:
    rmtree(base)
    test_fail(f'rsync survived, but failed to write data to the log pipe ({ctx})')

log_data = log_out.read_text()
if "transfer_me.txt" not in log_data:
    rmtree(base)
    test_fail(f'Log pipe received data, but is missing expected output: {log_data[:100]}')

print('Test 1 Passed: rsync successfully wrote logs to a process substitution pseudo-path')

# -------------------------------------------------------------------------
# TEST 2: Confined Root (Should Reject Pseudo-path)
# -------------------------------------------------------------------------
bash_script_confined = f"{rsync_base_cmd} --confine-root={dest_path} -v --log-file=>(cat > {log_path_confined}) {src_path} {dest_path}"

try:
    proc_confined = subprocess.run(
        [bash, '-c', bash_script_confined],
        capture_output=True,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    rmtree(base)
    test_fail('confined process substitution test timed out')

ctx_confined = f'rc={proc_confined.returncode}, stderr={proc_confined.stderr.strip()!r}'

# Rsync considers log-file failure a warning, so it still exits 0. 
stderr_lower = proc_confined.stderr.lower()
if "no such file or directory" in stderr_lower and "failed to open" in stderr_lower:
    if log_out_confined.exists() and log_out_confined.stat().st_size > 0:
        rmtree(base)
        test_fail(f'rsync printed an error but still wrote the confined log! ({ctx_confined})')
    print('Test 2 Passed: rsync correctly rejected the pseudo-path when confine_root was active')
else:
    rmtree(base)
    test_fail(f'rsync failed to reject the pseudo-path or had an unexpected error ({ctx_confined})')

rmtree(base)
raise SystemExit(0)
