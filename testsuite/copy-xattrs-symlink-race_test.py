#!/usr/bin/env python3
# Symlink-race (TOCTOU) in the generator's --copy-dest xattr copy.
#
# With -X and --copy-dest, when a basis file matches the source the generator
# copies it into place with copy_altdest_file() -> copy_file() -> copy_xattrs().
# copy_xattrs() wrote each attribute with sys_lsetxattr(dest, ...): lsetxattr
# does not follow a *leaf* symlink, but the kernel's path walk follows symlinks
# in PARENT components.  copy_file's dest open is confined (do_open_at, secure),
# so it only succeeds while dest/sub is a real directory and pins that inode --
# but the unfixed copy_xattrs re-resolved the dest *path* afterwards, so a parent
# component flipped to a symlink->outside in the window between the open and the
# setxattr redirected the attacker-chosen xattr onto a file OUTSIDE the
# destination tree (the attacker controls the bytes -- e.g. security.capabilities
# as root).
#
# RED before the fix (the marker xattr lands on an outside sentinel); GREEN once
# copy_xattrs sets through the held O_NOFOLLOW fd (fsetxattr), which a parent
# flip cannot redirect.  A separate-process flipper wins the race; no rsync
# instrumentation is needed.

import os
import platform
import subprocess
import time

from rsyncfns import (
    RACE_TIMEOUT, SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped,
    xattr_set, xattrs_supported,
)

MARKER = 'user.marker'   # on-disk name we look for on the outside sentinels
N = 40

if platform.system() != 'Linux':
    test_skipped("parent-flip xattr race is checked on Linux (os.*xattr)")
if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling)")

base = SCRATCHDIR / 'copy-xattrs-race'
src = base / 'src'
cdbasis = base / 'cdbasis'      # absolute --copy-dest basis
dest = base / 'dest'
outside = base / 'outside'
rmtree(base)
(src / 'sub').mkdir(parents=True)
(cdbasis / 'sub').mkdir(parents=True)
dest.mkdir()
outside.mkdir()

# Source + matching copy-dest basis (same content/mtime/mode so the generator
# copies from the basis rather than transferring), both carrying the attacker-
# chosen marker xattr.  A large payload widens the copy_file open->setxattr
# window so the separate-process flipper can land inside it.
payload = ('x' * 4096 + '\n') * 256
try:
    for i in range(N):
        s = src / 'sub' / f'f{i}'
        b = cdbasis / 'sub' / f'f{i}'
        s.write_text(payload)
        b.write_text(payload)
        os.chmod(s, 0o644)
        os.chmod(b, 0o644)
        st = s.stat()
        os.utime(b, (st.st_atime, st.st_mtime))
        xattr_set('marker', 'PWNED', s, b)
        # Pre-create the outside sentinel the escaped lsetxattr would land on
        # (lsetxattr on a missing path is a no-op ENOENT, not an escape).
        (outside / f'f{i}').write_text('')
except OSError as e:
    test_skipped(f"filesystem does not support user xattrs ({e})")

sub = dest / 'sub'           # the parent component the attacker flips
link = dest / '.sublink'     # symlink -> outside, swapped in for `sub`
link.symlink_to(outside)


def push():
    subprocess.run(
        rsync_argv('-rtpX', '--inplace', f'--copy-dest={cdbasis}',
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def outside_marked():
    return sorted(p.name for p in outside.iterdir()
                  if p.is_file() and not p.is_symlink()
                  and MARKER in os.listxattr(str(p)))


# Positive control: a normal push copies the basis xattr into dest/sub, so the
# copy_xattrs path is genuinely exercised and the race below isn't vacuous.
rmtree(sub)
sub.mkdir()
push()
if MARKER not in os.listxattr(str(sub / 'f0')):
    test_fail("positive control: a normal --copy-dest -X push did not copy the "
              "basis marker xattr into dest/sub/f0, so the copy_xattrs race "
              "window would be vacuous")
# Clear any marker the control might have left on the sentinels.
for i in range(N):
    try:
        os.removexattr(str(outside / f'f{i}'), MARKER)
    except OSError:
        pass

# Separate-process flipper: flip dest/sub between a real directory and the
# symlink->outside (atomic 3-rename swap), fast enough to win the window
# between copy_file's confined open and copy_xattrs' setxattr.  It self-
# terminates when the test process goes away (getppid changes) and after a
# hard deadline, so a killed test can't leak an orphan (see start_path_flipper).
flip_code = (
    "import os, sys, time\n"
    "sub, link = sys.argv[1], sys.argv[2]\n"
    "scratch = sub + '.flip'\n"
    "parent = os.getppid()\n"
    "deadline = time.time() + 120\n"
    "while time.time() < deadline and os.getppid() == parent:\n"
    "    try:\n"
    "        os.makedirs(sub, exist_ok=True)\n"
    "        os.rename(sub, scratch); os.rename(link, sub); os.rename(scratch, link)\n"
    "        os.rename(sub, scratch); os.rename(link, sub); os.rename(scratch, link)\n"
    "    except OSError:\n"
    "        pass\n"
)
flip = subprocess.Popen(['python3', '-c', flip_code, str(sub), str(link)])
try:
    deadline = time.monotonic() + max(RACE_TIMEOUT, 10.0)
    while time.monotonic() < deadline:
        # Start each round from a real (absent->mkdir'd) dest/sub so the copy
        # opens a real directory; the flip then happens during the copy.
        rmtree(sub)
        try:
            sub.unlink()
        except OSError:
            pass
        push()
        marked = outside_marked()
        if marked:
            test_fail(
                "copy_xattrs parent-symlink race: the attacker-chosen marker "
                f"xattr was written onto files OUTSIDE the destination tree "
                f"({marked}) -- copy_altdest_file's copy_xattrs() re-resolved "
                "the dest path with lsetxattr and followed a flipped dest/sub "
                "symlink.")
finally:
    flip.terminate()
    try:
        flip.wait(timeout=5)
    except subprocess.TimeoutExpired:
        flip.kill()

# No escape within the race budget.
