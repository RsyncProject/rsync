#!/usr/bin/env python3
# Finding [8] (CWE-59): support/rrsync leaves --copy-unsafe-links enabled in a
# restricted (non-"/") directory.  A restricted SSH user who can request
# --copy-unsafe-links can have the spawned rsync sender dereference a symlink
# that points OUTSIDE the restricted tree and copy the *referent's content* out,
# exfiltrating files readable by the rrsync account but outside the allowed
# policy directory.
#
# Part 1 demonstrates the danger of the option itself: a symlink inside the
# served tree pointing at an outside secret is dereferenced by --copy-unsafe-
# links, so the outside content lands in the puller's copy.  Part 2 is the fix:
# a restricted rrsync now refuses --copy-unsafe-links, so the part-1 exfiltration
# cannot be requested through the wrapper.  Runs at any uid.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, run_rrsync_denied, test_fail,
)

SECRET = 'OUTSIDE-SECRET-readable-by-the-rrsync-account\n'

base = SCRATCHDIR / 'rrsync-copy-unsafe'
rmtree(base)
served = base / 'restricted'
outside = base / 'outside'
served.mkdir(parents=True)
outside.mkdir()
(outside / 'secret').write_text(SECRET)
# An "unsafe" symlink: it lives inside the served tree but its target escapes it.
os.symlink('../outside/secret', served / 'leak')

# --- Part 1: --copy-unsafe-links dereferences the escaping symlink (the danger).
dest = base / 'dest'
dest.mkdir()
subprocess.run(rsync_argv('-a', '--copy-unsafe-links', f'{served}/', f'{dest}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
leaked = dest / 'leak'
if not (leaked.is_file() and not leaked.is_symlink() and leaked.read_text() == SECRET):
    test_fail("premise check failed: --copy-unsafe-links should turn the escaping "
              "symlink into a regular file holding the outside secret")

# --- Part 2: a restricted rrsync must refuse --copy-unsafe-links, so the
# exfiltration shown in part 1 cannot be requested through the wrapper.
run_rrsync_denied('rsync --server --sender --copy-unsafe-links . .',
                  'option --copy-unsafe-links has been disabled')

print("rrsync-copy-unsafe-links: --copy-unsafe-links exfiltrates outside symlink "
      "targets; restricted rrsync refuses it")
