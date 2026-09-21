#!/usr/bin/env python3
"""KI-17: start_inband_exchange leaks modname on early error returns.

clientserver.c:start_inband_exchange allocates `modname` (new_array) and frees
it only on the success path; the early `return -1` error paths (e.g. the
daemon replying @ERROR for an unknown module) leak it.  Client-side, one-shot
per failed connection.

ASan/LSan reproducer: connect to a daemon requesting a non-existent module so
the @ERROR path is taken, then assert no client leak report names
start_inband_exchange.  Gated on an AddressSanitizer build and --use-tcp.
"""

import glob
import os
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, RSYNC,
    make_tree, require_asan, require_tcp, rmtree, rsync_argv,
    start_test_daemon, test_fail,
)

DAEMON_PORT = 12896
require_tcp("the daemon @ERROR handshake needs a real TCP peer")
require_asan("KI-17 modname leak is only observable under AddressSanitizer/LSan", RSYNC)

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

conf = SCRATCHDIR / 'modname-leak.conf'
conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    f"log file = {SCRATCHDIR}/rsyncd.log\n"
    f"\n[realmod]\n\tpath = {src}\n\tread only = yes\n"
)
url = start_test_daemon(conf, DAEMON_PORT)

asan_log = SCRATCHDIR / 'modname-leak-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
os.environ['ASAN_OPTIONS'] = (
    f"detect_leaks=1:abort_on_error=0:log_path={asan_log}"
)

# Request a module that does not exist: the daemon replies @ERROR and the
# client's start_inband_exchange takes the early `return -1` that leaks modname.
p = subprocess.run(rsync_argv('-r', f'{url}no-such-module/'),
                   capture_output=True, text=True)

# Non-vacuity: the connection must have been refused via the @ERROR path that
# leaks modname (rc != 0 and an "Unknown module" / @ERROR diagnostic).
if p.returncode == 0:
    test_fail("connection to a non-existent module unexpectedly succeeded; "
              "the modname-leak path was not exercised")
if 'ERROR' not in p.stderr and 'nknown module' not in p.stderr:
    test_fail(f"expected an @ERROR/unknown-module rejection; got:\n{p.stderr}")

reports = ''.join(open(r, errors='replace').read()
                  for r in glob.glob(f"{asan_log}.*"))
if 'start_inband_exchange' in reports:
    test_fail("start_inband_exchange leaked modname on the @ERROR path (KI-17):\n"
              + reports[:1500])

print("inband-modname-leak: start_inband_exchange does not leak modname")
