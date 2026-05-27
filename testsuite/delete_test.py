#!/usr/bin/env python3
# Python rewrite of testsuite/delete.test.
#
# Exercises three independent delete-handling behaviours:
#   1. --del dry-run output matches a real copy's output (sans the trivial
#      "created directory" / "sent" / "total size" lines).
#   2. --del --remove-source-files leaves the source empty (only dirs) and
#      the destination matching what a plain copy would have produced.
#   3. per-directory filter file with "P" (protect) keeps a file alive across
#      --delete-excluded; "-" (exclude) does NOT.

import os
import shutil
import subprocess

from rsyncfns import (
    CHKDIR, FROMDIR, TMPDIR, TODIR,
    checkit, hands_setup, makepath, rsync_argv, test_fail,
)


hands_setup()
makepath(CHKDIR, TODIR / 'extradir', TODIR / 'emptydir' / 'subdir')

(TODIR / 'remove1').write_text("extra\n")
(TODIR / 'remove2').write_text("extra\n")
(TODIR / 'extradir' / 'remove3').write_text("extra\n")
(TODIR / 'emptydir' / 'subdir' / 'remove4').write_text("extra\n")


def _run_capture(*args):
    proc = subprocess.run(rsync_argv(*args), capture_output=True, text=True)
    return proc


def _strip_chatter(text: str) -> str:
    """Remove the lines the shell test stripped via grep -E -v."""
    keep = []
    for line in text.splitlines():
        if (line.startswith('created directory ')
                or line.startswith('sent ')
                or line.startswith('total size ')):
            continue
        keep.append(line)
    return '\n'.join(keep) + ('\n' if text.endswith('\n') else '')


# Two chkdirs: copy/ has what a normal copy looks like, empty/ has just
# directories (used as a remove-source-files comparator).
copy_proc = _run_capture('-av', f'{FROMDIR}/', f'{CHKDIR}/copy/')
copy_out = _strip_chatter(copy_proc.stdout + copy_proc.stderr)
(TMPDIR / 'copy.out').write_text(copy_out)
print(copy_proc.stdout)

# --del dry-run output (status may be 0 or 23 from delete behaviour; ignore
# return code as the shell test does).
copy2_proc = _run_capture('-avn', '--del', f'{FROMDIR}/', f'{CHKDIR}/copy2/')
copy2_out = _strip_chatter(copy2_proc.stdout + copy2_proc.stderr)
(TMPDIR / 'copy2.out').write_text(copy2_out)
print(copy2_proc.stdout)

if copy_out != copy2_out:
    diff = subprocess.run(
        ['diff', '-u', str(TMPDIR / 'copy.out'), str(TMPDIR / 'copy2.out')],
        capture_output=True, text=True,
    )
    sys_stdout = diff.stdout
    print(sys_stdout)
    test_fail("--del dry-run output diverged from a plain copy's output")

# Build chk/empty as a directories-only mirror of fromdir.
proc = subprocess.run(
    rsync_argv('-av', '-f', 'exclude,! */', f'{FROMDIR}/', f'{CHKDIR}/empty/'),
)
if proc.returncode != 0:
    test_fail("setup of chk/empty failed")

# Main: --del + --remove-source-files leaves dirs only in fromdir, and
# destination matches a normal copy.
checkit(['-avv', '--del', '--remove-source-files', f'{FROMDIR}/', f'{TODIR}/'],
        CHKDIR / 'copy', TODIR)

diff = subprocess.run(['diff', '-r', '-u', str(CHKDIR / 'empty'), str(FROMDIR)])
if diff.returncode != 0:
    test_fail("--remove-source-files did not leave fromdir as just directories")


# Per-directory filter file: "P" protects, "-" excludes.
(TODIR / 'filters').write_text("P foo\n- bar\n")
for name in ('foo', 'bar', 'baz'):
    (TODIR / name).touch()

proc = subprocess.run(
    rsync_argv('-r', '--exclude=baz', '--filter=: filters', '--delete-excluded',
               f'{FROMDIR}/', f'{TODIR}/'),
)
if proc.returncode != 0:
    test_fail(f"filter-file run exited {proc.returncode}")

if not (TODIR / 'foo').is_file():
    test_fail(f"rsync should NOT have deleted {TODIR / 'foo'}")
if (TODIR / 'bar').is_file():
    test_fail(f"rsync SHOULD have deleted {TODIR / 'bar'}")
if (TODIR / 'baz').is_file():
    test_fail(f"rsync SHOULD have deleted {TODIR / 'baz'}")
