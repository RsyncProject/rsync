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
    forced_protocol, makepath, rmtree, rsync_argv, test_fail,
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

# log_format_has() must not see the 'i' in '%%i' as an itemize escape: the
# second '%' of a '%%' is a literal, not the start of a new specifier.  A
# misdetection turns on itemizing, which logs attribute-only changes that a
# plain --out-format run must not mention.
(src / 'percentfile').chmod(0o640)
p = subprocess.run(
    rsync_argv('-a', '--out-format=%%i %n', f'{src}/', f'{TODIR}/'),
    capture_output=True, text=True,
)
if p.returncode != 0:
    test_fail(f"rsync exited {p.returncode}:\n{p.stderr}")
(src / 'percentfile').chmod(0o600)
p = subprocess.run(
    rsync_argv('-a', '--out-format=%%i %n', f'{src}/', f'{TODIR}/'),
    capture_output=True, text=True,
)
if p.returncode != 0:
    test_fail(f"rsync exited {p.returncode}:\n{p.stderr}")
if 'percentfile' in p.stdout:
    test_fail(
        "'%%i' misdetected as itemizing: an attribute-only change was logged "
        f"by --out-format='%%i %n':\n{p.stdout}"
    )

# And '%%i' on a really transferred file still renders as literal '%i'.
(src / 'percentfile').write_text("data2\n")
p = subprocess.run(
    rsync_argv('-a', '--out-format=%%i %n', f'{src}/', f'{TODIR}/'),
    capture_output=True, text=True,
)
if p.returncode != 0:
    test_fail(f"rsync exited {p.returncode}:\n{p.stderr}")
if '%i percentfile' not in p.stdout:
    test_fail(
        "expected literal '%i percentfile' for a transferred file with "
        f"--out-format='%%i %n', got:\n{p.stdout}"
    )

# log_format_has() and log_formatted() must agree on where an escape letter is
# even for an over-long field width.  log_formatted() stops consuming width
# digits at its 32-byte fmt buffer, then rescans and expands the trailing %C;
# an unbounded log_format_has() instead swallows every digit and the following
# '%' as a literal %%, missing the %C.  With -c that leaves sender_keeps_checksum
# unset while %C still reads F_SUM -- a heap over-read (ASan: use-after-poison in
# sum_as_hex), and the rendered digest is garbage.
#
# Oracle without a sanitizer: the digest %C renders for the over-wide format
# must equal the digest a plain '%C' renders for the same file.  A parser
# divergence leaves the wide-format sender without a retained checksum, so its
# %C differs (or is garbage) while the plain %C is correct.
import re

def checksum_for(fmt):
    (src / 'percentfile').write_text("data3\n")
    rmtree(TODIR)
    r = subprocess.run(
        rsync_argv('-a', '-c', f'--out-format={fmt} %n', f'{src}/', f'{TODIR}/'),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        test_fail(f"rsync -c exited {r.returncode} for --out-format={fmt!r}:\n{r.stderr}")
    m = re.search(r'([0-9a-f]+) percentfile', r.stdout)
    if not m:
        test_fail(f"no '<hex> percentfile' in --out-format={fmt!r} output:\n{r.stdout}")
    return m.group(1)

# The over-wide %C check needs %C to actually render a hex digest, which only
# happens for a canonical checksum: at protocol < 30 the negotiated file checksum
# is a non-canonical MD4 variant and %C (via sum_as_hex) renders empty, so there
# is no digest to compare and no F_SUM read to over-run.  Skip that sub-case
# there; at protocol 30+ checksum_for() still fails on a missing digest, so a
# real regression is caught.  The %% literal checks above are protocol-independent.
proto = forced_protocol()
if proto is None or proto >= 30:
    # The over-wide format renders its width digits literally, so the digest is
    # the trailing run of hex; compare that tail against the plain %C digest.
    plain = checksum_for('%C')
    wide = checksum_for('%' + '0' * 30 + '%C')
    if not wide.endswith(plain):
        test_fail(
            f"over-wide %C rendered ...{wide[-len(plain):]!r} but plain %C rendered "
            f"{plain!r}: log_format_has()/log_formatted() disagree on the escape position"
        )

print("ki58-log-format-percent: %% literal-percent escape verified")
