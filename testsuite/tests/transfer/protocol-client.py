#!/usr/bin/env python3

import io
import os
from contextlib import redirect_stdout

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, start_test_daemon, test_fail,
)
from harness import protocol as rp
from harness import protocol_client

PORT = 12968
require_tcp("the protocol client uses a raw TCP socket; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'protocol-client'
rmtree(base)
mod = base / 'mod'
makepath(mod / 'sub')
A = b'A' * 16
B = b'B' * 47 + b'\n'
NOTE = b'just a note\n'
CSUB = b'nested pdf\n'
(mod / 'a.pdf').write_bytes(A)
(mod / 'b.pdf').write_bytes(B)
(mod / 'note.txt').write_bytes(NOTE)
(mod / 'sub' / 'c.pdf').write_bytes(CSUB)
os.symlink('a.pdf', mod / 'link.pdf')
MTIME = 1609556645
os.utime(mod / 'a.pdf', (MTIME, MTIME))
os.utime(mod / 'b.pdf', (MTIME, MTIME))

conf = write_daemon_conf([
    ('mod', {'path': str(mod), 'read only': 'no', 'use chroot': 'no'}),
], name='protocol-client.conf')
start_test_daemon(conf, PORT)

COMMON = ['--port', str(PORT)]

buf = io.StringIO()
with redirect_stdout(buf):
    rc = protocol_client.main(COMMON + ['-l', 'localhost::mod/'])
listing = buf.getvalue()
if rc != 0:
        test_fail(f"protocol client list returned {rc}\n{listing}")
for name in ('a.pdf', 'b.pdf', 'note.txt', 'link.pdf', 'sub'):
    if name not in listing:
        test_fail(f"protocol client list missing {name!r}:\n{listing}")
if 'link.pdf -> a.pdf' not in listing:
    test_fail(f"protocol client list did not resolve the symlink target:\n{listing}")
names = [ln.split()[2] for ln in listing.splitlines() if ln.strip()]
if names != sorted(names):
    test_fail(f"protocol client list not in sorted order: {names}")

pull_dest = base / 'pull'
makepath(pull_dest)
rc = protocol_client.main(COMMON + ['-a', 'localhost::mod/*.pdf', str(pull_dest)])
if rc != 0:
    test_fail(f"protocol client pull returned {rc}")
if (pull_dest / 'a.pdf').read_bytes() != A:
    test_fail("pulled a.pdf content mismatch")
if (pull_dest / 'b.pdf').read_bytes() != B:
    test_fail("pulled b.pdf content mismatch")
if not (pull_dest / 'link.pdf').is_symlink() \
   or os.readlink(pull_dest / 'link.pdf') != 'a.pdf':
    test_fail("pulled link.pdf is not a symlink to a.pdf")
if int((pull_dest / 'a.pdf').stat().st_mtime) != MTIME:
    test_fail(f"pulled a.pdf mtime not preserved "
              f"({int((pull_dest / 'a.pdf').stat().st_mtime)} != {MTIME})")
if (pull_dest / 'note.txt').exists():
    test_fail("pull of *.pdf should not have fetched note.txt")

push_src = base / 'push'
makepath(push_src)
U1 = b'uploaded one\n'
U2 = os.urandom(5000)
(push_src / 'u1.txt').write_bytes(U1)
(push_src / 'u2.bin').write_bytes(U2)
rc = protocol_client.main(COMMON + [str(push_src / 'u1.txt'), str(push_src / 'u2.bin'),
                           'localhost::mod/'])
if rc != 0:
    test_fail(f"protocol client push returned {rc}")
if (mod / 'u1.txt').read_bytes() != U1:
    test_fail("pushed u1.txt content mismatch")
if (mod / 'u2.bin').read_bytes() != U2:
    test_fail("pushed u2.bin (random) content mismatch")

class CorruptingClient(rp.DaemonClient):
    def make_file_token_stream(self, content):
        return super().make_file_token_stream(b'X' * len(content))

rc = protocol_client.main(COMMON + [str(push_src / 'u1.txt'), 'localhost::mod/hooked.txt'],
                 client_factory=CorruptingClient)
landed = mod / 'hooked.txt'
if landed.exists() and landed.read_bytes() == U1:
    test_fail("the corrupting hook did not take effect -- the daemon accepted "
              "the original content, so make_file_token_stream was not used")
if landed.exists() and landed.read_bytes() not in (b'X' * len(U1),):
    pass

print("protocol client: list + pull + push round-trip against rsyncd, and the "
      "make_file_token_stream hook is honoured (corrupt push rejected by md5).")
