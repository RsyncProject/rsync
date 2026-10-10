#!/usr/bin/env python3

import os

from harness.rsync import FROMDIR, TODIR, checkit

FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / 'foo').write_text("1\n")

checkit(['-aiv', '--delay-updates', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

(TODIR / '.~tmp~').mkdir(exist_ok=True)
(TODIR / '.~tmp~' / 'foo').write_text("2\n")
stale = 1_000_000_000
os.utime(TODIR / '.~tmp~' / 'foo', (stale, stale))
os.utime(TODIR / 'foo', (stale, stale))
(FROMDIR / 'foo').write_text("3\n")

checkit(['-aiv', '--delay-updates', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
