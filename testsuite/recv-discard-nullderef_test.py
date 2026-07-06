#!/usr/bin/env python3
# Regression test for a receiver NULL-deref on the delta DISCARD path.
#
# In receiver.c receive_data(), a block-MATCH token that arrives while the
# receiver is DISCARDING a file (discard_receive_data() -> receive_data() with
# fname==NULL, fd==-1, hence mapbuf==NULL) reached
#     rprintf(FERROR, "...%s...", full_fname(fname), ...)
# with fname==NULL. full_fname() dereferences its argument unconditionally
# (util1.c: `if (*fn == '/')`), so the receiver SIGSEGVs. The faulty error
# branch was added in 31fbb17d ("receiver: fix absolute --partial-dir delta
# resume"); the fix discriminates on fd (not mapbuf) and, on the discard path
# (fd==-1), absorbs the matched bytes benignly instead of erroring.
#
# This is a NORMAL-operation crash, not adversarial: a stock cooperating sender
# triggers it. The generator sends real block sums (basis readable, delta mode);
# the receiver then has to discard because its output mkstemp() fails -- here
# because the destination directory is not writable. A block MATCH against the
# shared leading block reaches the discard path and crashes the pre-fix binary.
#
# We drive a real sender<->receiver pair (client sender -> daemon receiver) so
# the receiver actually takes the recv_files discard path; a local `rsync a b`
# does not. In the default (pipe) daemon transport both ends are the binary
# under test.
#
# Skipped (exit 77) when running as root (root bypasses DAC), or when the
# directory mode is not enforced (e.g. a non-root process holding
# CAP_DAC_OVERRIDE in an unprivileged container): in both cases the receiver's
# mkstemp() would succeed despite chmod 0555, the discard path would not be
# taken, and the test would silently pass against a buggy binary. The
# post-chmod writability probe converts that silent false-pass into an honest
# skip and subsumes the root check.

import os
import shlex
import subprocess
import tempfile

from rsyncfns import (
    SCRATCHDIR, RSYNC, TMPDIR,
    get_testuid, get_rootuid, makepath, start_test_daemon, write_daemon_conf,
    test_fail, test_skipped,
)

DAEMON_PORT = 12895

if get_testuid() == get_rootuid():
    test_skipped("root bypasses DAC: the unwritable dest dir wouldn't make "
                 "the receiver's mkstemp fail, so the discard path (and the "
                 "bug) is never reached")

os.chdir(TMPDIR)

MODDIR = SCRATCHDIR / 'recvdiscard-mod'   # daemon module root (writable)
BASISDIR = MODDIR / 'd'                    # made read-only -> mkstemp fails
SRCDIR_ = SCRATCHDIR / 'recvdiscard-src'   # client source tree
makepath(MODDIR, BASISDIR, SRCDIR_)

# Basis and source share a leading block (2000 'A's) so the generator emits
# real sums and the receiver gets a block MATCH; the tails differ and the
# source is larger so a delta (not a no-op) is sent.
basis = BASISDIR / 'f'
basis.write_bytes(b'A' * 2000 + b'C' * 1000)
src = SRCDIR_ / 'f'
src.write_bytes(b'A' * 2000 + b'B' * 3000)

# A read/write daemon module rooted at MODDIR.
conf = write_daemon_conf([('recvdiscard', {'path': str(MODDIR),
                                           'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)

# Make the destination directory unwritable so the receiver's output mkstemp()
# fails and it falls back to discarding the delta stream. Restore in finally so
# the per-test scratch tree can be cleaned up.
os.chmod(BASISDIR, 0o555)

# Probe that the chmod actually denies writes for *this* process.  A non-root
# user holding CAP_DAC_OVERRIDE bypasses the directory write bit, so mkstemp
# would succeed in the daemon receiver too, the discard path would never be
# taken, and the test would silently pass on a buggy binary.  Better to skip
# explicitly.  (Root takes this path too: its probe succeeds → skip, which
# subsumes the uid==0 check.)
try:
    _fd, _probe = tempfile.mkstemp(dir=BASISDIR)
    os.close(_fd)
    os.unlink(_probe)
    os.chmod(BASISDIR, 0o755)
    test_skipped("destination dir is writable despite chmod 0555 "
                 "(CAP_DAC_OVERRIDE?); cannot force the receiver discard path")
except OSError:
    pass  # EACCES -- good, the precondition is enforced

try:
    argv = shlex.split(RSYNC) + [
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

# A receiver SIGSEGV manifests to the client as a protocol error (the daemon's
# receiver child crashes mid-stream and the connection drops): exit code 12.
# With the fix the receiver drains the delta and, because the forced-unwritable
# destination leaves the file untransferred, the run reports the benign "some
# files were not transferred" -- exit code 23.
#
# 23 is the ONLY non-crash outcome here: the writability probe above guarantees
# the receiver's mkstemp() fails, so the file is always discarded. An exit 0
# would mean the file actually transferred -- the discard path was NOT exercised
# and the run proves nothing -- so require exactly 23 (and call out 12 as the
# pre-fix crash).
if rc == 12:
    test_fail(f"receiver crashed on the discard path (rsync exited {rc}: "
              "error in rsync protocol data stream -- the receiver child "
              "SIGSEGV'd in full_fname(NULL))")
if rc != 23:
    test_fail(f"expected rsync exit 23 (the forced discard leaves the file "
              f"untransferred); got {rc} -- the discard path was not exercised, "
              "so this run validates nothing (12 would be the pre-fix crash)")

print(f"OK: receiver discarded the delta without crashing (rsync exit {rc})")
