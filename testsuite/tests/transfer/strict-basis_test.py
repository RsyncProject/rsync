#!/usr/bin/env python3
"""Ensure forged partial-dir basis tokens cannot enable in-place writes."""

import hashlib
import os
import socket
import struct
import subprocess

from rsyncfns import (
    TODIR,
    hands_setup,
    makepath,
    rmtree,
    rsync_argv,
    test_fail,
    test_skipped,
)
import rsync_proto as rp

hands_setup()

CF_INPLACE_PARTIAL_DIR = 1 << 6
FNAMECMP_BASIS_DIR_LOW = 0x00
FNAMECMP_PARTIAL_DIR = 0x81
ITEM_BASIS_TYPE_FOLLOWS = 1 << 11
ITEM_XNAME_FOLLOWS = 1 << 12

ORIGINAL_DATA = b"ORIGINAL_DEST_BYTES_KEEP_ME\n"
MODIFIED_DATA = b"MODIFIED_INPLACE_DESPITE_BAD_CHECKSUM\n"
INVALID_MD5 = b"\x00" * 16
MODTIME = 1_700_000_000
EXPECTED_PROTOCOL_ERROR = "error in rsync protocol data stream"
TEST_DATA = b"STRICT_NOFOLLOW_REQUIRED\n"


def drain_argv(peer):
    nul_run = 0
    while nul_run < 2:
        byte = peer._recv_exact(1)
        nul_run = nul_run + 1 if byte == b"\0" else 0


def read_mux_bytes(peer, buf, count):
    while len(buf) < count:
        word = struct.unpack("<I", peer._recv_exact(4))[0]
        payload = peer._recv_exact(word & 0xFFFFFF)
        if (word >> 24) - rp.MPLEX_BASE == rp.MSG_DATA:
            buf.extend(payload)
    result = bytes(buf[:count])
    del buf[:count]
    return result


def read_mux_int(peer, buf):
    return struct.unpack("<i", read_mux_bytes(peer, buf, 4))[0]


def read_mux_short(peer, buf):
    return struct.unpack("<H", read_mux_bytes(peer, buf, 2))[0]


def read_mux_byte(peer, buf):
    return read_mux_bytes(peer, buf, 1)[0]


def read_mux_ndx(peer, buf):
    first = read_mux_byte(peer, buf)
    if first == 0:
        return rp.NDX_DONE
    if first == 0xFF:
        raise RuntimeError("generator sent negative index")
    if first == 0xFE:
        value = read_mux_bytes(peer, buf, 2)
        if value[0] & 0x80:
            rest = read_mux_bytes(peer, buf, 2)
            return ((value[0] & 0x7F) << 24) | value[1] | (rest[0] << 8) | (rest[1] << 16)
        return (value[0] << 8) + value[1] - 1
    return first - 1


def drain_generator_request(peer, buf):
    while True:
        count = read_mux_int(peer, buf)
        if count == 0:
            break
        read_mux_bytes(peer, buf, count)

    while True:
        index = read_mux_ndx(peer, buf)
        if index == rp.NDX_DONE:
            raise RuntimeError("generator sent NDX_DONE")
        flags = read_mux_short(peer, buf)
        if flags & ITEM_BASIS_TYPE_FOLLOWS:
            read_mux_byte(peer, buf)
        if flags & ITEM_XNAME_FOLLOWS:
            length = read_mux_byte(peer, buf)
            if length & 0x80:
                length = (length & 0x7F) * 0x100 + read_mux_byte(peer, buf)
            if length:
                read_mux_bytes(peer, buf, length)
        count = read_mux_int(peer, buf)
        for _ in range(3):
            read_mux_int(peer, buf)
        for _ in range(count):
            read_mux_int(peer, buf)
        if flags & rp.ITEM_TRANSFER:
            return index


def run_synthetic_sender(client_args, uri_path, dest_path, inject_fault):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    command = rsync_argv("--protocol=30", "-r", "--no-whole-file") + client_args
    command += [f"rsync://127.0.0.1:{port}/{uri_path}", f"{dest_path}/"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    client, _ = listener.accept()
    client.settimeout(15)
    peer = rp.DaemonReceiver(client)

    try:
        peer.handshake(compat_flags=CF_INPLACE_PARTIAL_DIR, seed=0x12345678)
        drain_argv(peer)
        entry = rp.FileEntry("keep", mode=rp.S_IFREG | 0o644,
                             length=len(MODIFIED_DATA), modtime=MODTIME)
        peer.send_data(entry.encode() + rp.end_of_flist())
        buf = bytearray()
        index = drain_generator_request(peer, buf)

        body = bytearray()
        body += rp.w_shortint(rp.ITEM_TRANSFER | ITEM_BASIS_TYPE_FOLLOWS)
        body += rp.w_byte(FNAMECMP_PARTIAL_DIR)
        body += rp.w_sum_head(0, 0, 0, 0)
        body += rp.w_int(len(MODIFIED_DATA)) + MODIFIED_DATA
        body += rp.w_int(0)
        body += INVALID_MD5 if inject_fault else hashlib.md5(MODIFIED_DATA).digest()

        response = bytearray(peer.w_ndx(index))
        response += body
        response += peer.w_ndx(rp.NDX_DONE) * 4
        peer.send_data(bytes(response))
    finally:
        peer.drain(timeout=3)
        peer.close()
        listener.close()

    stdout, _ = process.communicate(timeout=10)
    return process.returncode, stdout


def run_basis_sender(client_args, dest_path, inject_fault):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    command = rsync_argv("--protocol=30", "-r", "--no-whole-file") + client_args
    command += [f"rsync://127.0.0.1:{port}/mod/", f"{dest_path}/"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    client, _ = listener.accept()
    client.settimeout(15)
    peer = rp.DaemonReceiver(client)

    try:
        peer.handshake(compat_flags=CF_INPLACE_PARTIAL_DIR, seed=0x12345678)
        drain_argv(peer)
        entry = rp.FileEntry("f", mode=rp.S_IFREG | 0o644,
                             length=len(TEST_DATA), modtime=MODTIME)
        peer.send_data(entry.encode() + rp.end_of_flist())
        buf = bytearray()
        index = drain_generator_request(peer, buf)

        body = bytearray()
        body += rp.w_shortint(rp.ITEM_TRANSFER | ITEM_BASIS_TYPE_FOLLOWS)
        body += rp.w_byte(FNAMECMP_BASIS_DIR_LOW)
        body += rp.w_sum_head(1, len(TEST_DATA), 16, len(TEST_DATA))
        body += rp.w_int(-1) + rp.w_int(0)
        body += INVALID_MD5 if inject_fault else hashlib.md5(TEST_DATA).digest()

        response = bytearray(peer.w_ndx(index))
        response += body
        response += peer.w_ndx(rp.NDX_DONE) * 4
        peer.send_data(bytes(response))
    finally:
        peer.drain(timeout=3)
        peer.close()
        listener.close()

    stdout, _ = process.communicate(timeout=10)
    return process.returncode, stdout


print("=== Test 1: Operator-path leaf symlink rejection ===", flush=True)
rmtree(TODIR)
dest = TODIR / "dest"
linkdest = TODIR / "linkdest"
makepath(dest, linkdest)
(linkdest / "f").write_bytes(TEST_DATA)

_, output = run_basis_sender([f"--link-dest={linkdest}", "--partial"], dest, False)
if not (dest / "f").is_file() or (dest / "f").read_bytes() != TEST_DATA:
    test_fail("regular alt-dest basis control failed:\n" + output)

rmtree(TODIR)
dest = TODIR / "dest"
linkdest = TODIR / "linkdest"
target = TODIR / "outside-target"
makepath(dest, linkdest)
target.write_bytes(TEST_DATA)
try:
    os.symlink(str(target), linkdest / "f")
except (OSError, NotImplementedError):
    test_skipped("leaf-symlink basis test requires symlink support")

ret, output = run_basis_sender([f"--link-dest={linkdest}", "--partial"], dest, True)
if ret == 0 or "got a block match with no basis file" not in output:
    test_fail("leaf symlink basis was not rejected:\n" + output)
if (dest / "f").is_file() and (dest / "f").read_bytes() == TEST_DATA:
    test_fail("receiver followed an alt-dest leaf symlink:\n" + output)


print("=== Test 1: Legitimate partial-dir transfer ===", flush=True)
rmtree(TODIR)
dest = TODIR / "dest"
partial_dir = dest / ".rsync-partial"
makepath(dest, partial_dir)
dest_file = dest / "keep"
partial_file = partial_dir / "keep"
partial_file.write_bytes(ORIGINAL_DATA)

ret, output = run_synthetic_sender(["--partial-dir=.rsync-partial"], "mod/keep", dest, False)
if ret != 12 or EXPECTED_PROTOCOL_ERROR not in output \
        or partial_file.exists() or not dest_file.is_file() \
        or dest_file.read_bytes() != MODIFIED_DATA:
    test_fail(f"legitimate partial-dir transfer failed (rc={ret}, "
              f"partial={partial_file.exists()}, file={dest_file.exists()}, "
              f"data={dest_file.read_bytes() if dest_file.exists() else None!r}):\n"
              + output)

print("=== Test 2: Forged partial-dir token ===", flush=True)
rmtree(TODIR)
makepath(dest)
dest_file.write_bytes(ORIGINAL_DATA)

ret, output = run_synthetic_sender([], "mod/keep", dest, True)
if ret != 12 or EXPECTED_PROTOCOL_ERROR not in output:
    test_fail("forged FNAMECMP_PARTIAL_DIR token did not reach the expected "
              f"protocol failure (rc={ret}):\n{output}")
if not dest_file.is_file() or dest_file.read_bytes() != ORIGINAL_DATA:
    test_fail("forged partial-dir token changed the destination:\n" + output)

print("SUCCESS: partial-dir state validation passed", flush=True)
