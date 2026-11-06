"""Regression test for --stream-source option validation, bounds, and refused flags."""

import os
from pathlib import Path
import threading

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail, test_skipped,
)

if not hasattr(os, 'mkfifo'):
    test_skipped('os.mkfifo is not available on this platform')

_probe = run_rsync('--help', capture_output=True, check=False)
if _probe.returncode != 0 or '--stream-source' not in _probe.stdout:
    test_skipped('rsync binary does not support --stream-source')


def expect_fail(label: str, *args, expected_err: str = ''):
    print(f"[RUN] {label}", flush=True)
    proc = run_rsync(*args, capture_output=True, check=False)
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


def run_stream_transfer(label: str, fifo_path: Path, payload: bytes, dst_path: Path, *extra_args):
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
    if proc.returncode != 0:
        test_fail(
            f"[FAIL] {label}: rsync exited with code {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    if writer_exc:
        test_fail(f"[FAIL] {label}: FIFO writer failed: {writer_exc[0]}")
    print(f"[PASS] {label}", flush=True)
    return proc


rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

fifo = FROMDIR / 'stream.fifo'
try:
    os.mkfifo(fifo)
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
for sub_opt in ('--stream-max-size=1M', '--stream-timeout=5', '--stream-pipe-sz=1M'):
    expect_fail(
        f"Refuse {sub_opt} without --stream-source",
        sub_opt, f'{reg_src}', f'{dst}',
        expected_err='--stream-source',
    )

# ---------------------------------------------------------------------------
# Part 3: --stream-max-size validation (missing, <= 0, invalid, truncation log)
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

# Stream exceeding --stream-max-size logs warning and stops at limit
if dst.exists():
    dst.unlink()
over_payload = b'X' * 4096
ref_1k = FROMDIR / 'ref_1k.bin'
ref_1k.write_bytes(over_payload[:1024])

proc_over = run_stream_transfer(
    "Stream exceeding --stream-max-size=1024 logs and stops at limit",
    fifo, over_payload, dst,
    '--stream-max-size=1024',
)
combined_over = (proc_over.stdout + proc_over.stderr).lower()
if 'exceeded' not in combined_over and 'limit' not in combined_over:
    test_fail(
        f"[FAIL] Expected warning when stream exceeds --stream-max-size, got:\n"
        f"--- stdout ---\n{proc_over.stdout}\n"
        f"--- stderr ---\n{proc_over.stderr}"
    )
assert_same(dst, ref_1k, label="stream-max-size=1024 cap")

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
# Part 5: --stream-timeout (<= 0 disables timeout; positive expires on stall)
# ---------------------------------------------------------------------------
for no_timeout in ('0', '-1'):
    if dst.exists():
        dst.unlink()
    run_stream_transfer(
        f"Disabled timeout with --stream-timeout={no_timeout}",
        fifo, payload_4k, dst,
        '--stream-max-size=1M', f'--stream-timeout={no_timeout}',
    )
    assert_same(dst, ref_4k, label=f"timeout={no_timeout}")

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

expect_fail(
    "Refuse multiple sources with --stream-source",
    '--stream-source', '--stream-max-size=1M', f'{fifo}', f'{reg_src}', f'{TODIR}/',
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
