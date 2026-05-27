#!/usr/bin/env python3
# Python rewrite of testsuite/hardlinks.test.
#
# Verify rsync -H detects hard links and re-creates them on the receiver.
# Also covers the incremental-recursion path (lots of small files), the
# remote (lsh.sh) path, --link-dest / --copy-dest, --checksum without
# slipping HLINK_BUMP boundaries, and the single-file / single-directory
# corner cases that have broken in the past.

import os
import shutil
import subprocess

from rsyncfns import (
    CHKDIR, FROMDIR, OUTFILE, RSYNC, SRCDIR, TODIR,
    checkit, makepath, rsync_argv, test_fail, test_skipped,
)


SSH = str(SRCDIR / 'support' / 'lsh.sh')

FROMDIR.mkdir(parents=True, exist_ok=True)
name1 = FROMDIR / 'name1'
name2 = FROMDIR / 'name2'
name3 = FROMDIR / 'name3'
name4 = FROMDIR / 'name4'
name1.write_text("This is the file\n")
try:
    os.link(name1, name2)
except OSError:
    test_skipped("Can't create hardlink")
try:
    os.link(name2, name3)
except OSError:
    test_fail("Can't create hardlink")
shutil.copy(name2, name4)

text = bytearray()
for f in sorted(SRCDIR.glob('*.c')):
    text.extend(f.read_bytes())
(FROMDIR / 'text').write_bytes(bytes(text))

checkit(['-aHivv', '--debug=HLINK5', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

# Force a delta-overwrite on one of the linked names; -H should still
# leave name1..name3 hard-linked on the destination.
with open(TODIR / 'name1', 'a') as f:
    f.write("extra extra\n")

checkit(['-aHivv', '--debug=HLINK5', '--no-whole-file',
         f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

# Add a new link under a subdir that doesn't exist on the dest yet, plus
# pile on lots of small files to exercise incremental recursion's link
# bookkeeping across batches.
makepath(FROMDIR / 'subdir' / 'down' / 'deep')

cdir = FROMDIR / 'subdir'
chars = list('abcdefghijklmnopqrstuvwxyz0123456789')
for x in chars:
    for y in chars:
        (cdir / f'{x}{y}').touch()

os.link(name1, FROMDIR / 'subdir' / 'down' / 'deep' / 'new-file')
(TODIR / 'text').unlink()

checkit(['-aHivve', SSH, '--debug=HLINK5', f'--rsync-path={RSYNC}',
         f'{FROMDIR}/', f'localhost:{TODIR}/'], FROMDIR, TODIR)

# --link-dest and --copy-dest should also keep hard-linked entries.
checkit(['-aHivv', '--debug=HLINK5', f'--link-dest={TODIR}',
         f'{FROMDIR}/', f'{CHKDIR}/'], TODIR, CHKDIR)

shutil.rmtree(CHKDIR, ignore_errors=True)
checkit(['-aHivv', '--debug=HLINK5', f'--copy-dest={TODIR}',
         f'{FROMDIR}/', f'{CHKDIR}/'], FROMDIR, CHKDIR)

# Make a hard link whose other end is outside the source -- the dest
# stays single-linked -- and re-sync with --checksum.
(FROMDIR / 'solo').write_text("This is another file\n")
try:
    os.link(FROMDIR / 'solo', CHKDIR / 'solo')
except OSError:
    test_fail("Can't create hardlink")

# Make sure --checksum doesn't slip the offset due to an HLINK_BUMP() change.
proc = subprocess.run(
    rsync_argv('-aHivc', '--debug=HLINK5', f'{FROMDIR}/', f'{CHKDIR}/'),
    capture_output=True, text=True,
)
OUTFILE.write_text(proc.stdout)
print(proc.stdout, end='')
if proc.returncode != 0:
    test_fail(f"-aHivc run exited {proc.returncode}")
if 'solo' in proc.stdout:
    test_fail("Erroneous copy of solo file occurred!")

# Single-file with -H is a regression-prone path; just confirm it copies.
shutil.rmtree(TODIR, ignore_errors=True)
TODIR.mkdir(parents=True, exist_ok=True)
subprocess.run(rsync_argv('-aHivv', '--debug=HLINK5', str(name1), f'{TODIR}/'))
diff = subprocess.run(['diff', '-u', str(name1), str(TODIR / 'name1')])
if diff.returncode != 0:
    test_fail("solo copy of name1 failed")

# Single-directory with -H is the 3.4.0 regression.
shutil.rmtree(FROMDIR, ignore_errors=True)
shutil.rmtree(TODIR, ignore_errors=True)
makepath(FROMDIR / 'sym', TODIR)
subprocess.run(rsync_argv('-aH', str(FROMDIR / 'sym'), str(TODIR)))
diff = subprocess.run(['diff', '-r', '-u', str(FROMDIR), str(TODIR)])
if diff.returncode != 0:
    test_fail("solo copy of sym failed")
