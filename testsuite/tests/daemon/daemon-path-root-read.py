#!/usr/bin/env python3

import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

DAEMON_PORT = 12897

served = SCRATCHDIR / 'served'
dst = SCRATCHDIR / 'pulldst'
rmtree(served)
rmtree(dst)
makepath(served / 'sub')
makepath(dst)
(served / 'README').write_text("readme-contents\n")
(served / 'sub' / 'deep.txt').write_text("deep-contents\n")

conf = write_daemon_conf([
    ('root', {'path': '/', 'read only': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

served_rel = str(served).lstrip('/')
proc = subprocess.run(
    rsync_argv('-a', f'{url}root/{served_rel}/', f'{dst}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
out = proc.stdout or ''
print(out)

if 'Invalid argument (22)' in out or ('failed to open' in out and proc.returncode != 0):
    from harness.rsync import test_xfail
    test_xfail('#897: path=/ daemon failed to open an absolute module-root path')

if proc.returncode != 0:
    test_fail(f"daemon pull failed unexpectedly (rc={proc.returncode}); "
              f"not the #897 EINVAL symptom:\n{out}")
for rel in ('README', 'sub/deep.txt'):
    got = dst / rel
    if not got.is_file():
        test_fail(f"daemon pull did not deliver {rel} (dst={dst})")
    if got.read_text() != (served / rel).read_text():
        test_fail(f"delivered {rel} content differs from source")
