#!/usr/bin/env python3
# Regression test: the generated --write-batch replay script must single-quote
# every argument -- including an arg that starts with '-' and contains '=' (the
# write_arg "--opt=" split) -- so shell metacharacters in a path (backtick or
# $(...) command substitution, redirection, newline, ...) cannot execute when
# the .sh is run.

import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'wb-quoting'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
(src / 'f').write_text("hello\n")

# Relative paths so a fired substitution writes its sentinel here, where we run
# the generated replay script.
os.chdir(base)


def check(label, dest_arg, sentinel_name, *extra):
    sentinel = base / sentinel_name
    if sentinel.exists():
        sentinel.unlink()
    # check=False: a '-'-leading dest after '--' makes the transfer itself fail,
    # but write-batch still emits the .sh (the artifact we care about).
    run_rsync('-a', '--write-batch=B', 'src/', *extra, dest_arg, check=False)
    batch_sh = base / 'B.sh'
    if not batch_sh.exists():
        test_fail(f"{label}: write-batch did not generate the replay script")
    script = batch_sh.read_text()
    # Command substitution in any unquoted token fires during shell word
    # expansion, independent of whether rsync itself runs.
    subprocess.run(['sh', 'B.sh'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=str(base), env=os.environ.copy())
    if sentinel.exists():
        test_fail(f"{label}: metacharacters executed when the replay script ran:\n{script}")


# Backtick command substitution in a bare path token (the value-quoting path).
# The body uses only `backtick` and `>` -- neither in the old quote-trigger set.
check("bare-path", 'd`>PWNED`x/', 'PWNED')

# Command substitution in the prefix before '=' of a '-'-leading destination,
# passed after '--' (exercises the write_arg "--opt=" split). Here `$(` and `)`
# would be caught by value-quoting, but the prefix bytes bypass it unless the
# whole arg is quoted.
check("dash-eq-prefix", '-x$(>PWNED2)=y/', 'PWNED2', '--')

print("write-batch-quoting: shell metacharacters in batch paths stay quoted")
