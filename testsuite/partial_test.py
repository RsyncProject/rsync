#!/usr/bin/env python3
"""Coverage of --partial / --partial-dir at depth and across directory
boundaries.

--partial keeps a partially transferred file so a later run can resume it.
--partial-dir=DIR keeps the partial in DIR instead of the destination file:
a RELATIVE dir is created in (and removed from) each destination file's own
directory; an ABSOLUTE dir is a reserved location that holds partials by
basename. All of this is parent- and cross-directory path resolution -- what
the resolver restructure rewrites -- so exercise it on a file several levels
deep, with the absolute partial-dir kept OUTSIDE the destination tree.
"""

import os
import signal
import subprocess
import time

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_same, make_data_file, makepath, rmtree, rsync_argv, run_rsync,
    test_fail,
)

src = FROMDIR
deepdir = os.path.join('d1', 'd2', 'd3')
deep = os.path.join(deepdir, 'f3')
FULL = 12_000_000


def seed_big():
    rmtree(src)
    rmtree(TODIR)
    makepath(src / deepdir)
    make_data_file(src / deep, FULL)


def is_prefix(partial) -> bool:
    pb = partial.read_bytes()
    return 0 < len(pb) < FULL and (src / deep).read_bytes()[:len(pb)] == pb


def interrupt_transfer(extra_args, partial_path):
    """Start a deliberately slow transfer, SIGTERM it once the receiver's
    in-progress temp (.f3.XXXXXX) has some data, and wait for `partial_path`
    (where this mode keeps the partial) to materialise.

    The bandwidth limit is low so the multi-second transfer cannot finish
    before we interrupt it -- important under a loaded parallel run (-j16),
    where the polling loop can lag by seconds. We then poll for the partial,
    since rsync moves it into place from its exit_cleanup handler."""
    proc = subprocess.Popen(
        rsync_argv('-a', '--no-whole-file', '--bwlimit=400', *extra_args,
                   f'{src}/', f'{TODIR}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tdir = TODIR / deepdir
    caught = False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break                       # exited before we caught it
        if tdir.is_dir():
            temps = [p for p in tdir.glob('.f3.*')
                     if p.is_file() and p.stat().st_size > 0]
            if temps:
                caught = True
                break
        time.sleep(0.02)
    proc.send_signal(signal.SIGTERM)
    proc.wait()
    if not caught:
        test_fail("never caught an in-progress temp (transfer finished too "
                  "fast to interrupt)")
    # rsync moves the partial into place from exit_cleanup; give it a moment.
    pdeadline = time.monotonic() + 5
    while time.monotonic() < pdeadline:
        if partial_path.is_file() and partial_path.stat().st_size > 0:
            return
        time.sleep(0.05)


# --- 1. --partial (no dir): partial kept in the dest file itself, at depth --
seed_big()
interrupt_transfer(['--partial'], TODIR / deep)
if not (TODIR / deep).is_file() or not is_prefix(TODIR / deep):
    test_fail("--partial did not leave a valid partial in the dest file")
run_rsync('-a', '--partial', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--partial resume')

# --- 2. relative --partial-dir at depth: deterministic clean pre-seed -------
rmtree(src)
rmtree(TODIR)
makepath(src / deepdir, TODIR / deepdir / '.rsync-partial')
make_data_file(src / deep, 1_000_000)
full = (src / deep).read_bytes()
(TODIR / deepdir / '.rsync-partial' / 'f3').write_bytes(full[:400_000])
run_rsync('-a', '--partial-dir=.rsync-partial', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='rel partial-dir preseed')
if (TODIR / deepdir / '.rsync-partial').exists():
    test_fail("relative --partial-dir not removed after the partial was used")

# --- 3. relative --partial-dir at depth: interrupt then resume -------------
seed_big()
part = TODIR / deepdir / '.rsync-partial' / 'f3'
interrupt_transfer(['--partial-dir=.rsync-partial'], part)
if not part.is_file() or not is_prefix(part):
    test_fail("relative --partial-dir did not keep a valid partial at depth")
run_rsync('-a', '--partial-dir=.rsync-partial', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='rel partial-dir resume')

# --- 4. absolute --partial-dir OUTSIDE the tree (cross-dir): interrupt write -
ext = SCRATCHDIR / 'partials'      # sibling of from/ and to/ -- outside both
rmtree(ext)
ext.mkdir()
seed_big()
interrupt_transfer([f'--partial-dir={ext}'], ext / 'f3')
if not (ext / 'f3').is_file() or not is_prefix(ext / 'f3'):
    test_fail("absolute --partial-dir did not write the partial to the "
              "outside-tree dir")

# --- 5. absolute --partial-dir delta resume completes (regression guard) ----
# A delta (--no-whole-file) resume from an absolute, outside-tree partial-dir
# used to fail whole-file verification forever: the receiver couldn't open the
# absolute basis, so matched blocks were dropped from the verify checksum.
rmtree(src)
rmtree(TODIR)
rmtree(ext)
makepath(src / deepdir, ext)
make_data_file(src / deep, 1_000_000)
(ext / 'f3').write_bytes((src / deep).read_bytes()[:400_000])   # clean prefix
run_rsync('-a', f'--partial-dir={ext}', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='abs partial-dir delta resume')
if (ext / 'f3').exists():
    test_fail("absolute --partial-dir basis was not consumed after a "
              "successful delta resume")

print("partial: --partial + relative/absolute --partial-dir verified at depth")
