#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, rsync_argv, test_fail, rsh_cmd

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

if sentinel.exists():
    test_fail("remote-shell newline injection executed the sentinel command")
if (base / 'dest').exists():
    test_fail("remote-shell newline split the destination "
              "(eval ran the server into base/dest as a separate command)")

print("remote-shell-newline-escaping: newline in a remote arg is quoted, not executed")
