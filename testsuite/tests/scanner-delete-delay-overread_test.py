#!/usr/bin/python3
# Regression test for run5 0017: read_delay_line() mis-sized a '!'-prefixed
# (DEL_NO_UID_WRITE) delete-delay entry by one byte, over-reading deldelay_buf
# when a buffer-filling entry was the last one.  Drive --delete-delay over many
# non-writable (0444), long-named dest files so the '!' path and a full buffer
# are both exercised; must complete cleanly (RED under ASAN before the fix).
import os
import subprocess
from rsyncfns import FROMDIR, TODIR, makepath, rmtree, rsync_argv, test_fail

src = FROMDIR
dest = TODIR
rmtree(src)
rmtree(dest)
makepath(src)
makepath(dest)

(src / 'keep').write_text('k\n')
# ~80 dest-only files, 0444, ~250-char names -> --delete removes them; the
# read-only files take the DEL_NO_UID_WRITE ('!') prefix under --no-super.
for i in range(80):
    name = f'del{i:02d}-' + 'n' * 240
    p = dest / name
    p.write_text('old\n')
    os.chmod(p, 0o444)

r = subprocess.run(rsync_argv('-a', '--no-super', '--delete-delay', f'{src}/', f'{dest}/'),
                   capture_output=True, text=True)
if r.returncode < 0 or r.returncode >= 128:
    test_fail(f'--delete-delay crashed (rc={r.returncode}): {r.stderr.strip()[:200]}')
left = [q.name for q in dest.iterdir() if q.name.startswith('del')]
if left:
    test_fail(f'--delete-delay did not delete the read-only files: {left[:2]}')
print("scanner-delete-delay-overread: ~80 0444 long-named --delete-delay entries clean")
