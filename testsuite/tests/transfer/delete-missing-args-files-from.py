#!/usr/bin/env python3

import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail, test_xfail,
)

DAEMON_PORT = 12910

mod = SCRATCHDIR / 'recvmod910'
src = SCRATCHDIR / 'src910'
rmtree(mod)
rmtree(src)
makepath(mod / 'ghostdir', src)
(src / 'keep.txt').write_text("keep-me\n")
(mod / 'keep.txt').write_text("stale\n")
(mod / 'ghost.txt').write_text("delete-me-file\n")
(mod / 'ghostdir' / 'inner').write_text("delete-me-dir\n")

flist = SCRATCHDIR / 'files910.lst'
flist.write_text("keep.txt\nghost.txt\nghostdir\n")

conf = write_daemon_conf([
    ('recv', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-a', '--delete', '--delete-missing-args',
               f'--files-from={flist}', f'{src}/', f'{url}recv/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
print(out)

if 'invalid file mode' in out or (proc.returncode == 2 and (mod / 'ghost.txt').exists()):
    test_xfail(
        '#910: --delete-missing-args with --files-from rejects mode-0 entries')

if proc.returncode != 0:
    test_fail(f"transfer failed unexpectedly (rc={proc.returncode}); "
              f"not the #910 mode-00 symptom:\n{out}")
if (mod / 'ghost.txt').exists():
    test_fail("missing-arg file ghost.txt was not deleted on the receiver")
if (mod / 'ghostdir').exists():
    test_fail("missing-arg directory ghostdir was not deleted on the receiver")
if not (mod / 'keep.txt').is_file() or (mod / 'keep.txt').read_text() != "keep-me\n":
    test_fail("present file keep.txt was not delivered/updated correctly")
