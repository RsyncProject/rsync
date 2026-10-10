#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'wb-filter-inj'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
(src / 'keep').write_text("hello\n")

os.chdir(base)
sentinel = base / 'PWNED_FILTER'
if sentinel.exists():
    sentinel.unlink()

evil = "- keep\n#E#\ntouch PWNED_FILTER"

run_rsync('-a', '--write-batch=B', f'--filter={evil}', 'src/', 'dest/', check=False)

batch_sh = base / 'B.sh'
if batch_sh.exists():
    subprocess.run(['sh', 'B.sh'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=str(base), env=os.environ.copy())

if sentinel.exists():
    script = batch_sh.read_text() if batch_sh.exists() else '<no script>'
    test_fail("a newline in a filter rule forged the here-doc terminator and "
              f"injected a shell command into the replay script:\n{script}")

print("write-batch-filter-injection: a newline-bearing filter rule cannot "
      "inject into the replay script")
