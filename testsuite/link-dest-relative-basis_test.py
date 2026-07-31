#!/usr/bin/env python3
# Functional regression: a RELATIVE alt-basis dir (--link-dest / --copy-dest /
# --compare-dest = ../sibling) is silently ignored by a daemon receiver, so the
# basis is never used -- every file is re-transferred instead of hard-linked /
# copied / skipped.  No error is printed; backups silently stop de-duplicating.
#
# Reported as #915 ("Security fix breaks --link-dest via rsync daemon": a
# `use chroot = no` daemon with `--link-dest=../01` re-transfers everything and
# fills the backup disk).  The closely-related #928 is the same family over a
# remote shell with a relative `--link-dest=../snap.1`.
#
# Root cause: the 3.4.x symlink-race hardening resolves the receiver's basis
# through the confined resolver, which rejects the `..` that climbs from the
# destination (00) to its sibling basis (01); no basis is found, so the file is
# treated as new.  Works in 3.4.1 (basis honoured).
#
# We exercise all three alt-basis forms because they are NOT obviously identical
# even though they share check_alt_basis_dirs():
#   * --link-dest=../01   : the matched file must be HARD-LINKED to the basis.
#   * --copy-dest=../01   : the matched file is COPIED from the basis, so its
#                           data is NOT sent over the wire (literal data ~ 0).
#   * --compare-dest=../01 : a matched file is skipped entirely -- NOT created
#                           in the destination at all.
# Each signal cleanly separates "basis honoured" (fixed/3.4.1) from "basis
# ignored" (the regression).
#
# XFAIL until a relative alt-basis dir is honoured by a sanitize_paths receiver
# again (the accompanying syscall.c/receiver.c fix; cf. upstream PR #930).  On
# platforms without openat2/O_RESOLVE_BENEATH the portable resolver still
# rejects the '..' for safety, so this stays XFAIL there.  Runs at any uid.

import re
import subprocess

from rsyncfns import (
    SCRATCHDIR, make_data_file, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_xfail, write_daemon_conf,
)

DAEMON_PORT = 12915
DATA_SIZE = 40000

mod = SCRATCHDIR / 'bakmod'        # daemon module root: holds basis 01 and dest 00
src = SCRATCHDIR / 'src915'
rmtree(mod)
rmtree(src)
makepath(mod / '01', src)
make_data_file(src / 'f.dat', DATA_SIZE)
# Basis 01 holds a byte-identical copy of the file (same name/size/mtime so the
# quick-check treats it as a match and the basis is eligible).
import shutil
shutil.copy2(src / 'f.dat', mod / '01' / 'f.dat')

conf = write_daemon_conf([
    ('bak', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def push(opt):
    """Fresh dest 00, push src/ into bak/00/ with the given alt-basis option.
    Returns (rc, stdout)."""
    rmtree(mod / '00')
    proc = subprocess.run(
        rsync_argv('-a', '--stats', opt, f'{src}/', f'{url}bak/00/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, (proc.stdout or '')


def same_inode(a, b):
    sa, sb = a.stat(), b.stat()
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def literal_bytes(out):
    m = re.search(r'Literal data:\s*([\d,]+)', out)
    return int(m.group(1).replace(',', '')) if m else -1


regressions = []
basis = mod / '01' / 'f.dat'

# --- 1. --link-dest=../01 : matched file must be hard-linked to the basis ----
rc, out = push('--link-dest=../01')
if rc not in (0, 23):    # 23: no-RESOLVE_BENEATH platforms reject the basis
    test_fail(f"--link-dest push failed unexpectedly (rc={rc}):\n{out}")
dest = mod / '00' / 'f.dat'
if not dest.is_file():
    test_fail(f"--link-dest: destination file missing ({dest})")
if not same_inode(dest, basis):
    regressions.append("--link-dest=../01 did not hard-link to the basis "
                       "(file re-transferred)")

# --- 2. --copy-dest=../01 : matched file copied locally, NOT sent on the wire -
rc, out = push('--copy-dest=../01')
if rc not in (0, 23):    # 23: no-RESOLVE_BENEATH platforms reject the basis
    test_fail(f"--copy-dest push failed unexpectedly (rc={rc}):\n{out}")
dest = mod / '00' / 'f.dat'
if not dest.is_file():
    test_fail(f"--copy-dest: destination file missing ({dest})")
lit = literal_bytes(out)
if lit > DATA_SIZE // 2:
    regressions.append(f"--copy-dest=../01 re-sent the data over the wire "
                       f"(Literal data={lit}, basis not used)")

# --- 3. --compare-dest=../01 : matched file skipped, NOT created in dest ------
rc, out = push('--compare-dest=../01')
if rc not in (0, 23):    # 23: no-RESOLVE_BENEATH platforms reject the basis
    test_fail(f"--compare-dest push failed unexpectedly (rc={rc}):\n{out}")
if (mod / '00' / 'f.dat').is_file():
    regressions.append("--compare-dest=../01 created the file in the dest "
                       "(basis not matched, so the file was transferred)")

if regressions:
    test_xfail(
        "#915: a daemon receiver ignored a RELATIVE alt-basis dir (../01); the "
        "confined path resolver rejects the `..` climb to the sibling basis so "
        "the basis is never used:\n  - " + "\n  - ".join(regressions) +
        "\nTo be closed by honouring a relative alt-basis dir on a "
        "sanitize_paths receiver again (cf. PR #930).")
# No regressions -> all three relative alt-basis forms honoured the basis.
