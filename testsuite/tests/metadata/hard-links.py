#!/usr/bin/env python3

import os
import shutil
import subprocess

from harness.rsync import (
    CHKDIR,
    FROMDIR,
    OUTFILE,
    SRCDIR,
    TODIR,
    checkit,
    makepath,
    rsync_argv,
    test_fail,
    test_skipped,
    rsync_path_arg,
    rsh_cmd,
)
from harness import metadata

metadata(features={'hardlinks', 'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', cost='expensive', mutates={'filesystem', 'process'}, tags={'compatibility', 'metadata', 'transfer', 'version-mix'})

SSH = rsh_cmd()

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

with open(TODIR / 'name1', 'a') as f:
    f.write("extra extra\n")

checkit(['-aHivv', '--debug=HLINK5', '--no-whole-file',
         f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

makepath(FROMDIR / 'subdir' / 'down' / 'deep')

cdir = FROMDIR / 'subdir'
chars = list('abcdefghijklmnopqrstuvwxyz0123456789')
for x in chars:
    for y in chars:
        (cdir / f'{x}{y}').touch()

os.link(name1, FROMDIR / 'subdir' / 'down' / 'deep' / 'new-file')
(TODIR / 'text').unlink()

checkit(['-aHivve', SSH, '--debug=HLINK5', f'--rsync-path={rsync_path_arg()}',
         f'{FROMDIR}/', f'localhost:{TODIR}/'], FROMDIR, TODIR)

checkit(['-aHivv', '--debug=HLINK5', f'--link-dest={TODIR}',
         f'{FROMDIR}/', f'{CHKDIR}/'], TODIR, CHKDIR)

shutil.rmtree(CHKDIR, ignore_errors=True)
checkit(['-aHivv', '--debug=HLINK5', f'--copy-dest={TODIR}',
         f'{FROMDIR}/', f'{CHKDIR}/'], FROMDIR, CHKDIR)

(FROMDIR / 'solo').write_text("This is another file\n")
try:
    os.link(FROMDIR / 'solo', CHKDIR / 'solo')
except OSError:
    test_fail("Can't create hardlink")

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

shutil.rmtree(TODIR, ignore_errors=True)
TODIR.mkdir(parents=True, exist_ok=True)
subprocess.run(rsync_argv('-aHivv', '--debug=HLINK5', str(name1), f'{TODIR}/'))
diff = subprocess.run(['diff', '-u', str(name1), str(TODIR / 'name1')])
if diff.returncode != 0:
    test_fail("solo copy of name1 failed")

shutil.rmtree(FROMDIR, ignore_errors=True)
shutil.rmtree(TODIR, ignore_errors=True)
makepath(FROMDIR / 'sym', TODIR)
subprocess.run(rsync_argv('-aH', str(FROMDIR / 'sym'), str(TODIR)))
diff = subprocess.run(['diff', '-r', '-u', str(FROMDIR), str(TODIR)])
if diff.returncode != 0:
    test_fail("solo copy of sym failed")
