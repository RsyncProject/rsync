#!/usr/bin/env python3
# Regression test for an out-of-bounds write in options.c parse_size_arg().
#
# C99 snprintf() returns the number of bytes it *would* have written, so when
# a daemon client supplies an oversized --max-size / --min-size / --bwlimit /
# --block-size / --max-alloc value (each daemon arg may be up to
# BIGPATHBUFLEN-1 = 5119 bytes via read_args), the `len` at the failure: label
# can far exceed sizeof err_buf (200). The trailing
#     err_buf[len] = '\n'; err_buf[len+1] = '\0';
# then writes 0x0A,0x00 at an attacker-chosen offset thousands of bytes past
# err_buf into adjacent .bss. Reachable from an unauthenticated client against
# any daemon module before chroot/privilege-drop.
#
# The client itself parses --max-size locally and would reject the oversized
# value before connecting, so we forward it to the *daemon's* option parser
# with -M (--remote-option), which the client passes through unparsed.
#
# Oracle:
#   * fixed:   parse_size_arg() clamps `len` to sizeof err_buf - 2 before the
#              trailing writes; the daemon rejects the value with a clean
#              "--max-size=... is invalid" error and stays alive.
#   * pre-fix: the OOB .bss write corrupts adjacent globals. On a normal build
#              this may not fault deterministically, but under ASAN it is a
#              hard global-buffer-overflow abort of the daemon child. This test
#              therefore asserts the GREEN behaviour (clean reject, no crash)
#              on every build and is a true RED detector under the ASAN matrix.

import re
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

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

big = '9' * 5000  # >> sizeof err_buf (200); ~4.9 KB past err_buf if unclamped

proc = subprocess.run(
    rsync_argv('-a', f'-M--max-size={big}', f'{url}mod/', f'{dst}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

# parse_size_arg() builds the "--max-size=<value> is invalid" message in
# err_buf and only THEN does the (pre-fix) out-of-bounds err_buf[len] write,
# before returning -1 to the option-error reporter that forwards err_buf to
# the client. So the daemon echoing the rejected --max-size value back is
# proof it ran parse_size_arg to completion without faulting on the trailing
# write. (On a normal build the OOB write lands silently in .bss, so pre- and
# post-fix look identical here and the test passes either way; under the ASAN
# matrix the pre-fix write is a hard global-buffer-overflow abort that happens
# BEFORE err_buf is reported, so the value never comes back and this assert
# fires -- that is the RED signal.)
out = proc.stdout
if not re.search(r'--max-size=9{100,}', out):
    test_fail(
        "daemon did not echo back the rejected oversized --max-size value "
        f"(rc={proc.returncode}); parse_size_arg likely aborted on the "
        f"unclamped err_buf[len] out-of-bounds write.\noutput:\n{out}")

print("daemon-size-arg-overflow: oversized --max-size rejected by the daemon "
      "after parse_size_arg ran to completion (err_buf length is clamped).")
