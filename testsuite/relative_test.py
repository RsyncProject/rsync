#!/usr/bin/env python3
# Python rewrite of testsuite/relative.test.
#
# Exercise rsync --relative (-R) behaviour: paths anchored at a "./" cut
# point should reproduce that subtree at the destination. We pile on
# hard-link preservation, --del / --del-on-extras and the side-by-side
# combination of an -R-anchored arg with an absolute "extra" path.

import os
import subprocess
import time

from rsyncfns import (
    CHKDIR, FROMDIR, OUTFILE, TMPDIR, TODIR,
    checkit, hands_setup, makepath, rsync_argv,
    run_rsync, test_fail,
)


deepstr = 'down/3/deep'
deepdir = FROMDIR / deepstr
extradir = TMPDIR / 'extra'

makepath(deepdir, extradir / deepstr, CHKDIR)

# Generate the rich source tree underneath the deep nested dir, not under
# fromdir directly. hands_setup reads FROMDIR from the module, so override
# briefly via the rsyncfns module attribute.
import rsyncfns
real_fromdir = rsyncfns.FROMDIR
try:
    rsyncfns.FROMDIR = deepdir
    hands_setup()
finally:
    rsyncfns.FROMDIR = real_fromdir

extrafile = extradir / deepstr / 'extra.added.value'
extrafile.write_text("wowza\n")
# rsync's -R uses "./" as the anchor cut point: anything after it is the
# subtree path to recreate at the destination. Preserve the literal "./"
# in the string we pass to rsync, separately from the Path we use for
# filesystem operations.
extrafile_for_rsync = f"{extradir}/./{deepstr}/extra.added.value"

# Seed extradir with just the directory skeleton of fromdir.
run_rsync('-av', '--existing', '--include=*/', '--exclude=*',
          f'{FROMDIR}/', f'{extradir}/')

os.chdir(FROMDIR)

# chkdir: same shape as a --include=/down/ --exclude=/* sync of fromdir.
run_rsync('-ai', '--include=/down/', '--exclude=/*',
          f'{FROMDIR}/', f'{CHKDIR}/')

time.sleep(1)

print("Test basic relative:")
checkit(['-avR', f'./{deepstr}', str(TODIR)], CHKDIR, TODIR)

# Add a hard link inside the source and the chk dir; mirror it on both
# sides so the --delete pass below doesn't see it as new on either tree.
os.link(deepdir / 'filelist', deepdir / 'dir' / 'filelist')
os.link(CHKDIR / deepstr / 'filelist', CHKDIR / deepstr / 'dir' / 'filelist')
# Re-touch both dirs so the inner-dir time matches.
src_t = (deepdir / 'dir').stat().st_mtime
os.utime(deepdir / 'dir', (src_t, src_t))
os.utime(CHKDIR / deepstr / 'dir', (src_t, src_t))

print("Test hard links:")
checkit(['-avHR', f'./{deepstr}/', str(TODIR)], CHKDIR, TODIR)

# Drop some stray files at the dest then re-sync with --del to confirm
# they're removed.
import shutil
shutil.copy(deepdir / 'text', TODIR / deepstr / 'ThisShouldGo')
shutil.copy(deepdir / 'text', TODIR / deepstr / 'dir' / 'ThisShouldGoToo')

print("Test deletion:")
checkit(['-avHR', '--del', f'./{deepstr}/', str(TODIR)], CHKDIR, TODIR)

print("Test non-deletion:")
# Same as the previous pass but capture output to grep for spurious
# 'deleting ' lines.
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

# Relative with merging.
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
