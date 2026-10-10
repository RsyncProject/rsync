#!/usr/bin/env python3

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, rsync_command_binary,
    test_fail, test_skipped,
)
if not sys.platform.startswith('linux'):
    test_skipped('Kernel pseudo-path string is a Linux-specific procfs feature', capability='proc_fd')
    raise SystemExit(0)

bash = shutil.which('bash')
if bash is None:
    test_skipped('bash is unavailable, cannot test process substitution', capability='proc_fd')

bash_env = {k: v for k, v in os.environ.items() if k != 'POSIXLY_CORRECT'}
probe = subprocess.run(
    [bash, '-c', 'echo "probe" > >(cat > /dev/null)'],
    capture_output=True,
    env=bash_env,
)
if probe.returncode != 0:
    test_skipped('bash process substitution is not supported on this system', capability='proc_fd')

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

bash_script = f"{rsync_base_cmd} -v --log-file=>(cat > {log_path}) {src_path} {dest_path}"

try:
    proc = subprocess.run(
        [bash, '-c', bash_script],
        capture_output=True,
        env=bash_env,
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

bash_script_confined = f"{rsync_base_cmd} --confine-root={dest_path} -v --log-file=>(cat > {log_path_confined}) {src_path} {dest_path}"

try:
    proc_confined = subprocess.run(
        [bash, '-c', bash_script_confined],
        capture_output=True,
        env=bash_env,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    rmtree(base)
    test_fail('confined process substitution test timed out')

ctx_confined = f'rc={proc_confined.returncode}, stderr={proc_confined.stderr.strip()!r}'

stderr_lower = proc_confined.stderr.lower()
if "no such file or directory" in stderr_lower and "failed to open" in stderr_lower:
    if log_out_confined.exists() and log_out_confined.stat().st_size > 0:
        rmtree(base)
        test_fail(f'rsync printed an error but still wrote the confined log! ({ctx_confined})')
    print('Test 2 Passed: rsync correctly rejected the pseudo-path when confine_root was active')
else:
    rmtree(base)
    test_fail(f'rsync failed to reject the pseudo-path or had an unexpected error ({ctx_confined})')

rmtree(dest)
makepath(dest)
trailing_script = (
    f'pipe_path=<(printf "transfer_me.txt\\n"); '
    f'{rsync_base_cmd} --exclude-from="$pipe_path/trailing" {src_path} {dest_path}'
)
try:
    proc_trailing = subprocess.run(
        [bash, '-c', trailing_script],
        capture_output=True,
        env=bash_env,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    rmtree(base)
    test_fail('trailing-component pseudo-path test timed out')

if proc_trailing.returncode == 0:
    rmtree(base)
    test_fail('/dev/fd/N/trailing unexpectedly opened descriptor N')
if (dest / 'transfer_me.txt').exists():
    rmtree(base)
    test_fail('transfer continued after accepting a trailing pseudo-path component')

print("Running Test 3: Standard I/O Symlinks (/dev/stdin)...", flush=True)

rmtree(dest)
makepath(dest)
(src / 'stdin_test.txt').write_text('stdin data\n')

stdin_script = f'printf "stdin_test.txt\\n" | {rsync_base_cmd} --files-from=/dev/stdin {src_path} {dest_path}'
proc_stdin = subprocess.run([bash, '-c', stdin_script], env=bash_env, capture_output=True, text=True, timeout=10)

if proc_stdin.returncode != 0:
    test_fail(f'rsync failed to read --files-from=/dev/stdin (rc={proc_stdin.returncode}, stderr={proc_stdin.stderr.strip()!r})')

if not (dest / 'stdin_test.txt').is_file():
    test_fail(f'rsync failed to transfer file specified via /dev/stdin')

print('Test 3A Passed: rsync successfully read files-from via /dev/stdin', flush=True)

rmtree(dest)
makepath(dest)

symlink_list = base / 'rsync.list'
if symlink_list.is_symlink() or symlink_list.exists():
    symlink_list.unlink()

symlink_list.symlink_to('/dev/stdin')

symlink_script = f'printf "stdin_test.txt\\n" | {rsync_base_cmd} --files-from={shlex.quote(str(symlink_list))} {src_path} {dest_path}'
proc_symlink = subprocess.run([bash, '-c', symlink_script], env=bash_env, capture_output=True, text=True, timeout=10)

if proc_symlink.returncode != 0:
    test_fail(f'/dev/stdin files-from symlink failed: rc={proc_symlink.returncode}, stderr={proc_symlink.stderr.strip()!r}')

if not (dest / 'stdin_test.txt').is_file():
     test_fail(f'rsync failed to follow trusted symlink to /dev/stdin to read files-from list')

print('Test 3B Passed: rsync successfully resolved a trusted nested symlink to /dev/stdin', flush=True)

print("Running Test 3C: Spoofed Relative Path to Pseudo-pipe (dev/fd/99)...", flush=True)

rmtree(dest)
makepath(dest)

spoofed_dev_fd = base / 'dev' / 'fd'
makepath(spoofed_dev_fd)

spoofed_target = spoofed_dev_fd / 'pipe:['
spoofed_target.write_text('transfer_me.txt\n')

spoofed_symlink = spoofed_dev_fd / '99'
if spoofed_symlink.is_symlink() or spoofed_symlink.exists():
    spoofed_symlink.unlink()
spoofed_symlink.symlink_to('pipe:[')

spoofed_script = f'cd {shlex.quote(str(base))} && {rsync_base_cmd} --files-from=dev/fd/99 {src_path} {dest_path}'
proc_spoofed = subprocess.run([bash, '-c', spoofed_script], env=bash_env, capture_output=True, text=True, timeout=10)

stderr_lower = proc_spoofed.stderr.lower()

if "levels of symbolic links" in stderr_lower or "eloop" in stderr_lower:
    test_fail(f'spoofed pseudo-path returned ELOOP: rc={proc_spoofed.returncode}, stderr={proc_spoofed.stderr.strip()!r}')

if proc_spoofed.returncode != 0:
    test_fail(f'rsync unexpectedly did not complete the transfer! (stderr={proc_spoofed.stderr.strip()!r})')

print('Test 3C Passed: rsync correctly treated the un-anchored spoofed path as a normal file rather than a kernel pseudo-path.', flush=True)

unshare = shutil.which('unshare')
if unshare is None:
    print('unshare is unavailable')
    rmtree(base)
    raise SystemExit(0)

launcher = []
if os.geteuid() == 0:
    setpriv = shutil.which('setpriv')
    if setpriv is None:
        print('setpriv is unavailable for the root-run testsuite')
        rmtree(base)
        raise SystemExit(0)
    launcher = [setpriv, '--reuid=65534', '--regid=65534', '--clear-groups']

unshare_argv = [unshare, '--user', '--map-root-user', '--mount', '--pid',
                '--fork', '--mount-proc']

probe = subprocess.run(
    launcher + unshare_argv + ['true'],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
if probe.returncode != 0:
    rmtree(base)
    print(f'user namespaces unavailable (rc={probe.returncode})')
    raise SystemExit(0)

check_ns = (
    "import os\n"
    "proc_uid = os.lstat('/proc/self').st_uid\n"
    "if proc_uid in (0, os.geteuid()):\n"
    "    exit(22)\n"
)
probe_uid = subprocess.run(launcher + unshare_argv + [sys.executable, '-c', check_ns])
if probe_uid.returncode == 22:
    rmtree(base)
    print('/proc/self does not expose an overflow uid in this namespace')
    raise SystemExit(0)

ws_base = Path(tempfile.mkdtemp(prefix='rsync-unshare-'))
ws_base.chmod(0o777)

try:
    ws_src = ws_base / 'src'
    ws_dest = ws_base / 'dest'
    makepath(ws_src, ws_dest)
    ws_src.chmod(0o777)
    ws_dest.chmod(0o777)

    tf = ws_src / 'transfer_me.txt'
    tf.write_text('sync this\n')
    tf.chmod(0o777)

    sf = ws_src / 'stdin_test.txt'
    sf.write_text('stdin data\n')
    sf.chmod(0o777)

    local_bin = ws_base / 'rsync-bin'
    shutil.copy2(rsync_command_binary(), local_bin)
    local_bin.chmod(0o777)

    cmd_prefix = shlex.join([str(local_bin), '-a'])
    s_path = shlex.quote(str(ws_src) + '/')
    d_path = shlex.quote(str(ws_dest) + '/')

    inner_script_4a = f'{cmd_prefix} --no-o --no-g --files-from=<(printf "stdin_test.txt\\n") {s_path} {d_path}'
    unshare_cmd_4a = launcher + unshare_argv + [bash, '-c', inner_script_4a]

    proc_unshare_4a = subprocess.run(
        unshare_cmd_4a,
        capture_output=True,
        env=bash_env,
        text=True,
        timeout=10,
    )

    ctx_unshare_4a = f'rc={proc_unshare_4a.returncode}, stderr={proc_unshare_4a.stderr.strip()!r}'

    if proc_unshare_4a.returncode != 0:
        test_fail(f'rsync failed reading process substitution pseudo-path inside user namespace ({ctx_unshare_4a})')

    if not (ws_dest / 'stdin_test.txt').is_file():
        test_fail(f'rsync failed to transfer file specified via process substitution inside user namespace ({ctx_unshare_4a})')

    print('Test 4A Passed: rsync successfully resolved process substitution pseudo-paths inside user namespace', flush=True)

    rmtree(ws_dest)
    makepath(ws_dest)
    ws_dest.chmod(0o777)

    symlink_list_ns = ws_base / 'rsync.list'
    if symlink_list_ns.is_symlink() or symlink_list_ns.exists():
        symlink_list_ns.unlink()

    symlink_list_ns.symlink_to('/dev/stdin')

    if os.geteuid() == 0:
        os.lchown(symlink_list_ns, 65534, 65534)

    inner_script_4b = f'printf "stdin_test.txt\\n" | {cmd_prefix} --no-o --no-g --files-from={shlex.quote(str(symlink_list_ns))} {s_path} {d_path}'
    unshare_cmd_4b = launcher + unshare_argv + [bash, '-c', inner_script_4b]

    proc_unshare_4b = subprocess.run(
        unshare_cmd_4b,
        capture_output=True,
        env=bash_env,
        text=True,
        timeout=10,
    )
    ctx_unshare_4b = f'rc={proc_unshare_4b.returncode}, stderr={proc_unshare_4b.stderr.strip()!r}'
    if proc_unshare_4b.returncode != 0:
        test_fail(f'rsync failed with --files-from=rsync.list -> /dev/stdin inside user namespace ({ctx_unshare_4b})')

    if not (ws_dest / 'stdin_test.txt').is_file():
        test_fail(f'rsync failed to transfer the file specified via nested /dev/stdin symlink in namespace ({ctx_unshare_4b})')
    print('Test 4B Passed: rsync successfully resolved a trusted nested symlink to /dev/stdin in user namespace', flush=True)

finally:
    rmtree(ws_base)

rmtree(base)
raise SystemExit(0)
