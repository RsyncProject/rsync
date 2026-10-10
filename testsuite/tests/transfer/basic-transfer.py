#!/usr/bin/env python3

import os
import shutil

from harness.rsync import FROMDIR, TMPDIR, TODIR, checkit, hands_setup, run_rsync

hands_setup()

DEBUG_OPTS = "--debug=all0,deltasum0"

print("Test basic operation:")
checkit(['-av', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

os.link(FROMDIR / 'filelist', FROMDIR / 'dir' / 'filelist')
print("Test hard links:")
checkit(['-avH', '--bwlimit=0', DEBUG_OPTS, f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

(TODIR / 'text').unlink()
print("Test one file:")
checkit(['-avH', DEBUG_OPTS, f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

with open(TODIR / 'text', 'a') as f:
    f.write("extra line\n")
print("Test extra data:")
checkit(['-avH', DEBUG_OPTS, '--no-whole-file', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

shutil.copy(FROMDIR / 'text', TODIR / 'ThisShouldGo')
print("Test --delete:")
checkit(['--delete', '-avH', DEBUG_OPTS, f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

os.chdir(TMPDIR)
shutil.rmtree('to', ignore_errors=True)
for entry in TMPDIR.glob('from/*dir'):
    if entry.is_dir():
        shutil.rmtree(entry, ignore_errors=True)
    else:
        entry.unlink()

sources = sorted(str(p) for p in (TMPDIR / 'from').iterdir())
run_rsync('-av', *sources, 'to/')
checkit(['-av', '--exclude=*', 'from/', 'to/'], FROMDIR, TODIR)
