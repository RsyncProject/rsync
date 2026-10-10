#!/usr/bin/env python3

import subprocess

from harness.rsync import TOOLDIR, test_fail

INPUTS = [
    "/usr/local/bin",
    "/usr/local/bin/",
    "/usr/local/bin///",
    "//a//",
    "////",
    "/Users/Weird Macintosh Name/// Ooh, translucent plastic/",
]

EXPECTED = """\
/usr/local/bin
/usr/local/bin
/usr/local/bin
//a
/
/Users/Weird Macintosh Name/// Ooh, translucent plastic
"""

proc = subprocess.run(
    [str(TOOLDIR / 'trimslash'), *INPUTS],
    capture_output=True, text=True,
)
if proc.returncode != 0:
    test_fail(f"trimslash exited {proc.returncode}\nstderr:\n{proc.stderr}")

if proc.stdout != EXPECTED:
    test_fail(
        "trimslash output did not match expected.\n"
        f"--- expected ---\n{EXPECTED}"
        f"--- got ---\n{proc.stdout}"
    )
