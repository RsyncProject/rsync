#!/usr/bin/env python3
# Finding [27]: rsync's remote-shell argument quoting (SHELL_CHARS in options.c)
# omitted the newline/CR command separators, so a newline in a remote path could
# inject a separate command into the remote shell.  The test rsh support/lsh.sh
# runs the remote command via `eval "${@}"`, so an UNescaped newline in the
# destination splits off a second line that runs `touch <sentinel>`.  The fix
# adds \n\r to SHELL_CHARS so such args are quoted and the newline stays literal.

import os
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, rmtree, rsync_argv, test_fail, rsh_cmd

base = SCRATCHDIR / 'remote-shell-newline'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
(src / 'f').write_text('payload\n')
sentinel = base / 'pwned'

env = os.environ.copy()
env['RSYNC_RSH'] = rsh_cmd(None, '--no-cd')
dest = f"lh:{base}/dest\ntouch {sentinel}\n#"
subprocess.run(rsync_argv('-a', f'{src}/', dest),
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

# Without the fix, lsh.sh's eval splits on the newline: the first line runs the
# server into base/dest and the second runs `touch <sentinel>`.  With the newline
# quoted the whole string is one literal (invalid) destination, so neither effect
# happens.  Both checks are filesystem-independent -- they don't depend on whether
# a newline-bearing path is creatable, which differs across Linux/macOS/BSD.
if sentinel.exists():
    test_fail("remote-shell newline injection executed the sentinel command")
if (base / 'dest').exists():
    test_fail("remote-shell newline split the destination "
              "(eval ran the server into base/dest as a separate command)")

print("remote-shell-newline-escaping: newline in a remote arg is quoted, not executed")
