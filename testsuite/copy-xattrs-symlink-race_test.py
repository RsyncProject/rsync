#!/usr/bin/env python3
# Symlink-race (TOCTOU) in a confined receiver's metadata xattr write.
#
# With -X and --copy-dest, when a basis file matches the source the generator
# copies it into place and then runs set_file_attrs(), which sets the xattrs/ACLs
# through a held O_NOFOLLOW fd (fsetxattr).  When that held fd is unavailable --
# the held-dirfd cache misses, e.g. for a path deeper than the cache -- the
# unfixed code fell through to a path-based sys_lsetxattr(dest, ...).  lsetxattr
# does not follow a *leaf* symlink, but the kernel's path walk follows symlinks
# in PARENT components, so a parent flipped to a symlink->outside in the window
# between the confined create and the setxattr redirected the attacker-chosen
# xattr onto a file OUTSIDE the destination tree (the attacker controls the
# bytes -- e.g. security.capabilities as root).
#
# RED before the fix (the marker xattr lands on an outside sentinel); GREEN once
# set_file_attrs() re-pins via the secure resolver (or refuses) so the write
# always uses fsetxattr on a confined fd, which a parent flip cannot redirect.
# The dest path is buried below the held-dirfd cache depth so the fd-less path is
# taken on EVERY push (see DEEP), making the flip the only race; a separate-
# process flipper wins it.  No rsync instrumentation is needed.

import os
import platform
import subprocess
import time

from rsyncfns import (
    race_budget, SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped,
    xattr_set, xattrs_supported,
)

MARKER = 'user.marker'   # on-disk name we look for on the outside sentinels
N = 120

if platform.system() != 'Linux':
    test_skipped("parent-flip xattr race is checked on Linux (os.*xattr)")
if not xattrs_supported():
    test_skipped("rsync built without xattr support (or no xattr tooling)")

base = SCRATCHDIR / 'copy-xattrs-race'
src = base / 'src'
cdbasis = base / 'cdbasis'      # absolute --copy-dest basis
dest = base / 'dest'
outside = base / 'outside'
# Bury the flipped dir below the receiver's held-dirfd cache depth (>64) so
# held_dfd_for()/vfs_cached_dirfd() returns -1 for EVERY file deterministically
# (not just on a cache-miss race): set_file_attrs() then takes the fd-less
# metadata path on every push, so the only remaining race is the flip landing
# during the lsetxattr -- a far stronger RED oracle, especially under -j load.
DEEP = '/'.join(['d'] * 70)

rmtree(base)
(src / DEEP / 'sub').mkdir(parents=True)
(cdbasis / DEEP / 'sub').mkdir(parents=True)
(dest / DEEP).mkdir(parents=True)
outside.mkdir()

# Source + matching copy-dest basis (same content/mtime/mode so the generator
# copies from the basis rather than transferring), both carrying the attacker-
# chosen marker xattr.  A large payload widens the copy_file open->setxattr
# window so the separate-process flipper can land inside it.
payload = ('x' * 4096 + '\n') * 16  # ~64 KB: widens the per-file create->lsetxattr window
try:
    for i in range(N):
        s = src / DEEP / 'sub' / f'f{i}'
        b = cdbasis / DEEP / 'sub' / f'f{i}'
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

sub = dest / DEEP / 'sub'           # the parent component the attacker flips
link = dest / DEEP / '.sublink'     # symlink -> outside, swapped in for `sub`
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
# between the confined create and set_file_attrs' fd-less metadata setxattr.  It self-
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
    deadline = time.monotonic() + race_budget(10.0)
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
                "metadata xattr parent-symlink race: the attacker-chosen marker "
                f"xattr was written onto files OUTSIDE the destination tree "
                f"({marked}) -- a confined receiver's set_file_attrs() fell back to "
                "a path-based lsetxattr and followed a flipped dest/sub symlink.")
finally:
    flip.terminate()
    try:
        flip.wait(timeout=5)
    except subprocess.TimeoutExpired:
        flip.kill()

# No escape within the race budget.
