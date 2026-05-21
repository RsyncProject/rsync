#!/usr/bin/env python3
# Python rewrite of testsuite/batch-mode.test.
#
# Test rsync's --write-batch / --read-batch / --only-write-batch flags,
# both for local transfers and for daemon source/destination.

import os
import shutil
import subprocess

from rsyncfns import (
    CHKDIR, FROMDIR, SCRATCHDIR, TMPDIR, TODIR,
    build_rsyncd_conf, checkit, hands_setup, rmtree,
    run_rsync, start_test_daemon, test_fail,
)


DAEMON_PORT = 12874

conf = build_rsyncd_conf()
hands_setup()

os.chdir(TMPDIR)

# chkdir mirrors a normal transfer minus the daemon's foobar.baz exclude.
run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')

# --only-write-batch must NOT create the destination directory.
run_rsync('-av', '--only-write-batch=BATCH', '--exclude=foobar.baz',
          f'{FROMDIR}/', f'{TODIR}/missing/')
if (TODIR / 'missing').is_dir():
    test_fail("--only-write-batch should not have created destination dir")

print("Test --read-batch (only):")
checkit(['-av', '--read-batch=BATCH', str(TODIR)], CHKDIR, TODIR)

# Wipe any leftover BATCH* files so the next pass starts clean.
rmtree(TODIR)
for batch in TMPDIR.glob('BATCH*'):
    batch.unlink()

print("Test local --write-batch:")
checkit(['-av', '--write-batch=BATCH', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

rmtree(TODIR)
print("Test --read-batch:")
checkit(['-av', '--read-batch=BATCH', str(TODIR)], FROMDIR, TODIR)

# Daemon variants: pipe transport by default, real loopback rsyncd under
# --use-tcp.
url = start_test_daemon(conf, DAEMON_PORT)

rmtree(TODIR)
print("Test daemon sender --write-batch:")
checkit(['-av', '--write-batch=BATCH',
         f'{url}test-from/', str(TODIR)],
        CHKDIR, TODIR, allowed_codes=(0, 23))

rmtree(TODIR)
print("Test --read-batch from daemon:")
checkit(['-av', '--read-batch=BATCH', str(TODIR)], CHKDIR, TODIR)

rmtree(TODIR)
print("Test BATCH.sh use of --read-batch:")
# BATCH.sh is the auto-generated wrapper script that re-applies the
# batch -- we run it directly, not via the rsync binary, then verify.
from rsyncfns import verify_dirs
proc = subprocess.run(['sh', './BATCH.sh'])
if proc.returncode != 0:
    test_fail(f"BATCH.sh exited {proc.returncode}")
verify_dirs(CHKDIR, TODIR, label="BATCH.sh use of --read-batch")

print("Test do-nothing re-run of batch:")
proc = subprocess.run(['sh', './BATCH.sh'])
if proc.returncode != 0:
    test_fail(f"BATCH.sh (re-run) exited {proc.returncode}")
verify_dirs(CHKDIR, TODIR, label="do-nothing re-run of batch")

rmtree(TODIR)
TODIR.mkdir()
print("Test daemon recv --write-batch:")
# ignore23 swallows the partial-transfer code 23 that daemon mode sometimes
# emits even on success.
ignore23 = SCRATCHDIR / 'ignore23'
# We pass ignore23 by inserting it ahead of the rsync invocation. checkit
# calls subprocess.run(rsync_argv(...)) directly, so do the run manually
# and call verify_dirs for the comparison.
from rsyncfns import rsync_argv
proc = subprocess.run(
    [str(ignore23), *rsync_argv('-av', '--write-batch=BATCH',
                                 f'{FROMDIR}/', f'{url}test-to')],
)
if proc.returncode != 0:
    test_fail(f"daemon recv --write-batch exited {proc.returncode}")
verify_dirs(CHKDIR, TODIR, label="daemon recv --write-batch")
