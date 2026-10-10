#!/usr/bin/env python3

import re
import shutil
import subprocess

from harness.rsync import (
    CHKDIR, FROMDIR, OUTFILE, SRCDIR, TMPDIR, TODIR,
    checkit, cp_touch, makepath, rsync_argv, test_fail, verify_dirs,
)

bakdir = TMPDIR / 'bak'

makepath(FROMDIR / 'deep', bakdir / 'dname')
name1 = FROMDIR / 'deep' / 'name1'
name2 = FROMDIR / 'deep' / 'name2'

def _cat_glob(pattern: str, dest):
    chunks = bytearray()
    for f in sorted(SRCDIR.glob(pattern)):
        chunks.extend(f.read_bytes())
    dest.write_bytes(bytes(chunks))

_cat_glob('[gr]*.[ch]', name1)
_cat_glob('[et]*.[ch]', name2)

checkit(['-ai', '--info=backup', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
checkit(['-ai', '--info=backup', f'{FROMDIR}/', f'{CHKDIR}/'], FROMDIR, CHKDIR)

_cat_glob('[fgpr]*.[ch]', name1)
_cat_glob('[etw]*.[ch]', name2)

def _run_and_capture(args, outfile):
    proc = subprocess.run(rsync_argv(*args), capture_output=True, text=True)
    outfile.write_text(proc.stdout)
    print(proc.stdout, end='')
    if proc.returncode != 0:
        test_fail(f"rsync exited {proc.returncode}")
    return proc

_run_and_capture(
    ['-ai', '--info=backup', '--no-whole-file', '--backup',
     f'{FROMDIR}/', f'{TODIR}/'],
    OUTFILE,
)
text = OUTFILE.read_text()
for fn in ('deep/name1', 'deep/name2'):
    if f"backed up {fn} to {fn}~" not in text:
        test_fail(f"no backup message output for {fn}")
    diff = subprocess.run(['diff', '-u', str(FROMDIR / fn), str(TODIR / fn)])
    if diff.returncode != 0:
        test_fail(f"copy of {fn} failed")
    diff = subprocess.run(['diff', '-u', str(CHKDIR / fn), str(TODIR / f'{fn}~')])
    if diff.returncode != 0:
        test_fail(f"backup of {fn} to {fn}~ failed")
    shutil.move(str(TODIR / f'{fn}~'), str(TODIR / fn))

(TODIR / 'dname').write_text("deleted-file\n")
cp_touch(TODIR / 'dname', CHKDIR)

_run_and_capture(
    ['-ai', '--info=backup', '--no-whole-file', '--delete-delay',
     '--backup', f'--backup-dir={bakdir}',
     f'{FROMDIR}/', f'{TODIR}/'],
    OUTFILE,
)
verify_dirs(FROMDIR, TODIR, label="post --backup-dir run")

text = OUTFILE.read_text()
for fn in ('deep/name1', 'deep/name2'):
    if not re.search(rf"backed up {re.escape(fn)} to .*/{re.escape(fn)}$",
                     text, flags=re.MULTILINE):
        test_fail(f"no backup message output for {fn}")
diff = subprocess.run(['diff', '-r', '-u', str(CHKDIR), str(bakdir)])
if diff.returncode != 0:
    test_fail("backup dir contents are bogus")
(bakdir / 'dname').unlink()

checkit(['-ai', '--info=backup', '--del', f'{FROMDIR}/', f'{CHKDIR}/'],
        FROMDIR, CHKDIR)
_cat_glob('[efgr]*.[ch]', name1)
_cat_glob('[ew]*.[ch]', name2)

_run_and_capture(
    ['-ai', '--info=backup', '--inplace', '--no-whole-file',
     '--backup', f'--backup-dir={bakdir}',
     f'{FROMDIR}/', f'{TODIR}/'],
    OUTFILE,
)
verify_dirs(FROMDIR, TODIR, label="post --inplace --backup-dir run")

text = OUTFILE.read_text()
for fn in ('deep/name1', 'deep/name2'):
    if not re.search(rf"backed up {re.escape(fn)} to .*/{re.escape(fn)}$",
                     text, flags=re.MULTILINE):
        test_fail(f"no backup message output for {fn}")
diff = subprocess.run(['diff', '-r', '-u', str(CHKDIR), str(bakdir)])
if diff.returncode != 0:
    test_fail("backup dir contents are bogus")

checkit(['-ai', '--info=backup', '--inplace', '--no-whole-file',
         f'{FROMDIR}/', f'{bakdir}/'], FROMDIR, bakdir)
