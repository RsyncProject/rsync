#!/usr/bin/env python3

import subprocess

from harness.rsync import (
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
    keep = []
    for line in text.splitlines():
        if (line.startswith('created directory ')
                or line.startswith('sent ')
                or line.startswith('total size ')):
            continue
        keep.append(line)
    return '\n'.join(keep) + ('\n' if text.endswith('\n') else '')

copy_proc = _run_capture('-av', f'{FROMDIR}/', f'{CHKDIR}/copy/')
copy_out = _strip_chatter(copy_proc.stdout + copy_proc.stderr)
(TMPDIR / 'copy.out').write_text(copy_out)
print(copy_proc.stdout)

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

proc = subprocess.run(
    rsync_argv('-av', '-f', 'exclude,! */', f'{FROMDIR}/', f'{CHKDIR}/empty/'),
)
if proc.returncode != 0:
    test_fail("setup of chk/empty failed")

checkit(['-avv', '--del', '--remove-source-files', f'{FROMDIR}/', f'{TODIR}/'],
        CHKDIR / 'copy', TODIR)

diff = subprocess.run(['diff', '-r', '-u', str(CHKDIR / 'empty'), str(FROMDIR)])
if diff.returncode != 0:
    test_fail("--remove-source-files did not leave fromdir as just directories")

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
