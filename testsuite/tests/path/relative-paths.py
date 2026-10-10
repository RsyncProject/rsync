#!/usr/bin/env python3

import os
import shutil
import subprocess

from harness import rsync
from harness.rsync import (
    CHKDIR, FROMDIR, OUTFILE, TMPDIR, TODIR,
    checkit, hands_setup, makepath, rsync_argv,
    run_rsync, test_fail,
)

deepstr = 'down/3/deep'
deepdir = FROMDIR / deepstr
extradir = TMPDIR / 'extra'

makepath(deepdir, extradir / deepstr, CHKDIR)

real_fromdir = rsync.FROMDIR
try:
    rsync.FROMDIR = deepdir
    hands_setup()
finally:
    rsync.FROMDIR = real_fromdir

extrafile = extradir / deepstr / 'extra.added.value'
extrafile.write_text("wowza\n")
extrafile_for_rsync = f"{extradir}/./{deepstr}/extra.added.value"

run_rsync('-av', '--existing', '--include=*/', '--exclude=*',
          f'{FROMDIR}/', f'{extradir}/')

os.chdir(FROMDIR)

run_rsync('-ai', '--include=/down/', '--exclude=/*',
          f'{FROMDIR}/', f'{CHKDIR}/')

print("Test basic relative:")
checkit(['-avR', f'./{deepstr}', str(TODIR)], CHKDIR, TODIR)

os.link(deepdir / 'filelist', deepdir / 'dir' / 'filelist')
os.link(CHKDIR / deepstr / 'filelist', CHKDIR / deepstr / 'dir' / 'filelist')
src_t = (deepdir / 'dir').stat().st_mtime
os.utime(deepdir / 'dir', (src_t, src_t))
os.utime(CHKDIR / deepstr / 'dir', (src_t, src_t))

print("Test hard links:")
checkit(['-avHR', f'./{deepstr}/', str(TODIR)], CHKDIR, TODIR)

shutil.copy(deepdir / 'text', TODIR / deepstr / 'ThisShouldGo')
shutil.copy(deepdir / 'text', TODIR / deepstr / 'dir' / 'ThisShouldGoToo')

print("Test deletion:")
checkit(['-avHR', '--del', f'./{deepstr}/', str(TODIR)], CHKDIR, TODIR)

print("Test non-deletion:")
proc = subprocess.run(
    rsync_argv('-aiHR', '--del', f'./{deepstr}/', str(TODIR)),
    capture_output=True, text=True,
)
OUTFILE.write_text(proc.stdout)
print(proc.stdout, end='')
if proc.returncode != 0:
    test_fail(f"non-deletion run exited {proc.returncode}")
if 'deleting ' in proc.stdout:
    test_fail("Erroneous deletions occurred!")

run_rsync('-ai', str(extradir / 'down'), f'{CHKDIR}/')

print("Test merge:")
checkit(['-aiR', deepstr, extrafile_for_rsync, str(TODIR)], CHKDIR, TODIR)

print("Test merge with --del:")
proc = subprocess.run(
    rsync_argv('-aiR', '--del', deepstr, extrafile_for_rsync, str(TODIR)),
    capture_output=True, text=True,
)
OUTFILE.write_text(proc.stdout)
print(proc.stdout, end='')
if proc.returncode != 0:
    test_fail(f"merge --del run exited {proc.returncode}")
if 'deleting ' in proc.stdout:
    test_fail("Erroneous deletions occurred! (2)")
