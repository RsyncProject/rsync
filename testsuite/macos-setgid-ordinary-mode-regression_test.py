#!/usr/bin/env python3
"""A failed setgid request must not discard the ordinary directory mode.

On macOS, mkdir(..., 02750) for a caller-owned directory whose inherited group
is not one of the caller's groups succeeds with setgid cleared and mode 0750.
The hardened 3.5 receiver stages a new directory and later uses
fchmodat(..., AT_SYMLINK_NOFOLLOW), which returns EPERM without applying any of
the ordinary bits.  A pinned-descriptor fchmod preserves the former semantics.
"""

import ctypes
import errno
import os
import tempfile
import shutil
import atexit
import pathlib
import platform
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped


if platform.system() != "Darwin":
    test_skipped("macOS-specific fchmodat setgid behavior")

AT_FDCWD = -2
AT_SYMLINK_NOFOLLOW = 0x0020
libc = ctypes.CDLL(None, use_errno=True)
libc.fchmodat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint, ctypes.c_int]


def mode(path):
    return path.stat().st_mode & 0o7777


# macOS gives a new directory its parent's group, and the bug only shows when
# that group is one the caller cannot grant.  The build tree usually satisfies
# that, but not always -- a checkout under a home directory inherits a group the
# user IS in, and the test then skipped silently.  Fall back to /private/tmp,
# which is group wheel, so this runs rather than skipping wherever it can.
def _scratch_with_ungrantable_group():
    """A scratch dir whose group we cannot grant, else None.

    The build tree is tried first; a checkout under a home directory
    inherits a group the user IS in, so /private/tmp (group wheel) is the
    fallback.  That one is outside SCRATCHDIR, which the harness cleans, so
    it gets a unique name and an atexit hook -- a fixed name in a sticky
    world-writable directory would let concurrent runs delete each other's
    live fixture, and a leftover owned by another user would make every
    later run skip.
    """
    cand = SCRATCHDIR / "macos-setgid-ordinary-mode-regression"
    rmtree(cand)
    cand.mkdir(parents=True)
    if cand.stat().st_gid not in os.getgroups():
        return cand
    rmtree(cand)

    try:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="rsync-setgid-", dir="/private/tmp"))
    except OSError:
        return None
    atexit.register(shutil.rmtree, tmp, True)
    if tmp.stat().st_gid not in os.getgroups():
        return tmp
    return None


base = _scratch_with_ungrantable_group()
if base is None:
    test_skipped("no scratch parent whose group this user cannot grant "
                 "(running as root, or every candidate group is granted)")

mprobe = base / "mprobe"
mprobe.mkdir(mode=0o2750)
if mode(mprobe) != 0o750:
    test_skipped("mkdir does not apply ordinary bits while clearing setgid")

faprobe = base / "fchmodat-probe"
faprobe.mkdir(mode=0o700)
ctypes.set_errno(0)
frc = libc.fchmodat(AT_FDCWD, os.fsencode(faprobe), 0o2750,
                    AT_SYMLINK_NOFOLLOW)
if frc == 0 or ctypes.get_errno() != errno.EPERM or mode(faprobe) != 0o700:
    test_skipped("fchmodat no-follow does not expose the macOS differential")

fdprobe = base / "fchmod-probe"
fdprobe.mkdir(mode=0o700)
fd = os.open(fdprobe, os.O_RDONLY)
try:
    os.fchmod(fd, 0o2750)
finally:
    os.close(fd)
if mode(fdprobe) != 0o750:
    test_skipped("descriptor chmod does not preserve the legacy mkdir semantics")

src = base / "src"
dest = base / "dest"
src.mkdir(mode=0o755)
(src / "child").mkdir(mode=0o755)
(src / "child" / "payload").write_text("ordinary-mode-preserved\n")

proc = subprocess.run(
    rsync_argv("-a", "--chmod=D2750,F0640", str(src) + "/", str(dest) + "/"),
    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
observed = (mode(dest), mode(dest / "child"))
if proc.returncode != 0 or observed != (0o750, 0o750):
    test_fail(
        "setgid failure discarded requested ordinary directory permissions: "
        f"rc={proc.returncode}, root/child modes={tuple(oct(v) for v in observed)}, "
        f"output={proc.stdout!r}"
    )

print("PASS: ungrantable setgid did not discard the requested 0750 mode")
