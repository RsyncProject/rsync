#!/usr/bin/env python3

import re
import subprocess

from harness.rsync import (
    FROMDIR, TODIR,
    forced_protocol, makepath, rmtree, rsync_argv, test_fail,
)

src = FROMDIR

rmtree(src)
rmtree(TODIR)
makepath(src)
(src / 'percentfile').write_text("data\n")

p = subprocess.run(
    rsync_argv('-a', '--out-format=100%% done %f', f'{src}/', f'{TODIR}/'),
    capture_output=True, text=True,
)
if p.returncode != 0:
    test_fail(f"rsync exited {p.returncode}:\n{p.stderr}")

if '100% done ' not in p.stdout or 'percentfile' not in p.stdout:
    test_fail(
        "expected '100% done ' (a single literal percent) and 'percentfile' in "
        f"--out-format output, got:\n{p.stdout}"
    )

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

proto = forced_protocol()
if proto is None or proto >= 30:
    plain = checksum_for('%C')
    wide = checksum_for('%' + '0' * 30 + '%C')
    if not wide.endswith(plain):
        test_fail(
            f"over-wide %C rendered ...{wide[-len(plain):]!r} but plain %C rendered "
            f"{plain!r}: log_format_has()/log_formatted() disagree on the escape position"
        )

print('log format width and literal percent handling verified')
