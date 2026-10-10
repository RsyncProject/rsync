#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'wb-quoting'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
(src / 'f').write_text("hello\n")

os.chdir(base)

def check(label, dest_arg, sentinel_name, *extra):
    sentinel = base / sentinel_name
    if sentinel.exists():
        sentinel.unlink()
    run_rsync('-a', '--write-batch=B', 'src/', *extra, dest_arg, check=False)
    batch_sh = base / 'B.sh'
    if not batch_sh.exists():
        test_fail(f"{label}: write-batch did not generate the replay script")
    script = batch_sh.read_text()
    subprocess.run(['sh', 'B.sh'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=str(base), env=os.environ.copy())
    if sentinel.exists():
        test_fail(f"{label}: metacharacters executed when the replay script ran:\n{script}")

check("bare-path", 'd`>PWNED`x/', 'PWNED')

check("dash-eq-prefix", '-x$(>PWNED2)=y/', 'PWNED2', '--')

print("write-batch-quoting: shell metacharacters in batch paths stay quoted")
