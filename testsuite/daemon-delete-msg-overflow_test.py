#!/usr/bin/env python3
"""A quiet daemon delete must not queue every deleted pathname.

The daemon-side generator cannot always flush multiplexed messages while the
sender is still producing its file list, so send_msg() lets its message buffer
grow.  Sending MSG_DELETED for a quiet transfer made a large recursive delete
grow that buffer without bound even though the client would print none of the
names.  At --max-alloc the allocation error itself was sent through the same
full buffer, recursively exhausting the stack.

Use long paths to reach the 1-MiB minimum --max-alloc with a modest number of
files.  --delete-excluded and --backup-dir mirror the reported daemon failure.
"""

import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    build_rsyncd_conf, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail,
)


DAEMON_PORT = 12969
FILE_COUNT = 1400

src = FROMDIR
dst = TODIR / 'current'
history = dst / 'history'

rmtree(src)
rmtree(TODIR)
makepath(src, dst)
(src / 'keep').write_text('keep\n')
(dst / 'keep').write_text('keep\n')

# Keep the complete path below the most restrictive systems' practical limits.
# The messages total over 1 MiB; more than twice the 512-KiB queue occupancy
# that makes its next doubling hit (and be rejected by) --max-alloc=1m.
deep = dst / 'runs'
for i in range(10):
    deep /= f'd{i}-' + 'x' * 76
makepath(deep)
for i in range(FILE_COUNT):
    (deep / f'stale-{i:04d}').touch()

conf = build_rsyncd_conf()
url = start_test_daemon(conf, DAEMON_PORT)
proc = subprocess.run(
    rsync_argv(
        '-a', '--delete', '--delete-excluded', '--backup',
        '--backup-dir=history', '--exclude=/runs/', '--max-alloc=1m',
        f'{src}/', f'{url}test-to/current/',
    ),
    capture_output=True,
    text=True,
)
out = proc.stdout + proc.stderr
if proc.returncode != 0:
    test_fail(f'quiet daemon delete exited {proc.returncode}:\n{out}')
if out:
    test_fail(f'quiet daemon delete unexpectedly listed deleted names:\n{out}')
if (dst / 'runs').exists():
    test_fail('excluded destination tree was not deleted')

backed_up = history / 'runs'
if len(list(backed_up.rglob('stale-*'))) != FILE_COUNT:
    test_fail('deleted files were not all moved into the backup directory')

# A requested deletion list is still allowed to hit --max-alloc, but reporting
# that error must not recurse through the same full queue until the daemon
# exhausts its stack.  Restore the files and repeat with deletion output on.
backed_up.rename(dst / 'runs')
proc = subprocess.run(
    rsync_argv(
        '-av', '--delete', '--delete-excluded', '--backup',
        '--backup-dir=history', '--exclude=/runs/', '--max-alloc=1m',
        f'{src}/', f'{url}test-to/current/',
    ),
    capture_output=True,
    text=True,
)
out = proc.stdout + proc.stderr
if proc.returncode == 0:
    test_fail('verbose daemon delete unexpectedly stayed below --max-alloc')
if 'Segmentation fault' in out:
    test_fail(f'verbose daemon delete recursively exhausted its stack:\n{out}')

alloc_errors = (SCRATCHDIR / 'rsyncd.log').read_text().count(
    'exceeded --max-alloc='
)
if alloc_errors != 1:
    test_fail(f'expected one daemon --max-alloc error, found {alloc_errors}')

# The daemon's max-verbosity clamp applies after parsing the peer's options.
# It must not erase the fact that the client explicitly requested deletions.
conf_text = conf.read_text()
clamped_conf = conf_text.replace('max verbosity = 4', 'max verbosity = 0')
if clamped_conf == conf_text:
    test_fail('could not lower max verbosity in the daemon configuration')
conf.write_text(clamped_conf)
rmtree(dst)
makepath(dst)
(dst / 'keep').write_text('keep\n')
(dst / 'clamped').write_text('delete me\n')
proc = subprocess.run(
    rsync_argv(
        '-a', '--delete', '--info=del',
        f'{src}/', f'{url}test-to/current/',
    ),
    capture_output=True,
    text=True,
)
out = proc.stdout + proc.stderr
if proc.returncode != 0:
    test_fail(f'max-verbosity daemon delete exited {proc.returncode}:\n{out}')
if 'deleting clamped' not in proc.stdout:
    test_fail(f'max verbosity hid a requested deletion:\n{out}')

print('daemon-delete-msg-overflow: deletion output and allocation failure verified')
