#!/usr/bin/env python3
"""Regression test for --stream-source EOF length updates and stats accuracy."""

import os
from pathlib import Path
import re
import threading

from rsyncfns import (
    FROMDIR, TODIR, assert_same, rmtree, run_rsync, test_fail,
)

if not hasattr(os, 'mkfifo'):
    test_skipped('os.mkfifo is not available on this platform')

_probe = run_rsync('--help', capture_output=True, check=False)
if _probe.returncode != 0 or '--stream-source' not in _probe.stdout:
    test_skipped('rsync binary does not support --stream-source')

WIN_SZ = 263168
BLOCK = 700
TAIL_LEN = 123
C_BYTE = 100


def make_det_bytes(length: int, seed: int) -> bytes:
    out = bytearray(length)
    x = (seed & 0xFFFFFFFF) or 0x12345678
    for i in range(length):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17) & 0xFFFFFFFF
        x ^= (x << 5) & 0xFFFFFFFF
        out[i] = x & 0xFF
    return bytes(out)


def make_spread_colliding_block(delta: int, fill: int = C_BYTE) -> bytes:
    b = bytearray([fill]) * BLOCK
    b[0] = fill + delta
    b[349] = fill - delta
    b[350] = fill - delta
    b[699] = fill + delta
    return bytes(b)


def parse_stats(label: str, out: str):
    m_delta = re.search(
        r'total:\s+matches=(\d+)\s+hash_hits=(\d+)\s+false_alarms=(\d+)\s+data=(\d+)',
        out,
    )
    m_fsize = re.search(r'Total file size:\s*([\d,.]+)\s*bytes', out)
    m_lit = re.search(r'Literal data:\s*([\d,.]+)\s*bytes', out)
    m_match = re.search(r'Matched data:\s*([\d,.]+)\s*bytes', out)
    if not m_delta or not m_fsize or not m_lit or not m_match:
        test_fail(f"[{label}] could not find deltasum2/stats in rsync output:\n{out}")
    return {
        'matches': int(m_delta.group(1)),
        'hash_hits': int(m_delta.group(2)),
        'false_alarms': int(m_delta.group(3)),
        'delta_data': int(m_delta.group(4)),
        'total_file_size': int(m_fsize.group(1).replace(',', '').replace('.', '')),
        'literal': int(m_lit.group(1).replace(',', '').replace('.', '')),
        'matched': int(m_match.group(1).replace(',', '').replace('.', '')),
    }


def run_stream_rsync(label: str, fifo_path: Path, payload: bytes, dst_path: Path, *extra_args):
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
            finally:
                os.close(fd)
        except Exception as exc:
            writer_exc.append(exc)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    proc = run_rsync(
        '--stream-source',
        '--stream-max-size=10M',
        '--no-whole-file',
        f'--block-size={BLOCK}',
        '--no-compress',
        '--stats',
        '--debug=deltasum2',
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
    return proc


def assert_matches_regular(
    label: str,
    fifo_path: Path,
    payload: bytes,
    basis: bytes,
    *extra_args,
):
    print(f"[RUN] {label} (payload={len(payload)}B, basis={len(basis)}B)", flush=True)
    src_reg = FROMDIR / 'ref_src.bin'
    dst_reg = TODIR / 'dst_reg.bin'
    dst_stream = TODIR / 'dst_stream.bin'

    src_reg.write_bytes(payload)
    dst_reg.write_bytes(basis)
    dst_stream.write_bytes(basis)

    proc_reg = run_rsync(
        '-I',
        '--no-whole-file',
        f'--block-size={BLOCK}',
        '--no-compress',
        '--stats',
        '--debug=deltasum2',
        *extra_args,
        f'{src_reg}',
        f'{dst_reg}',
        capture_output=True,
    )
    proc_stream = run_stream_rsync(label, fifo_path, payload, dst_stream, *extra_args)

    s_reg = parse_stats(label, proc_reg.stdout + proc_reg.stderr)
    s_stream = parse_stats(label, proc_stream.stdout + proc_stream.stderr)

    actual_len = len(payload)

    if s_stream['literal'] + s_stream['matched'] != actual_len:
        test_fail(
            f"[FAIL] {label}: stream length not updated! "
            f"literal ({s_stream['literal']}) + matched ({s_stream['matched']}) "
            f"!= actual stream length ({actual_len})"
        )

    if s_stream['delta_data'] != s_stream['literal']:
        test_fail(
            f"[FAIL] {label}: deltasum2 data={s_stream['delta_data']} "
            f"!= literal={s_stream['literal']}"
        )

    for k in ('matches', 'hash_hits', 'false_alarms', 'delta_data', 'literal', 'matched'):
        if s_reg[k] != s_stream[k]:
            test_fail(
                f"[FAIL] {label}: stream stat '{k}' differs from regular file!\n"
                f"  regular = {s_reg}\n"
                f"  stream  = {s_stream}"
            )

    assert_same(dst_stream, src_reg, label=label)
    print(f"[PASS] {label}: {s_stream}", flush=True)
    return s_stream


rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

fifo = FROMDIR / 'stream.fifo'
os.mkfifo(fifo)

basis_10 = make_det_bytes(BLOCK * 10, seed=101)
prefix_350 = make_det_bytes(350, seed=202)
tail_123 = make_det_bytes(TAIL_LEN, seed=303)
basis_short_tail = basis_10 + tail_123
random_800 = make_det_bytes(800, seed=404)
zeros_basis_10 = b'\x00' * (BLOCK * 10)

# ---------------------------------------------------------------------------
# Part 1: Early-EOF (< 263,168B initial window)
# ---------------------------------------------------------------------------
assert_matches_regular(
    'Early 1: 0-byte EOF against zero basis',
    fifo, b'', zeros_basis_10,
)

assert_matches_regular(
    'Early 2: 100B stream < last basis block',
    fifo, b'\x00' * 100, zeros_basis_10,
)

assert_matches_regular(
    'Early 3: exact 123B short tail block match at offset=0',
    fifo, tail_123, basis_short_tail,
)

assert_matches_regular(
    'Early 4: exact 1-block match at EOF',
    fifo, basis_10[:BLOCK], basis_10,
)

assert_matches_regular(
    'Early 5: exact 3-block match at EOF',
    fifo, basis_10[: 3 * BLOCK], basis_10,
)

assert_matches_regular(
    'Early 6: unaligned 350B literal + 1-block match at EOF',
    fifo, prefix_350 + basis_10[:BLOCK], basis_10,
)

assert_matches_regular(
    'Early 7: short tail basis + unmatched 800B stream',
    fifo, random_800, basis_short_tail,
)

assert_matches_regular(
    'Early 8: 350B literal + final short basis block (123B) at EOF',
    fifo, prefix_350 + tail_123, basis_short_tail,
)

head_100 = make_det_bytes(100, seed=505) + (b'\x00' * (BLOCK * 2))
assert_matches_regular(
    'Early 9: --inplace zero-block realignment at early EOF',
    fifo, head_100, zeros_basis_10,
    '--inplace',
)

# ---------------------------------------------------------------------------
# Part 2: Late-EOF (> 263,168B initial def_window_size)
# ---------------------------------------------------------------------------
blk0 = make_det_bytes(BLOCK, seed=606)
basis_2 = blk0 + tail_123
random_win = make_det_bytes(WIN_SZ, seed=707)
random_577 = make_det_bytes(577, seed=808)

s_late1 = assert_matches_regular(
    'Late 1: EOF after 1st window + 123B short tail match',
    fifo, random_win + tail_123, basis_2,
)

s_late2 = assert_matches_regular(
    'Late 2: exact 263,168B window boundary EOF',
    fifo, random_win[: WIN_SZ - TAIL_LEN] + tail_123, basis_2,
)

s_late3 = assert_matches_regular(
    'Late 3: 263,168B window boundary + 1B EOF in null_hash',
    fifo, random_win[: WIN_SZ - TAIL_LEN + 1] + tail_123, basis_2,
)

s_late4 = assert_matches_regular(
    'Late 4: 263,168B window boundary - 1B EOF',
    fifo, random_win[: WIN_SZ - TAIL_LEN - 1] + tail_123, basis_2,
)

s_late5 = assert_matches_regular(
    'Late 5: match at 262,467B + post-matched() hits EOF across boundary',
    fifo,
    random_win[: WIN_SZ - BLOCK - 1] + blk0 + random_577[:78] + tail_123,
    basis_2,
)

s_late6 = assert_matches_regular(
    'Late 6: match before boundary + null_hash EOF at boundary',
    fifo,
    random_win[: WIN_SZ - 1400] + blk0 + random_577 + tail_123,
    basis_2,
)

zeros_basis_large = (b'\x00' * (BLOCK * 400)) + tail_123
s_late7 = assert_matches_regular(
    'Late 7: --inplace realignment lookahead hits EOF across boundary',
    fifo,
    random_win[: WIN_SZ - 708] + (b'\x00' * 720),
    zeros_basis_large,
    '--inplace',
)

coll_block = make_spread_colliding_block(5)
basis_coll_tail = (coll_block * 4) + tail_123
flood_with_tail = bytes([C_BYTE]) * (WIN_SZ - 101) + tail_123
s_late8 = assert_matches_regular(
    'Late 8: collision skip hits EOF across boundary + clamps k for tail_123',
    fifo, flood_with_tail, basis_coll_tail,
)
