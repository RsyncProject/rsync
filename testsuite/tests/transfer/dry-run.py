#!/usr/bin/env python3

import subprocess

from harness.rsync import FROMDIR, RSYNC, TMPDIR, TODIR, makepath, rsync_argv, test_fail

makepath(FROMDIR / 'subdir', TODIR)
(FROMDIR / 'subdir' / 'file').write_text("data\n")
(TODIR / 'other').write_text("data\n")

def run_capture(*args):
    proc = subprocess.run(rsync_argv(*args), capture_output=True, text=True)
    print(proc.stdout, end='')
    print(proc.stderr, end='')
    return proc

out_path = TMPDIR / 'out1'
proc = run_capture('-n', '-r', '--ignore-non-existing', '-vv',
                   f'{FROMDIR}/', f'{TODIR}/')
if proc.returncode != 0:
    test_fail(f"test 1 failed: dry-run errored (rc={proc.returncode})")
out_path.write_text(proc.stdout)
for line in proc.stdout.splitlines():
    if 'not creating new' in line and 'subdir/file' in line:
        test_fail("test 1 failed: dry-run announced creating subdir/file")

if 'protocol=29' not in RSYNC:
    proc = run_capture('-n', '-r', '-R', '--no-implied-dirs', '-y',
                       f'{FROMDIR}/./subdir/file', f'{TODIR}/')
    if proc.returncode != 0:
        test_fail("test 2 failed: --no-implied-dirs dry-run errored")
else:
    print("Skipped test 2 for protocol 29.")

proc = run_capture('-n', '-r', '--delete-after', '-i',
                   f'{FROMDIR}/', f'{TODIR}/')
saw_delete = any(line.lstrip().startswith('*deleting')
                 and 'other' in line
                 for line in proc.stdout.splitlines())
if not saw_delete:
    test_fail("test 3 failed: no '*deleting other' line in dry-run output")
