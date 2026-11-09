#!/usr/bin/env python3
# Python rewrite of testsuite/delay-updates.test.
#
# Exercise --delay-updates: pre-seed the destination's staging directory
# with a stale file then re-sync; the final destination must match the
# source regardless of what the staging dir already contained.

import os

from rsyncfns import FROMDIR, TODIR, checkit


FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / 'foo').write_text("1\n")

checkit(['-aiv', '--delay-updates', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

# Plant a stale "in-progress" update in the staging dir and a mismatched
# destination file, then re-sync. --delay-updates should overwrite cleanly.
(TODIR / '.~tmp~').mkdir(exist_ok=True)
(TODIR / '.~tmp~' / 'foo').write_text("2\n")
# Touch both to a fixed time in the past so they look stale and the source's
# fresh "3" clearly post-dates them.  (A real timestamp, not os.stat('..') --
# whose shared parent dir is bumped by concurrent -j tests to ~now -- avoids a
# same-second quick-check collision that would skip the update.)
stale = 1_000_000_000  # 2001-09-09, well in the past
os.utime(TODIR / '.~tmp~' / 'foo', (stale, stale))
os.utime(TODIR / 'foo', (stale, stale))
(FROMDIR / 'foo').write_text("3\n")

checkit(['-aiv', '--delay-updates', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
