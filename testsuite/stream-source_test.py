"""Regression test for --stream-source option validation, bounds, and refused flags."""

import os
from pathlib import Path
import resource
import subprocess
import threading
import time

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, rsync_argv, run_rsync, test_fail, test_skipped,
)

if not hasattr(os, 'mkfifo'):
    test_skipped('os.mkfifo is not available on this platform')

_probe = run_rsync('--help', capture_output=True, check=False)
if _probe.returncode != 0 or '--stream-source' not in _probe.stdout:
    test_skipped('rsync binary does not support --stream-source')


def run_high_fd_stream_test():
    target_fds = 2048
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < target_fds + 64:
        new_soft = min(hard, target_fds + 128) if hard != resource.RLIM_INFINITY else target_fds + 128
        if new_soft < target_fds + 64:
            print(f"[SKIP] High-FD test requires RLIMIT_NOFILE >= {target_fds + 64} (hard={hard})", flush=True)
            return
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))

    label = f"High inherited descriptors (>= {target_fds}) stream transfer"
    print(f"[RUN] {label}", flush=True)
    if dst.exists():
        dst.unlink()

    created_fds = []
    occupied_fds = []
    writer_exc = []

    def writer():
        try:
            fd = os.open(fifo, os.O_WRONLY)
            try:
                os.write(fd, payload_4k)
            finally:
                os.close(fd)
        except Exception as exc:
            writer_exc.append(exc)

    pipe_r, pipe_w = os.pipe()
    created_fds.extend([pipe_r, pipe_w])
    try:
        while True:
            dup_fd = os.dup(pipe_r)
            created_fds.append(dup_fd)
            if dup_fd >= target_fds:
                break

        for fd in range(3, target_fds + 1):
            try:
                os.fstat(fd)
                occupied_fds.append(fd)
            except OSError:
                pass

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        argv = rsync_argv(
            '--stream-source',
            '--stream-max-size=1M',
            '--stream-timeout=10',
            f'{fifo}',
            f'{dst}',
        )
        proc = subprocess.run(
            argv,
            pass_fds=occupied_fds,
            capture_output=True,
            text=True,
            timeout=30,
        )
        t.join(timeout=10)
    finally:
        for fd in created_fds:
            os.close(fd)

    if proc.returncode != 0:
        test_fail(
            f"[FAIL] {label}: rsync exited with code {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    if writer_exc:
        test_fail(f"[FAIL] {label}: FIFO writer failed: {writer_exc[0]}")
    assert_same(dst, ref_4k, label="high-fd stream transfer")
    print(f"[PASS] {label}", flush=True)


def expect_fail(label: str, *args, expected_err: str = '', timeout: float = 30.0):
    print(f"[RUN] {label}", flush=True)
    argv = rsync_argv(*args)
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode == 0:
        test_fail(
            f"[FAIL] {label}: expected non-zero exit code, got 0\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    combined = (proc.stdout + proc.stderr).lower()
    if expected_err and expected_err.lower() not in combined:
        test_fail(
            f"[FAIL] {label}: expected '{expected_err}' in output, got:\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    print(f"[PASS] {label} (exit={proc.returncode})", flush=True)
    return proc


def run_stream_transfer(label: str, fifo_path: Path, payload: bytes, dst_path: Path, *extra_args, expect_success: bool = True):
    print(f"[RUN] {label}", flush=True)
    writer_exc = []

    def writer():
        try:
            fd = os.open(fifo_path, os.O_WRONLY)
            try:
                view = memoryview(payload)
                total = 0
                while total < len(view):
                    written = os.write(fd, view[total : total + 65536])
                    total += written
            except BrokenPipeError:
                pass
            finally:
                os.close(fd)
        except Exception as exc:
            writer_exc.append(exc)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    proc = run_rsync(
        '--stream-source',
        *extra_args,
        f'{fifo_path}',
        f'{dst_path}',
        capture_output=True,
        check=False,
    )
    t.join(timeout=10)
    if writer_exc:
        test_fail(f"[FAIL] {label}: FIFO writer failed: {writer_exc[0]}")
    if expect_success and proc.returncode != 0:
        test_fail(
            f"[FAIL] {label}: rsync exited with code {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    if not expect_success and proc.returncode == 0:
        test_fail(
            f"[FAIL] {label}: expected non-zero exit code, got 0\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    print(f"[PASS] {label} (exit={proc.returncode})", flush=True)
    return proc


rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

fifo = FROMDIR / 'stream.fifo'
fifo2 = FROMDIR / 'stream2.fifo'
try:
    os.mkfifo(fifo)
    os.mkfifo(fifo2)
except OSError as exc:
    test_skipped(f"cannot create FIFO in test filesystem: {exc}")

reg_src = FROMDIR / 'reg.bin'
reg_src.write_bytes(b'regular file payload')
dst = TODIR / 'dst.bin'

# ---------------------------------------------------------------------------
# Part 1: Help output (--help includes stream options and FIFO usage)
# ---------------------------------------------------------------------------
label = "Main --help includes --stream-source options and FIFO usage"
print(f"[RUN] {label}", flush=True)
proc_main_help = run_rsync('--help', capture_output=True, check=False)
if proc_main_help.returncode != 0:
    test_fail(f"[FAIL] {label}: exited with {proc_main_help.returncode}\n{proc_main_help.stderr}")
for expected in (
    'FIFO DEST',
    '--stream-source',
    '--stream-max-size=SIZE',
    '--stream-timeout=SECONDS',
    '--stream-pipe-sz=SIZE',
):
    if expected not in proc_main_help.stdout:
        test_fail(f"[FAIL] {label}: missing '{expected}' in help output:\n{proc_main_help.stdout}")
print(f"[PASS] {label}", flush=True)

# ---------------------------------------------------------------------------
# Part 2: Sub-options used without --stream-source must be refused
# ---------------------------------------------------------------------------
for sub_opt in ('--stream-max-size=1M', '--stream-timeout=30', '--stream-pipe-sz=1M'):
    expect_fail(
        f"Refuse {sub_opt} without --stream-source",
        sub_opt, f'{reg_src}', f'{dst}',
        expected_err='--stream-source',
    )

# ---------------------------------------------------------------------------
# Part 3: --stream-max-size validation (missing, <= 0, invalid, exact, excess)
# ---------------------------------------------------------------------------
expect_fail(
    "Missing --stream-max-size with --stream-source",
    '--stream-source', f'{fifo}', f'{dst}',
    expected_err='--stream-max-size',
)

for bad_max in ('0', '0B', '-1', '-10M', 'invalid'):
    expect_fail(
        f"Invalid --stream-max-size={bad_max}",
        '--stream-source', f'--stream-max-size={bad_max}', f'{fifo}', f'{dst}',
    )

# Exact-limit stream completes cleanly without hanging
exact_1k = b'E' * 1024
ref_exact_1k = FROMDIR / 'ref_exact_1k.bin'
ref_exact_1k.write_bytes(exact_1k)
if dst.exists():
    dst.unlink()

run_stream_transfer(
    "Exact-limit stream (--stream-max-size=1024) completes cleanly",
    fifo, exact_1k, dst,
    '--stream-max-size=1024', '--stream-timeout=5',
)
assert_same(dst, ref_exact_1k, label="exact stream-max-size=1024")

# Stream exceeding --stream-max-size fails before finalisation and does not commit truncated output
sentinel_bytes = b'ORIGINAL_INTACT_DESTINATION_CONTENT'
ref_sentinel = FROMDIR / 'ref_sentinel.bin'
ref_sentinel.write_bytes(sentinel_bytes)
dst.write_bytes(sentinel_bytes)

over_payload = b'X' * 4096
proc_over = run_stream_transfer(
    "Stream exceeding --stream-max-size=1024 fails without committing truncated output",
    fifo, over_payload, dst,
    '--stream-max-size=1024', '--stream-timeout=5',
    expect_success=False,
)
combined_over = (proc_over.stdout + proc_over.stderr).lower()
if 'exceeded' not in combined_over:
    test_fail(
        f"[FAIL] Expected error mentioning 'exceeded' when stream exceeds --stream-max-size, got:\n"
        f"--- stdout ---\n{proc_over.stdout}\n"
        f"--- stderr ---\n{proc_over.stderr}"
    )
assert_same(dst, ref_sentinel, label="existing dst preserved on oversized stream failure")

# Exact-limit stream that stalls before EOF hits --stream-timeout before finalisation
dst.write_bytes(sentinel_bytes)
exact_stall_stop = threading.Event()


def exact_limit_stalled_writer():
    try:
        fd = os.open(fifo, os.O_WRONLY)
        try:
            os.write(fd, exact_1k)
            exact_stall_stop.wait(timeout=5)
        finally:
            os.close(fd)
    except Exception:
        pass


t_exact_stall = threading.Thread(target=exact_limit_stalled_writer, daemon=True)
t_exact_stall.start()
try:
    expect_fail(
        "Exact-limit stalled stream hits --stream-timeout=1 before finalisation",
        '--stream-source', '--stream-max-size=1024', '--stream-timeout=1',
        f'{fifo}', f'{dst}',
        expected_err='timed out',
    )
finally:
    exact_stall_stop.set()
    t_exact_stall.join(timeout=5)
assert_same(dst, ref_sentinel, label="existing dst preserved on exact-limit stall timeout")

# ---------------------------------------------------------------------------
# Part 4: --stream-pipe-sz min/max bounds (64KB .. 16MB)
# ---------------------------------------------------------------------------
for bad_pipe_sz in ('0', '-1', '32K', '65535', '17M', '16777217', 'invalid'):
    expect_fail(
        f"Out-of-bounds --stream-pipe-sz={bad_pipe_sz}",
        '--stream-source', '--stream-max-size=1M', f'--stream-pipe-sz={bad_pipe_sz}',
        f'{fifo}', f'{dst}',
    )

payload_4k = b'A' * 4096
ref_4k = FROMDIR / 'ref_4k.bin'
ref_4k.write_bytes(payload_4k)

for valid_pipe_sz in ('64K', '65536', '1M', '16M', '16777216'):
    if dst.exists():
        dst.unlink()
    run_stream_transfer(
        f"Valid --stream-pipe-sz={valid_pipe_sz}",
        fifo, payload_4k, dst,
        '--stream-max-size=1M', f'--stream-pipe-sz={valid_pipe_sz}',
    )
    assert_same(dst, ref_4k, label=f"pipe-sz={valid_pipe_sz}")

# ---------------------------------------------------------------------------
# Part 5: --stream-timeout (<= 0 disables timeout on paused stream; > 0 expires)
# ---------------------------------------------------------------------------
for no_timeout in ('0', '-1'):
    label = f"Paused stream succeeds with disabled --stream-timeout={no_timeout}"
    print(f"[RUN] {label}", flush=True)
    if dst.exists():
        dst.unlink()
    writer_exc = []

    def delayed_writer():
        try:
            fd = os.open(fifo, os.O_WRONLY)
            try:
                os.write(fd, payload_4k[:2048])
                time.sleep(1.2)
                os.write(fd, payload_4k[2048:])
            finally:
                os.close(fd)
        except Exception as exc:
            writer_exc.append(exc)

    t_delayed = threading.Thread(target=delayed_writer, daemon=True)
    t_delayed.start()
    proc_nt = run_rsync(
        '--stream-source', '--stream-max-size=1M', f'--stream-timeout={no_timeout}',
        f'{fifo}', f'{dst}',
        capture_output=True,
        check=False,
    )
    t_delayed.join(timeout=10)
    if writer_exc:
        test_fail(f"[FAIL] {label}: FIFO writer failed: {writer_exc[0]}")
    if proc_nt.returncode != 0:
        test_fail(
            f"[FAIL] {label}: rsync exited with code {proc_nt.returncode}\n"
            f"--- stdout ---\n{proc_nt.stdout}\n"
            f"--- stderr ---\n{proc_nt.stderr}"
        )
    assert_same(dst, ref_4k, label=f"timeout={no_timeout}")
    print(f"[PASS] {label}", flush=True)

stall_stop = threading.Event()


def stalled_writer():
    try:
        fd = os.open(fifo, os.O_WRONLY)
        try:
            os.write(fd, b'partial bytes before stall')
            stall_stop.wait(timeout=5)
        finally:
            os.close(fd)
    except Exception:
        pass


t_stall = threading.Thread(target=stalled_writer, daemon=True)
t_stall.start()
try:
    expect_fail(
        "Stalled stream hits --stream-timeout=1",
        '--stream-source', '--stream-max-size=1M', '--stream-timeout=1',
        f'{fifo}', f'{dst}',
        expected_err='timed out',
    )
finally:
    stall_stop.set()
    t_stall.join(timeout=5)

# ---------------------------------------------------------------------------
# Part 6: Refused / incompatible options with --stream-source
# ---------------------------------------------------------------------------
refused_flags = [
    ('-r',),
    ('-a',),
    ('-d',),
    ('--append',),
    ('--append-verify',),
    ('-c',),
    ('--checksum',),
    ('--size-only',),
    ('--max-size=1M',),
    ('--min-size=1K',),
    ('-y',),
    ('--fuzzy',),
    ('--remove-source-files',),
    ('--preallocate',),
]

for flags in refused_flags:
    flag_str = ' '.join(flags)
    expect_fail(
        f"Refuse incompatible flag {flag_str} with --stream-source",
        '--stream-source', '--stream-max-size=1M', *flags, f'{fifo}', f'{dst}',
    )

flist_file = FROMDIR / 'flist.txt'
flist_file.write_text('stream.fifo\n')
expect_fail(
    "Refuse --files-from with --stream-source",
    '--stream-source', '--stream-max-size=1M', f'--files-from={flist_file}',
    f'{FROMDIR}', f'{TODIR}',
)

batch_prefix = TODIR / 'batch_out'
expect_fail(
    "Refuse --write-batch with --stream-source",
    '--stream-source', '--stream-max-size=1M', f'--write-batch={batch_prefix}',
    f'{fifo}', f'{dst}',
)

# Reject multiple sources before opening either FIFO
expect_fail(
    "Refuse multiple FIFO sources (fifo, fifo2) before opening either FIFO",
    '--stream-source', '--stream-max-size=1M', f'{fifo}', f'{fifo2}', f'{TODIR}/',
    expected_err='multiple',
    timeout=5.0,
)

expect_fail(
    "Refuse multiple mixed sources (fifo, reg_src) with --stream-source",
    '--stream-source', '--stream-max-size=1M', f'{fifo}', f'{reg_src}', f'{TODIR}/',
    expected_err='multiple',
    timeout=5.0,
)

expect_fail(
    "Refuse --daemon with --stream-source",
    '--daemon', '--stream-source', '--stream-max-size=1M',
    expected_err='--stream-source',
)

expect_fail(
    "Refuse --server with --stream-source",
    '--server', '--stream-source', '--stream-max-size=1M', '.', f'{dst}',
    expected_err='--stream-source',
)

expect_fail(
    "Refuse remote shell source pull (HOST:SRC) with --stream-source",
    '--stream-source', '--stream-max-size=1M', f'localhost:{fifo}', f'{dst}',
    expected_err='--stream-source',
)

expect_fail(
    "Refuse remote daemon source pull (HOST::SRC) with --stream-source",
    '--stream-source', '--stream-max-size=1M', 'localhost::module/stream.fifo', f'{dst}',
    expected_err='--stream-source',
)

expect_fail(
    "Refuse rsync:// URL source pull with --stream-source",
    '--stream-source', '--stream-max-size=1M', 'rsync://localhost/module/stream.fifo', f'{dst}',
    expected_err='--stream-source',
)

run_high_fd_stream_test()
