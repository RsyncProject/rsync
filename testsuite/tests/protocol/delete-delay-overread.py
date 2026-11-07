#!/usr/bin/python3

import os
import subprocess
from harness.rsync import FROMDIR, TODIR, makepath, rmtree, rsync_argv, test_fail

src = FROMDIR
dest = TODIR
rmtree(src)
rmtree(dest)
makepath(src)
makepath(dest)

(src / 'keep').write_text('k\n')
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
print('delete-delay bounds verified')
