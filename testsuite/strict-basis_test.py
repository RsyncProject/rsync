#!/usr/bin/env python3
"""TCP receiver edge-cases.

Test 1: Ensures O_NOFOLLOW correctly blocks leaf symlinks in operator paths.
Test 2: Ensures unexpected FNAMECMP_PARTIAL_DIR tokens cannot cause an 
        unintended in-place overwrite.
"""

import hashlib
import os
import socket
import struct
import subprocess
from pathlib import Path

from rsyncfns import (
    TMPDIR, TODIR, hands_setup, makepath, rmtree, rsync_argv, test_fail
)
import rsync_proto as rp

hands_setup()

CF_INPLACE_PARTIAL_DIR = 1 << 6
FNAMECMP_BASIS_DIR_LOW = 0x00
FNAMECMP_PARTIAL_DIR = 0x81
ITEM_BASIS_TYPE_FOLLOWS = 1 << 11
ITEM_XNAME_FOLLOWS = 1 << 12

TEST_DATA = b"STRICT_NOFOLLOW_REQUIRED\n"
ORIGINAL_DATA = b"ORIGINAL_DEST_BYTES_KEEP_ME\n"
MODIFIED_DATA = b"MODIFIED_INPLACE_DESPITE_BAD_CHECKSUM\n"
INVALID_MD5 = b"\x00" * 16
MODTIME = 1_700_000_000

# --- Protocol Helpers ---
def drain_argv(peer):
    nul_run = 0
    while nul_run < 2:
        b = peer._recv_exact(1)
        nul_run = nul_run + 1 if b == b"\0" else 0

def read_mux_bytes(peer, buf, n):
    while len(buf) < n:
        word = struct.unpack("<I", peer._recv_exact(4))[0]
        payload = peer._recv_exact(word & 0xFFFFFF)
        if (word >> 24) - rp.MPLEX_BASE == rp.MSG_DATA:
            buf.extend(payload)
    out = bytes(buf[:n])
    del buf[:n]
    return out

def read_mux_int(peer, buf): return struct.unpack("<i", read_mux_bytes(peer, buf, 4))[0]
def read_mux_short(peer, buf): return struct.unpack("<H", read_mux_bytes(peer, buf, 2))[0]
def read_mux_byte(peer, buf): return read_mux_bytes(peer, buf, 1)[0]

def read_mux_ndx(peer, buf):
    b0 = read_mux_byte(peer, buf)
    if b0 == 0: return rp.NDX_DONE
    if b0 == 0xFF: raise RuntimeError("generator sent negative index")
    if b0 == 0xFE:
        b = read_mux_bytes(peer, buf, 2)
        if b[0] & 0x80:
            rest = read_mux_bytes(peer, buf, 2)
            return ((b[0] & 0x7F) << 24) | b[1] | (rest[0] << 8) | (rest[1] << 16)
        return (b[0] << 8) + b[1] - 1
    return b0 - 1

def drain_generator_request(peer, buf):
    while True:
        n = read_mux_int(peer, buf)
        if n == 0: break
        read_mux_bytes(peer, buf, n)

    while True:
        ndx = read_mux_ndx(peer, buf)
        if ndx == rp.NDX_DONE: raise RuntimeError("generator sent NDX_DONE")
        iflags = read_mux_short(peer, buf)
        if iflags & ITEM_BASIS_TYPE_FOLLOWS: read_mux_byte(peer, buf)
        if iflags & ITEM_XNAME_FOLLOWS:
            ln = read_mux_byte(peer, buf)
            if ln & 0x80: ln = (ln & 0x7F) * 0x100 + read_mux_byte(peer, buf)
            if ln: read_mux_bytes(peer, buf, ln)
        count = read_mux_int(peer, buf)
        for _ in range(3): read_mux_int(peer, buf)
        for _ in range(count): read_mux_int(peer, buf)
        if iflags & rp.ITEM_TRANSFER: return ndx

def run_synthetic_sender(client_args, uri_path, dest_path, test_logic, inject_fault=True): 
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]
    cmd = rsync_argv("--protocol=30", "-r", "--no-whole-file") + client_args
    cmd += [f"rsync://127.0.0.1:{port}/{uri_path}", f"{dest_path}/"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    csock, _ = lsock.accept()
    csock.settimeout(15)
    peer = rp.DaemonReceiver(csock)

    try:
        test_logic(peer, inject_fault)
    finally:
        peer.drain(timeout=3)
        peer.close()
        lsock.close()

    stdout, _ = proc.communicate(timeout=10)
    return proc.returncode, stdout


# --- Test 1: Operator Path Leaf Symlink Rejection (O_NOFOLLOW) --------------
print("=== Test 1: Operator Path Leaf Symlink Rejection (O_NOFOLLOW) ===", flush=True)

def test1_payload(peer, inject_fault, md5_hash):
    peer.handshake(compat_flags=CF_INPLACE_PARTIAL_DIR, seed=0x12345678)
    drain_argv(peer)
    entry = rp.FileEntry("f", mode=rp.S_IFREG | 0o644, length=len(TEST_DATA), modtime=MODTIME)
    peer.send_data(entry.encode() + rp.end_of_flist())
    buf = bytearray()
    ndx = drain_generator_request(peer, buf)
    body = bytearray()
    body += rp.w_shortint(rp.ITEM_TRANSFER | ITEM_BASIS_TYPE_FOLLOWS)
    body += rp.w_byte(FNAMECMP_BASIS_DIR_LOW)  
    body += rp.w_sum_head(1, len(TEST_DATA), 16, len(TEST_DATA)) + rp.w_int(-1) + rp.w_int(0)
    body += md5_hash
    out_bytes = bytearray(peer.w_ndx(ndx))
    out_bytes += body
    out_bytes += peer.w_ndx(rp.NDX_DONE) * 4
    peer.send_data(bytes(out_bytes))

# 1A: Positive Control
print("  Running 1A: Positive Control (Regular file basis)...", flush=True)
rmtree(TODIR)
dest = TODIR / "dest"
linkdest = TODIR / "linkdest"
makepath(dest, linkdest)
(linkdest / "f").write_bytes(TEST_DATA)
correct_md5 = hashlib.md5(TEST_DATA).digest()

ret, out = run_synthetic_sender(
    [f"--link-dest={linkdest}", "--partial"], "mod/", dest, 
    lambda peer, inject_fault: test1_payload(peer, inject_fault, correct_md5), inject_fault=False
)

if not (dest / "f").exists() or (dest / "f").read_bytes() != TEST_DATA:
    test_fail("BUG 1A (Control): Positive control failed! The patch accidentally blocked a regular basis file.\nOutput:\n" + out)

# 1B: Negative Control (Testing the edge-case)
print("  Running 1B: Negative Control (Symlink basis)...", flush=True)
rmtree(TODIR)
dest = TODIR / "dest"
linkdest = TODIR / "linkdest"
makepath(dest, linkdest)
target_file = TMPDIR / "target"
target_file.write_bytes(TEST_DATA)
os.symlink(str(target_file), linkdest / "f")

ret, out = run_synthetic_sender(
    [f"--link-dest={linkdest}", "--partial"], "mod/", dest, 
    lambda peer, inject_fault: test1_payload(peer, inject_fault, INVALID_MD5), inject_fault=True
)
# DIAGNOSTIC CHECK: Ensure rsync reached the patch and evaluated it, rather than segfaulting early
if (dest / "f").exists() and (dest / "f").read_bytes() == TEST_DATA:
    test_fail("BUG 1B (Negative Control): rsync incorrectly followed the operator-path fname leaf symlink!\nOutput:\n" + out)
elif "got a block match with no basis file" not in out:
    test_fail("BUG 1B (Negative Control): rsync crashed silently!\nOutput:\n" + out)

# --- Test 2: In-place Override Verification (FNAMECMP_PARTIAL_DIR) ----------
print("\n=== Test 2: In-place Override Verification (FNAMECMP_PARTIAL_DIR) ===", flush=True)

def test2_payload(peer, inject_fault):
    peer.handshake(compat_flags=CF_INPLACE_PARTIAL_DIR, seed=0x12345678)
    drain_argv(peer)
    entry = rp.FileEntry("keep", mode=rp.S_IFREG | 0o644, length=len(MODIFIED_DATA), modtime=MODTIME)
    peer.send_data(entry.encode() + rp.end_of_flist())
    buf = bytearray()
    ndx = drain_generator_request(peer, buf)
    body = bytearray()
    
    if inject_fault:
        body += rp.w_shortint(rp.ITEM_TRANSFER | ITEM_BASIS_TYPE_FOLLOWS)
        body += rp.w_byte(FNAMECMP_PARTIAL_DIR)
    else:
        body += rp.w_shortint(rp.ITEM_TRANSFER)
        
    body += rp.w_sum_head(0, 0, 0, 0)
    body += rp.w_int(len(MODIFIED_DATA)) + MODIFIED_DATA
    body += rp.w_int(0)
    body += INVALID_MD5
    out = bytearray(peer.w_ndx(ndx))
    out += body
    out += peer.w_ndx(rp.NDX_DONE) * 4
    peer.send_data(bytes(out))

# 2A: Negative Control
print("  Running 2A: Negative Control (Synthetic Token)...", flush=True)
rmtree(TODIR)
dest2 = TODIR / "dest"
makepath(dest2)
keep_file = dest2 / "keep"
keep_file.write_bytes(ORIGINAL_DATA)

ret, out = run_synthetic_sender([], "mod/keep", dest2, test2_payload, inject_fault=True)

if keep_file.exists():
    got = keep_file.read_bytes()
    if got == MODIFIED_DATA:
        test_fail("BUG 2A (Negative Control): Synthetic FNAMECMP_PARTIAL_DIR caused rsync to incorrectly overwrite the file in-place!\nOutput:\n" + out)
    elif got != ORIGINAL_DATA:
        test_fail(f"BUG 2A (Negative Control): File modified to unknown state: {got}")
elif "failed verification" not in out:
    # If the file didn't leak, we MUST verify rsync actually ran to completion and failed safely.
    test_fail("BUG 2A (Negative Control): rsync crashed silently!\nOutput:\n" + out)
    
print("\nSUCCESS: Both operator path parsing and forced-inplace overrides were correctly handled.", flush=True)

