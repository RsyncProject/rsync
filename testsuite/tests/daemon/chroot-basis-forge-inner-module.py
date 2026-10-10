#!/usr/bin/env python3

import os
import time

from harness.rsync import (
    SCRATCHDIR, claim_ports, get_rootuid, get_testuid, makepath, require_tcp,
    rmtree, start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)
from harness import protocol as rp

PORT = 12973
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
if get_testuid() != get_rootuid():
    test_skipped("the /./ inner-module chroot regression requires root", capability='root')
claim_ports(PORT)

base = SCRATCHDIR / 'chroot-basis-forge'
outer = base / 'outer'
inner = outer / 'inner'
outside = outer / 'outside'
src = base / 'src'
rmtree(base)
makepath(inner, outside, src)
os.symlink('../outside', inner / 'linkparent')

BLK = 700
SECRET = b'S' * BLK + b'Y'
PUBLIC = b'A' * BLK
SOURCE = b'A' * BLK + b'X'

(outside / 'f').write_bytes(SECRET)
(outside / 'dest').mkdir()
(outside / 'dest' / 'f').write_bytes(SECRET)
(src / 'f').write_bytes(SOURCE)
(inner / 'dest').mkdir()
(inner / 'dest' / 'f').write_bytes(PUBLIC)

conf = write_daemon_conf([
    ('mod', {'path': str(outer) + '/./inner', 'read only': 'no',
             'use chroot': 'yes', 'munge symlinks': 'no'}),
], name='chroot-basis-forge.conf')
log = SCRATCHDIR / 'rsyncd.log'
before = log.read_text(errors='replace') if log.exists() else ''
start_test_daemon(conf, PORT)

s = rp.DaemonSender('127.0.0.1', PORT)
s.handshake('mod', ['--server', '-le.LsfxCIu', '--no-whole-file',
                    '--compare-dest=../linkparent', '.', 'dest/'],
            greeting_version=30)
s.send_flat_flist([rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=len(SOURCE))])
s.run_forged_transfer(rp.FNAMECMP_FUZZY + 1, b'f', literal_tail=b'X')
s.drain(timeout=3.0)
s.close()

leaked = False
for _ in range(50):
    new = log.read_text(errors='replace')[len(before):]
    if 'failed verification' in new:
        leaked = True
        break
    time.sleep(0.1)

if leaked:
    new = log.read_text(errors='replace')[len(before):]
    test_fail("receiver opened and read an outside-inner-module file as the delta "
              "basis via a forged fnamecmp_type (the reconstruction failed the "
              "whole-file checksum -- but the out-of-module read already "
              "happened); secure_basis_open did not confine the /./ chroot.\n"
              + new)

print("chroot-basis-forge-inner-module: a forged alt-dest basis index cannot "
      "open a file outside the inner module")
