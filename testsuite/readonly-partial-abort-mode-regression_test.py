#!/usr/bin/env python3
"""An aborted in-place update must not relax an existing file's mode.

The proposed-3.5 receiver retries an EACCES output open by chmod'ing an
existing output or in-place partial file to 0600.  This test's fake remote
sender closes after the receiver has requested the file and before sending any
file-data token.  The transfer must fail, but mode 0444 must survive.  A normal
successful --inplace update of a read-only file is also checked so that the
3.5 compatibility feature remains intact.
"""

import os
import select
import struct
import subprocess
import sys

import rsync_proto as rp
from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail


CF_INPLACE_PARTIAL_DIR = 1 << 6
FNAMECMP_PARTIAL_DIR = 0x81
ITEM_BASIS_TYPE_FOLLOWS = 1 << 11


def write_all(data):
    view = memoryview(data)
    while view:
        n = os.write(1, view)
        if n <= 0:
            raise RuntimeError("short write to rsync client")
        view = view[n:]


def read_exact(n, timeout=10):
    out = bytearray()
    while len(out) < n:
        ready, _, _ = select.select([0], [], [], timeout)
        if not ready:
            raise RuntimeError(f"timeout reading {n} protocol bytes")
        chunk = os.read(0, n - len(out))
        if not chunk:
            raise RuntimeError(f"EOF after {len(out)}/{n} protocol bytes")
        out += chunk
    return bytes(out)


def frame(payload):
    word = ((rp.MPLEX_BASE + rp.MSG_DATA) << 24) | len(payload)
    return struct.pack("<I", word) + payload


def wait_for_generator_request():
    """Consume multiplexed client output until index 0's request appears."""
    data = bytearray()
    for _ in range(256):
        word = struct.unpack("<I", read_exact(4))[0]
        length = word & 0xFFFFFF
        tag = (word >> 24) - rp.MPLEX_BASE
        payload = read_exact(length) if length else b""
        if tag != rp.MSG_DATA:
            continue
        data += payload
        if len(data) >= 7 and b"\x01" in data[4:]:
            return
        if len(data) > 1024 * 1024:
            break
    raise RuntimeError(f"generator did not request index 0: {data[:64].hex()}")


def fake_sender(basis_type):
    write_all(rp.w_int(30))
    client_protocol = struct.unpack("<i", read_exact(4))[0]
    if client_protocol < 30:
        raise RuntimeError(f"client protocol is unexpectedly {client_protocol}")
    write_all(rp.w_varint(CF_INPLACE_PARTIAL_DIR) + rp.w_int(0x12345678))

    entry = rp.FileEntry("f", mode=rp.S_IFREG | 0o444, length=4096,
                         modtime=1700000000, protocol=30)
    write_all(frame(entry.encode() + rp.end_of_flist(protocol=30)))
    wait_for_generator_request()

    response = bytearray()
    response += b"\x01"
    response += rp.w_shortint(rp.ITEM_TRANSFER | ITEM_BASIS_TYPE_FOLLOWS)
    response += rp.w_byte(basis_type)
    response += rp.w_sum_head(0, 0, 0, 0)
    write_all(frame(bytes(response)))


fake_arg = next((arg for arg in sys.argv if arg.startswith("--fake-server=")), None)
if fake_arg:
    try:
        scenario = fake_arg.split("=", 1)[1]
        fake_sender(FNAMECMP_PARTIAL_DIR if scenario == "partial" else rp.FNAMECMP_FNAME)
    except Exception as exc:
        print(f"fake sender failed: {exc!r}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if os.geteuid() == 0:
    print("SKIP: root bypasses the read-only output-file precondition")
    sys.exit(77)

base = SCRATCHDIR / "readonly-partial-abort-mode-regression"
rmtree(base)
script = os.path.abspath(__file__)


def run_abort_case(scenario):
    dest = base / scenario / "dest"
    if scenario == "partial":
        existing = dest / ".rsync-partial" / "f"
        options = ("--partial-dir=.rsync-partial", "--no-whole-file")
    else:
        existing = dest / "f"
        options = ("--inplace", "--no-whole-file")
    makepath(existing.parent)
    existing.write_bytes(b"old data\n")
    os.chmod(existing, 0o444)

    rsh = f"{sys.executable} {script} --fake-server={scenario}"
    proc = subprocess.run(
        rsync_argv("--protocol=30", "-r", *options, "-e", rsh,
                   "fake:f", str(dest) + "/"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20,
    )

    failures = []
    if proc.returncode == 0:
        failures.append(f"truncated {scenario} stream unexpectedly succeeded:\n{proc.stdout}")
    mode = os.stat(existing).st_mode & 0o7777
    if mode != 0o444:
        failures.append(
            f"aborted {scenario} update changed pre-existing mode 0444 to {mode:04o}; "
            f"rsync output:\n{proc.stdout}"
        )
    return failures


def run_success_case():
    source = base / "success" / "source"
    dest = base / "success" / "dest"
    makepath(source)
    makepath(dest)
    (source / "f").write_bytes(b"replacement data\n")
    (dest / "f").write_bytes(b"old data\n")
    os.chmod(source / "f", 0o444)
    os.chmod(dest / "f", 0o444)
    proc = subprocess.run(
        rsync_argv("--inplace", "-r", str(source) + "/", str(dest) + "/"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20,
    )
    failures = []
    if proc.returncode != 0:
        failures.append(f"successful read-only --inplace update failed:\n{proc.stdout}")
    if (dest / "f").read_bytes() != b"replacement data\n":
        failures.append("successful read-only --inplace update did not install the new data")
    mode = os.stat(dest / "f").st_mode & 0o7777
    if mode != 0o444:
        failures.append(f"successful read-only --inplace update left mode {mode:04o}")
    return failures


failures = run_abort_case("inplace") + run_abort_case("partial") + run_success_case()
if failures:
    test_fail("\n\n".join(failures))

print("PASS: aborted updates preserved mode and successful read-only inplace still works")
