#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    CHKDIR, FROMDIR, SCRATCHDIR, TMPDIR, TODIR,
    build_rsyncd_conf, checkit, hands_setup, rmtree,
    rsync_argv, run_rsync, start_test_daemon, test_fail, verify_dirs,
)
from harness import metadata

metadata(features={'batch', 'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'compatibility', 'daemon', 'transfer', 'version-mix'})

DAEMON_PORT = 12874

conf = build_rsyncd_conf()
hands_setup()

os.chdir(TMPDIR)

run_rsync('-av', '--exclude=foobar.baz', f'{FROMDIR}/', f'{CHKDIR}/')

run_rsync('-av', '--only-write-batch=BATCH', '--exclude=foobar.baz',
          f'{FROMDIR}/', f'{TODIR}/missing/')
if (TODIR / 'missing').is_dir():
    test_fail("--only-write-batch should not have created destination dir")

print("Test --read-batch (only):")
checkit(['-av', '--read-batch=BATCH', str(TODIR)], CHKDIR, TODIR)

rmtree(TODIR)
for batch in TMPDIR.glob('BATCH*'):
    batch.unlink()

print("Test local --write-batch:")
checkit(['-av', '--write-batch=BATCH', f'{FROMDIR}/', str(TODIR)], FROMDIR, TODIR)

rmtree(TODIR)
print("Test --read-batch:")
checkit(['-av', '--read-batch=BATCH', str(TODIR)], FROMDIR, TODIR)

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
ignore23 = SCRATCHDIR / 'ignore23'
proc = subprocess.run(
    [str(ignore23), *rsync_argv('-av', '--write-batch=BATCH',
                                 f'{FROMDIR}/', f'{url}test-to')],
)
if proc.returncode != 0:
    test_fail(f"daemon recv --write-batch exited {proc.returncode}")
verify_dirs(CHKDIR, TODIR, label="daemon recv --write-batch")
