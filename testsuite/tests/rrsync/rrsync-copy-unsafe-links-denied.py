#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
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
os.symlink('../outside/secret', served / 'leak')

dest = base / 'dest'
dest.mkdir()
subprocess.run(rsync_argv('-a', '--copy-unsafe-links', f'{served}/', f'{dest}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
leaked = dest / 'leak'
if not (leaked.is_file() and not leaked.is_symlink() and leaked.read_text() == SECRET):
    test_fail("premise check failed: --copy-unsafe-links should turn the escaping "
              "symlink into a regular file holding the outside secret")

run_rrsync_denied('rsync --server --sender --copy-unsafe-links . .',
                  'option --copy-unsafe-links has been disabled')

print("rrsync-copy-unsafe-links: --copy-unsafe-links exfiltrates outside symlink "
      "targets; restricted rrsync refuses it")
