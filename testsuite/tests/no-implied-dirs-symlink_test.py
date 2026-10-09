#!/usr/bin/env python3
"""Pin the receiver-side symlink behaviour of -R `--no-implied-dirs`.

The rsync(1) `--no-implied-dirs` entry describes the effect euphemistically:
"the receiving rsync updates "path/foo/file" using the existing path elements,
which means that the file ends up being created in "path/bar"."  It does not
spell out the security-relevant consequence: an EXISTING destination symlink
used as a path element is FOLLOWED, so the write is silently redirected through
it.  The default (implied dirs) instead deletes that symlink and recreates it as
a real directory.

This test makes both behaviours explicit and measured.  Pure local client
behaviour: no daemon/root/tcp.  Cross-version: expected identical against
--rsync-bin=old_versions/rsync_3.2.7.
"""

import os
import subprocess

from rsyncfns import SCRATCHDIR, forced_protocol, rmtree, rsync_argv, test_fail

# The -R "/./" implied-dir marker is a protocol-30+ feature; at protocol 29 the
# generator rejects the multi-component "path/file" as an invalid path and
# aborts (verified identical on the 3.2.7 oracle), so the behaviour this test
# pins isn't reachable there.  Pass-through rather than skip, matching the
# sibling relative-implied test's handling of the same proto-29 limitation.
proto = forced_protocol()
if proto is not None and proto < 30:
    print(f"no-implied-dirs-symlink: protocol {proto} -- the -R /./ implied-dir "
          "path element is rejected by the proto-29 generator; nothing to test")
    raise SystemExit(0)

base = SCRATCHDIR / 'no-implied-dirs'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
(src / 'path').mkdir(parents=True)
(src / 'path' / 'file').write_text('NEW\n')


def run(dst, *opts):
    """Fresh dst with 'path' pre-existing as a symlink -> 'real'; transfer
    src/./path/file with -R and the given opts."""
    rmtree(dst)
    (dst / 'real').mkdir(parents=True)
    os.symlink('real', dst / 'path')
    subprocess.run(rsync_argv('-a', '-R', *opts, f'{src}/./path/file', f'{dst}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


# --no-implied-dirs: the dest symlink is kept and followed -> file lands in real/.
d = run(base / 'noimplied', '--no-implied-dirs')
if not (d / 'path').is_symlink():
    test_fail("--no-implied-dirs: dest 'path' symlink was replaced (expected kept)")
through = d / 'real' / 'file'
if not through.is_file() or through.read_text() != 'NEW\n':
    test_fail("--no-implied-dirs: write was not redirected through the symlink "
              "(expected real/file to receive the content)")

# default (implied dirs): the dest symlink is deleted and recreated as a real dir.
d = run(base / 'implied')
if (d / 'path').is_symlink():
    test_fail("default: dest 'path' symlink should have been replaced by a real dir")
direct = d / 'path' / 'file'
if not direct.is_file() or direct.read_text() != 'NEW\n':
    test_fail("default: file did not land in the recreated real directory")

print("no-implied-dirs-symlink: -R --no-implied-dirs FOLLOWS an existing dest "
      "symlink path element (write redirected through it); the default replaces "
      "the symlink with a real directory")
