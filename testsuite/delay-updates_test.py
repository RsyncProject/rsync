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
# Touch both to the same time so they look stale-but-recent.
ref_st = os.stat('..')
os.utime(TODIR / '.~tmp~' / 'foo', (ref_st.st_atime, ref_st.st_mtime))
os.utime(TODIR / 'foo', (ref_st.st_atime, ref_st.st_mtime))
(FROMDIR / 'foo').write_text("3\n")

checkit(['-aiv', '--delay-updates', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
