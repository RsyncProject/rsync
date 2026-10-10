#!/usr/bin/env python3

import ctypes
import errno
import os
import tempfile
import shutil
import atexit
import pathlib
import platform
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

if platform.system() != "Darwin":
    test_skipped("macOS-specific fchmodat setgid behaviour", capability='darwin')

AT_FDCWD = -2
AT_SYMLINK_NOFOLLOW = 0x0020
libc = ctypes.CDLL(None, use_errno=True)
libc.fchmodat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint, ctypes.c_int]

def mode(path):
    return path.stat().st_mode & 0o7777

def _scratch_with_ungrantable_group():
    cand = SCRATCHDIR / 'macos-setgid-ordinary-mode'
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
                 "(running as root or every candidate group is granted)",
                 capability='ungrantable_group')

mprobe = base / "mprobe"
mprobe.mkdir(mode=0o2750)
if mode(mprobe) != 0o750:
    test_skipped("mkdir does not apply ordinary bits while clearing setgid",
                 capability='setgid_semantics')

faprobe = base / "fchmodat-probe"
faprobe.mkdir(mode=0o700)
ctypes.set_errno(0)
frc = libc.fchmodat(AT_FDCWD, os.fsencode(faprobe), 0o2750,
                    AT_SYMLINK_NOFOLLOW)
if frc == 0 or ctypes.get_errno() != errno.EPERM or mode(faprobe) != 0o700:
    test_skipped("fchmodat no-follow does not expose the macOS differential",
                 capability='setgid_semantics')

fdprobe = base / "fchmod-probe"
fdprobe.mkdir(mode=0o700)
fd = os.open(fdprobe, os.O_RDONLY)
try:
    os.fchmod(fd, 0o2750)
finally:
    os.close(fd)
if mode(fdprobe) != 0o750:
    test_skipped("descriptor chmod does not preserve the legacy mkdir semantics",
                 capability='setgid_semantics')

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
