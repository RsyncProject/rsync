#!/usr/bin/env python3

import re
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

DAEMON_PORT = 12939

mod = SCRATCHDIR / 'sizemod'
rmtree(mod)
makepath(mod)
(mod / 'f.txt').write_text("data\n")

conf = write_daemon_conf([
    ('mod', {'path': str(mod), 'read only': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

dst = SCRATCHDIR / 'sizeout'
rmtree(dst)
makepath(dst)

big = '9' * 5000

proc = subprocess.run(
    rsync_argv('-a', f'-M--max-size={big}', f'{url}mod/', f'{dst}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

out = proc.stdout
if not re.search(r'--max-size=9{100,}', out):
    test_fail(
        "daemon did not echo back the rejected oversized --max-size value "
        f"(rc={proc.returncode}); parse_size_arg likely aborted on the "
        f"unclamped err_buf[len] out-of-bounds write.\noutput:\n{out}")

print("daemon-size-arg-overflow: oversized --max-size rejected by the daemon "
      "after parse_size_arg ran to completion (err_buf length is clamped).")
