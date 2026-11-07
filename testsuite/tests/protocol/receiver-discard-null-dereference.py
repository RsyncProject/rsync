#!/usr/bin/env python3

import os
import subprocess
import tempfile

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    RSYNC, SCRATCHDIR, TMPDIR, get_rootuid, get_testuid, makepath, split_rsync_cmd,
    start_test_daemon, test_fail, test_skipped,
)

DAEMON_PORT = 12895

if get_testuid() == get_rootuid():
    test_skipped("root bypasses DAC: the unwritable dest dir wouldn't make "
                 "the receiver's mkstemp fail, so the discard path (and the "
                 "bug) is never reached", capability='nonroot')

os.chdir(TMPDIR)

MODDIR = SCRATCHDIR / 'recvdiscard-mod'
BASISDIR = MODDIR / 'd'
SRCDIR_ = SCRATCHDIR / 'recvdiscard-src'
makepath(MODDIR, BASISDIR, SRCDIR_)

basis = BASISDIR / 'f'
basis.write_bytes(b'A' * 2000 + b'C' * 1000)
src = SRCDIR_ / 'f'
src.write_bytes(b'A' * 2000 + b'B' * 3000)

conf = write_daemon_conf([('recvdiscard', {'path': str(MODDIR),
                                           'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)

os.chmod(BASISDIR, 0o555)

try:
    _fd, _probe = tempfile.mkstemp(dir=BASISDIR)
    os.close(_fd)
    os.unlink(_probe)
    os.chmod(BASISDIR, 0o755)
    test_skipped("destination dir is writable despite chmod 0555 "
                 "(CAP_DAC_OVERRIDE?); cannot force the receiver discard path",
                 capability='dac_restriction')
except OSError:
    pass

try:
    argv = split_rsync_cmd(RSYNC) + [
        '--no-whole-file', '-a',
        str(src), f'{url}recvdiscard/d/f',
    ]
    print('Running:', ' '.join(argv))
    proc = subprocess.run(argv, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    print(proc.stdout, end='')
finally:
    os.chmod(BASISDIR, 0o755)

rc = proc.returncode

if rc == 12:
    test_fail(f"receiver crashed on the discard path (rsync exited {rc}: "
              "error in rsync protocol data stream -- the receiver child "
              "SIGSEGV'd in full_fname(NULL))")
if rc != 23:
    test_fail(f"expected rsync exit 23 (the forced discard leaves the file "
              f"untransferred); got {rc} -- the discard path was not exercised, "
              "so this run validates nothing (12 would be the pre-fix crash)")

print(f"OK: receiver discarded the delta without crashing (rsync exit {rc})")
