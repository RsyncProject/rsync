#!/usr/bin/env python3
# Regression test for a 1-byte heap overflow in add_implied_include()
# (exclude.c). Phase 1 classifies a '\' via !strchr("*[?", cp[1]); strchr()
# returns the literal's NUL terminator when cp[1] == '\0', so a TRAILING
# backslash takes neither branch -- it is copied into new_pat but backslash_cnt
# is not incremented. Phase 2's recurse/xfer_dirs '/**' rule allocates
# arg_len + backslash_cnt + 3 + 1 and doubles every '\' in new_pat (including the
# uncounted trailing one), so the closing '*p = '\0'' lands one byte past the
# heap buffer.
#
# Reachable on a standard network 'rsync --daemon' (the per-module
# parse_arguments() runs with am_server == 0, so trust_sender_args stays 0 and
# add_implied_include is active) by a remote unauthenticated client that sends
# '-r --files-from=<file>' with a files-from name carrying both an interior and a
# trailing backslash ('a\b\'): forward_filesfrom_data() feeds it to
# add_implied_include() on the daemon. The fix counts the trailing backslash.
#
# Oracle: pre-fix -> the daemon hits an ASan heap-buffer-overflow report; fixed
# -> none. Needs an ASan build + a real TCP daemon, and is skipped otherwise.

import glob
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_asan, require_tcp, rsync_argv,
    start_rsyncd, test_fail,
)

PORT = 12954
require_tcp("a real network rsync daemon is needed (am_server==0 per-module); "
            "run with --use-tcp")
require_asan("the 1-byte heap overflow in add_implied_include is only observable "
            "under AddressSanitizer")
claim_ports(PORT)

base = SCRATCHDIR / 'implied-bslash'
mod = base / 'mod'
dest = base / 'dest'
makepath(mod, dest)
(mod / 'file.txt').write_text("hello\n")

# A files-from entry with an interior AND a trailing backslash.
ff = base / 'files-from.txt'
ff.write_text('a\\b\\\n')

conf = base / 'implied.conf'
conf.write_text(f"""\
pid file = {SCRATCHDIR}/implied-rsyncd.pid
use chroot = no

[mod]
    path = {mod}
    read only = yes
""")

asan_log = base / 'implied-asan'
for stale in glob.glob(f"{asan_log}.*"):
    os.unlink(stale)
prev = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (
    (prev + ':' if prev else '') + f'detect_leaks=0:abort_on_error=1:log_path={asan_log}')

start_rsyncd(conf, PORT)

# A real client forwards the files-from names to the daemon, which parses each
# through add_implied_include() (recurse via -r so the vulnerable '/**' path runs).
proc = subprocess.run(
    rsync_argv('-r', f'--files-from={ff}', f'rsync://127.0.0.1:{PORT}/mod/',
               str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)


def asan_report():
    text = ''.join(open(r, errors='replace').read()
                   for r in glob.glob(f"{asan_log}.*"))
    return text if 'AddressSanitizer' in text else (
        proc.stdout if 'AddressSanitizer' in (proc.stdout or '') else '')


# The overflowing process (the forwarding client child) may flush its report a
# beat after the client exits, so poll briefly.
import time
report = ''
for _ in range(30):
    report = asan_report()
    if report:
        break
    time.sleep(0.1)

if report:
    test_fail(
        "add_implied_include() overflowed its heap buffer on a files-from name "
        "with a trailing backslash -- the trailing '\\' was doubled but not "
        "counted in the allocation:\n" + report[:1500])

print("exclude-implied-trailing-backslash: a trailing backslash in a files-from "
      "name is counted; no heap overflow in add_implied_include.")
