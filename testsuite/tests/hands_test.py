#!/usr/bin/env python3
# Python rewrite of testsuite/hands.test.
#
# The canonical end-to-end transfer test: build a richly-populated source
# tree via hands_setup() then run a series of rsync invocations covering
# basic operation, hard links, single-file copies, --no-whole-file delta
# updates and --delete cleanup. After each run the source and destination
# tree listings must match exactly.

import os
import shutil

from rsyncfns import FROMDIR, TMPDIR, TODIR, checkit, hands_setup, run_rsync


hands_setup()

DEBUG_OPTS = "--debug=all0,deltasum0"


# 1. basic operation
print("Test basic operation:")
checkit(['-av', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

# 2. hard links — link filelist into dir/ then transfer with -H so the
# receiver should recreate the link relationship.
os.link(FROMDIR / 'filelist', FROMDIR / 'dir' / 'filelist')
print("Test hard links:")
checkit(['-avH', '--bwlimit=0', DEBUG_OPTS, f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

# 3. one file — delete the destination 'text' and re-sync; only it should
# transfer, everything else stays uptodate.
(TODIR / 'text').unlink()
print("Test one file:")
checkit(['-avH', DEBUG_OPTS, f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

# 4. extra data — append to destination then re-sync with --no-whole-file so
# the rsync delta algorithm has to repair it.
with open(TODIR / 'text', 'a') as f:
    f.write("extra line\n")
print("Test extra data:")
checkit(['-avH', DEBUG_OPTS, '--no-whole-file', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

# 5. --delete — add a stray file on the destination and confirm --delete
# removes it.
shutil.copy(FROMDIR / 'text', TODIR / 'ThisShouldGo')
print("Test --delete:")
checkit(['--delete', '-avH', DEBUG_OPTS, f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

# 6. globbed copy without recursion — wipe and re-sync top-level entries by
# glob, then a final empty pass to compare listings.
os.chdir(TMPDIR)
shutil.rmtree('to', ignore_errors=True)
for entry in TMPDIR.glob('from/*dir'):
    if entry.is_dir():
        shutil.rmtree(entry, ignore_errors=True)
    else:
        entry.unlink()

# Replicate `rsync -av from/* to/` — list the from/ children explicitly.
sources = sorted(str(p) for p in (TMPDIR / 'from').iterdir())
run_rsync('-av', *sources, 'to/')
checkit(['-av', '--exclude=*', 'from/', 'to/'], FROMDIR, TODIR)
