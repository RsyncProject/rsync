#!/usr/bin/env python3
# Finding (codex review of [16-19]): the generator-side alt-dest basis stat is
# confined for a /./ inner-module chroot, but the RECEIVER's delta-basis open
# (secure_basis_open) bypassed confinement for any am_chrooted daemon. The
# receiver trusts the peer-supplied fnamecmp_type (read from the inbound stream),
# so a malicious push client can force an alternate-basis index whose basis_dir
# escapes the inner module through a symlinked parent (--compare-dest=../linkparent
# -> outside). The receiver then opens an outside-inner-module file as the delta
# basis and copies its matched blocks into the in-module destination -- an
# out-of-module read materialized into a file the client can later pull.
#
# This drives the attack with the pure-Python sender (rsync_proto) instead of a
# recompiled malicious binary: push file "f" and, in the transfer phase, forge
# fnamecmp_type = FNAMECMP_FUZZY + 1 (alt-dest index 0) with xname "f" and a
# delta that matches block 0 of that forged basis. The pre-existing dest matches
# the source's first block (so a block-0 match is legitimate); with the forged
# index the receiver applies that match against the OUTSIDE basis, and the wrong
# whole-file checksum then trips "failed verification" -- but only AFTER the
# receiver opened and read the out-of-module file, which is the confinement
# violation. So the tell is in the daemon log: "failed verification" means the
# escaping basis was opened (RED on the unconfined receiver); once
# secure_basis_open confines the inner-module case the open is refused (the
# forged match has no basis file) and no out-of-module read occurs.
#
# Needs root (the /./ chroot) and a real TCP daemon (the Python sender).

import os
import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, get_rootuid, get_testuid, makepath, require_tcp,
    rmtree, start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)
import rsync_proto as rp

PORT = 12973
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
if get_testuid() != get_rootuid():
    test_skipped("the /./ inner-module chroot regression requires root")
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
SECRET = b'S' * BLK + b'Y'        # outside basis: first block is the secret
PUBLIC = b'A' * BLK               # pre-existing dest == generator's FNAME basis
SOURCE = b'A' * BLK + b'X'        # block 0 matches dest, so a block-0 match is sent

(outside / 'f').write_bytes(SECRET)
(outside / 'dest').mkdir()        # cover both file->dirname resolutions
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
# Push file "f"; its content is delivered in the transfer phase, not the flist.
s.send_flat_flist([rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=len(SOURCE))])
# Forge alt-dest basis index 0 (FNAMECMP_FUZZY+1) with basename "f"; match block 0
# of that basis then send the literal 'X' tail.
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
