#!/usr/bin/env python3
"""Regression test: `%%` in --out-format outputs a literal `%`.

# Verifies: SW-REQ-166

The log_formatted() switch in log.c had no `case '%'`, so `%%` left the
first `%` in place and re-parsed the second `%` as the start of a new
specifier.  This test transfers a file with `--out-format='100%% done %f'`
and asserts the output contains `100% done <filename>` (a single literal
percent, a space, the word "done", and the expanded filename).
"""

import subprocess

from rsyncfns import (
    FROMDIR, TODIR,
    makepath, rmtree, rsync_argv, test_fail,
)

src = FROMDIR

# Build a single-file source tree.
rmtree(src)
rmtree(TODIR)
makepath(src)
(src / 'percentfile').write_text("data\n")

# Transfer using a format string with both %% (literal percent) and %f (name).
p = subprocess.run(
    rsync_argv('-a', '--out-format=100%% done %f', f'{src}/', f'{TODIR}/'),
    capture_output=True, text=True,
)
if p.returncode != 0:
    test_fail(f"rsync exited {p.returncode}:\n{p.stderr}")

# %f expands to the transfer-relative path, which includes leading directories
# when the source is addressed by an absolute path (as here) -- so don't pin the
# exact filename.  The point of this test is the escape: "100% done " with a
# single literal percent followed by a space (a broken %% consumes the space or
# leaves a doubled percent), plus the transferred file name somewhere.
if '100% done ' not in p.stdout or 'percentfile' not in p.stdout:
    test_fail(
        "expected '100% done ' (a single literal percent) and 'percentfile' in "
        f"--out-format output, got:\n{p.stdout}"
    )

print("ki58-log-format-percent: %% literal-percent escape verified")
