#!/usr/bin/env python3
"""A sender leaf rrsync never opens must still resolve beneath a pinned parent.

rrsync deliberately does not content-open a sender argument whose leaf is not a
regular file or a directory: O_RDONLY on a FIFO blocks until a writer appears,
and a dangling symlink resolves to nothing.  What it must NOT do is then hand
rsync the bare name, because every component goes back into play and a parent
flipped after the realpath() check redirects the read out of the tree -- which
is CVE-2026-53783.  The leaf is instead spelled beneath a pinned directory.

rrsync-sender-leaf-flip races this with the real binary, but a race only ever
samples the window: zero leaks in 104 attempts still leaves a few per cent of
per-attempt risk unmeasured.  This test closes the window deterministically
instead.  A stub standing in for rsync inherits the pinned descriptor, then
blocks; the parent is swapped for a symlink pointing outside the tree while it
is blocked, so the substitution is guaranteed to be in place before the stub
resolves anything; the stub then reports what the argument actually names.

With the parent pinned, the stub sees the original in-tree leaf no matter what
the tree now looks like.  Without it -- the bare name -- it sees whatever the
attacker's symlink points at.  So this fails outright if the pin is removed,
rather than depending on winning a race.

Not asserted here: a --relative argument with no client "/./" anchors at the
restricted root rather than the leaf's parent, so its intermediate components
stay raceable.  That limit predates this and is stated in NEWS.
"""

import os
import shlex
import stat
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, makepath, patched_rrsync, proc_self_fd_pins, rmtree, test_fail,
    test_skipped,
)

if not proc_self_fd_pins():
    test_skipped("rrsync's inode pin needs /proc/self/fd (Linux); the "
                 "unhardened fallback elsewhere is by design")

MARKER = 'AUDIT_PARENT_PIN_MARKER_DO_NOT_DISCLOSE'

base = SCRATCHDIR / 'rrsync-parent-pin'
rmtree(base)
restricted = base / 'restricted'
outside = base / 'outside'
makepath(restricted, outside)

# What the attacker wants reached: same names as in the tree, but regular
# files holding the marker, so a leak is unmistakable in the stub's report.
outdir = outside / 'dir'
makepath(outdir)
(outdir / 'target').write_text(MARKER + '\n')
(outdir / 'afifo').write_text(MARKER + '\n')

# In-tree: a dangling symlink and a FIFO, the two shapes rrsync never opens.
realdir = restricted / 'dir'
makepath(realdir)
os.symlink('missing-sibling', realdir / 'target')
os.mkfifo(realdir / 'afifo')

ready = base / 'stub-ready'
go = base / 'stub-go'
report = base / 'stub-report'

# The stub replaces rsync.  It signals that it is running -- with the pinned
# descriptor already inherited -- waits to be released, and only then resolves
# its source argument, which is the whole point: the swap below is guaranteed
# to have happened first.
stub = base / 'rsync-stub'
stub.write_text(f'''#!/usr/bin/env python3
import os, stat, sys, time
arg = sys.argv[-1]
open({str(ready)!r}, 'w').close()
for _ in range(600):
    if os.path.exists({str(go)!r}):
        break
    time.sleep(0.05)
try:
    st = os.lstat(arg)
except OSError as e:
    out = 'error %s' % e.strerror
else:
    if stat.S_ISLNK(st.st_mode):
        out = 'symlink %s' % os.readlink(arg)
    elif stat.S_ISFIFO(st.st_mode):
        out = 'fifo'
    elif stat.S_ISREG(st.st_mode):
        out = 'regular %s' % open(arg).read().strip()
    elif stat.S_ISDIR(st.st_mode):
        out = 'directory'
    else:
        out = 'other'
with open({str(report)!r}, 'w') as f:
    f.write('%s\\n%s\\n' % (arg, out))
''')
stub.chmod(0o755)

rrsync = patched_rrsync(base, rsync_path=str(stub))

evil = restricted / 'evil'
os.symlink(str(outdir), evil)


def resolve_with_parent_swapped(source):
    """Run rrsync, swap `dir` for the outside symlink mid-flight, report."""
    for p in (ready, go, report):
        if p.exists():
            p.unlink()

    cmd = ('rsync --server --sender -logDtpre.iLsfxC . %s' % source)
    proc = subprocess.Popen(
        [str(rrsync), '-ro', str(restricted)],
        env={**os.environ, 'SSH_ORIGINAL_COMMAND': cmd},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not ready.exists():
        if proc.poll() is not None:
            break
        time.sleep(0.02)
    if not ready.exists():
        out = proc.communicate()[0]
        test_fail(f'the stub never ran for {source!r}, so nothing was tested '
                  f'(rc={proc.returncode}, output={out.strip()[:300]!r})')

    # The stub is blocked with the pinned fd already inherited.  Swap the
    # parent for a symlink out of the tree -- guaranteed to be in place before
    # the stub resolves the argument.
    os.rename(realdir, base / 'stash')
    os.rename(evil, realdir)

    go.touch()
    out = proc.communicate(timeout=60)[0]

    # Put the tree back for the next case.
    os.rename(realdir, evil)
    os.rename(base / 'stash', realdir)

    if not report.exists():
        test_fail(f'the stub produced no report for {source!r} '
                  f'(rc={proc.returncode}, output={out.strip()[:300]!r})')
    handed, saw = report.read_text().splitlines()
    return handed, saw


# --- control: the swap really does redirect an unpinned name -----------------
# Resolve the same relative name the way rsync would without a pin, while the
# substitution is in place.  If this does NOT reach the outside file, the
# construction is broken and the assertions below would prove nothing.
os.rename(realdir, base / 'stash')
os.rename(evil, realdir)
try:
    probe = os.path.join(str(restricted), 'dir/target')
    st = os.lstat(probe)
    if not stat.S_ISREG(st.st_mode) or MARKER not in open(probe).read():
        test_fail('control failed: with "dir" swapped, the bare name '
                  'dir/target does not reach the outside file, so this test '
                  'cannot tell a pinned resolution from an unpinned one')
finally:
    os.rename(realdir, evil)
    os.rename(base / 'stash', realdir)

# --- the dangling symlink leaf ----------------------------------------------
handed, saw = resolve_with_parent_swapped('dir/target')
if saw != 'symlink missing-sibling':
    test_fail(f'a dangling-symlink leaf resolved to {saw!r} after its parent '
              f'was swapped for a symlink out of the tree; expected the '
              f'in-tree "symlink missing-sibling".  rsync was handed '
              f'{handed!r} -- an unpinned name puts every component back in '
              'play, which is CVE-2026-53783')

# --- the FIFO leaf -----------------------------------------------------------
handed, saw = resolve_with_parent_swapped('dir/afifo')
if saw != 'fifo':
    test_fail(f'a FIFO leaf resolved to {saw!r} after its parent was swapped '
              f'for a symlink out of the tree; expected the in-tree "fifo".  '
              f'rsync was handed {handed!r}')

print('a sender leaf rrsync never opens still resolves beneath its pinned '
      'parent when the parent is swapped out from under it')
