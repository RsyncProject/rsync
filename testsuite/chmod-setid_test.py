#!/usr/bin/env python3
# Regression for KI-55: --chmod=a+s must set BOTH setuid and setgid (like
# chmod(1)).  parse_chmod()'s 'a' clause used to leave the set-id bits unset, so
# "a+s" fell through to setuid only.

import os

from rsyncfns import SCRATCHDIR, run_rsync, test_fail

base = SCRATCHDIR / 'chmod_setid'
src = base / 'src'
dst = base / 'dst'
src.mkdir(parents=True, exist_ok=True)
dst.mkdir(parents=True, exist_ok=True)
(src / 'f').write_text('hi\n')
os.chmod(src / 'f', 0o644)

run_rsync('-rp', '--chmod=a+s', f'{src}/', f'{dst}/')

mode = (dst / 'f').stat().st_mode & 0o7777
if not (mode & 0o4000) or not (mode & 0o2000):
    test_fail(f'--chmod=a+s did not set both setuid+setgid (mode {mode:04o})')

# Sanity: u+s is setuid only, g+s is setgid only.
for spec, want, unwant in (('u+s', 0o4000, 0o2000), ('g+s', 0o2000, 0o4000)):
    d = base / spec.replace('+', '_')
    d.mkdir(exist_ok=True)
    run_rsync('-rp', f'--chmod={spec}', f'{src}/', f'{d}/')
    m = (d / 'f').stat().st_mode & 0o7777
    if not (m & want) or (m & unwant):
        test_fail(f'--chmod={spec} gave mode {m:04o} (want {want:04o}, not {unwant:04o})')

print('chmod-setid: a+s sets setuid+setgid; u+s/g+s stay single-bit')
