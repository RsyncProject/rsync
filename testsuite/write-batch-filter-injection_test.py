#!/usr/bin/env python3
# Regression test: the generated --write-batch replay script writes filter rules
# into a `<<'#E#'` here-doc, one rule per line.  A filter pattern that contains
# an embedded newline followed by a line equal to the here-doc terminator "#E#"
# could close the here-doc early and turn the remaining pattern bytes into shell
# commands in the replay .sh.  Command-line --filter/--exclude/--include keep an
# embedded newline (no word-splitting), and such a pattern can also come from a
# NUL-separated --exclude-from in an untrusted tree.  The batch writer must refuse
# a newline-bearing pattern (fail-closed) so no injectable script is produced.

import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'wb-filter-inj'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
(src / 'keep').write_text("hello\n")

# Relative sentinel path so a fired injection lands here, where we run the .sh.
os.chdir(base)
sentinel = base / 'PWNED_FILTER'
if sentinel.exists():
    sentinel.unlink()

# A single filter rule whose pattern embeds:  <newline> #E# <newline> touch ...
# Pre-fix this renders as
#     - keep
#     #E#
#     touch PWNED_FILTER
# inside the here-doc, so the "#E#" line closes it early and the touch runs as a
# shell command when the replay script is executed.
evil = "- keep\n#E#\ntouch PWNED_FILTER"

# check=False: the fixed binary refuses (exits non-zero) and the transfer itself
# may not complete; we judge solely by whether the injection can fire.
run_rsync('-a', '--write-batch=B', f'--filter={evil}', 'src/', 'dest/', check=False)

batch_sh = base / 'B.sh'
if batch_sh.exists():
    # The replay script must not, when run, execute the injected command.
    subprocess.run(['sh', 'B.sh'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=str(base), env=os.environ.copy())

if sentinel.exists():
    script = batch_sh.read_text() if batch_sh.exists() else '<no script>'
    test_fail("a newline in a filter rule forged the here-doc terminator and "
              f"injected a shell command into the replay script:\n{script}")

print("write-batch-filter-injection: a newline-bearing filter rule cannot "
      "inject into the replay script")
