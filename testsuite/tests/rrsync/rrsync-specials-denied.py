#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.rsync import (
    RSYNC, RSYNC_PREFIX, SCRATCHDIR, patched_rrsync, rmtree, rsync_argv,
    test_fail, xattr_set, xattrs_supported, rsync_path_arg,
)

base = SCRATCHDIR / 'rrsync-specials'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
os.mkfifo(src / 'evil_fifo')
(src / 'ordinary').write_text('PLAIN\n')

withd = base / 'with'
withd.mkdir()
subprocess.run(rsync_argv('-rlptgo', '--specials', f'{src}/', f'{withd}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not (withd / 'evil_fifo').is_fifo():
    test_fail("premise check failed: --specials should create the FIFO in the dest")
without = base / 'without'
without.mkdir()
subprocess.run(rsync_argv('-rlptgo', f'{src}/', f'{without}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if (without / 'evil_fifo').exists():
    test_fail("premise check failed: without --specials the FIFO should be skipped")

record = base / 'argv'
stub = base / 'rsync-record'
stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >' + str(record) + '\n')
stub.chmod(0o755)
rrsync = patched_rrsync(base, rsync_path=str(stub))
restricted = base / 'restricted'
restricted.mkdir(exist_ok=True)

def forwarded_opts(cmd):
    if record.exists():
        record.unlink()
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': cmd}
    r = subprocess.run([str(rrsync), str(restricted)], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"restricted rrsync rejected `{cmd}` instead of stripping "
                  f"the option (exit {r.returncode}): {r.stderr.strip()}")
    return record.read_text().split('\n') if record.exists() else []

for opt in ('-D', '--specials'):
    forwarded = forwarded_opts(f'rsync --server {opt} . .')
    if '--drop-D' not in forwarded:
        test_fail(f"restricted rrsync did not force --drop-D for `{opt}` on "
                  f"the receiving side, where creation happens: {forwarded}")
    if opt not in forwarded:
        test_fail(f"restricted rrsync dropped `{opt}` instead of leaving it "
                  f"alongside --drop-D: {forwarded}")
    if '--no-D' in forwarded:
        test_fail(f"restricted rrsync forced --no-D for `{opt}`.  That clears "
                  'preserve_devices/preserve_specials, which also frame the '
                  f'rdev fields, so the file list desynchronises: {forwarded}')

served = base / 'served'
served.mkdir()
pushwrap = base / 'pushwrap'
pushwrap.mkdir()
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
rrsync_push = patched_rrsync(pushwrap, rsync_path=str(shim))
rsh = base / 'fake-rsh'
rsh.write_text(
    '#!/bin/sh\n'
    'shift\n'
    'SSH_ORIGINAL_COMMAND="$*"\n'
    'export SSH_ORIGINAL_COMMAND\n'
    'exec %s %s\n' % (shlex.quote(str(rrsync_push)), shlex.quote(str(served))))
rsh.chmod(0o755)

proc = subprocess.run(
    rsync_argv('-rlptgoD', '--specials', '-e', str(rsh), f'{src}/', 'dummy:.'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
landed = served / 'ordinary'
if not landed.is_file() or landed.read_text() != 'PLAIN\n':
    test_fail(f'control failed: the push did not deliver an ordinary file, so '
              f'the absence of the FIFO proves nothing ({ctx})')
if (served / 'evil_fifo').exists():
    test_fail(f'a push through a restricted rrsync created a special file in '
              f'the served tree ({ctx})')
if proc.returncode != 0:
    test_fail(f'the FIFO was denied, but the transfer itself failed ({ctx})')

if xattrs_supported():
    devsrc = base / 'devsrc'
    devsrc.mkdir()
    (devsrc / 'ordinary').write_text('PLAIN\n')
    (devsrc / 'achar').write_bytes(b'')
    try:
        xattr_set(f'{RSYNC_PREFIX}.%stat', '20644 1,3 0:0', devsrc / 'achar')
        made_dev = True
    except OSError:
        made_dev = False

    if made_dev:
        devserved = base / 'devserved'
        devserved.mkdir()
        devwrap = base / 'devwrap'
        devwrap.mkdir()
        devshim = base / 'rsync-shim-fake'
        devshim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' --fake-super "$@"\n')
        devshim.chmod(0o755)
        rrsync_dev = patched_rrsync(devwrap, rsync_path=str(devshim))
        devrsh = base / 'fake-rsh-dev'
        devrsh.write_text(
            '#!/bin/sh\n'
            'shift\n'
            'SSH_ORIGINAL_COMMAND="$*"\n'
            'export SSH_ORIGINAL_COMMAND\n'
            'exec %s %s\n' % (shlex.quote(str(rrsync_dev)),
                               shlex.quote(str(devserved))))
        devrsh.chmod(0o755)
        proc = subprocess.run(
            rsync_argv('--fake-super', '-rlptgoD', '--specials', '-e',
                       str(devrsh), f'{devsrc}/', 'dummy:.'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=120)
        ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
        landed = devserved / 'ordinary'
        if not landed.is_file() or landed.read_text() != 'PLAIN\n':
            test_fail(f'device control did not deliver the adjacent regular file ({ctx})')
        if os.path.lexists(devserved / 'achar'):
            test_fail(f'a push through a restricted rrsync recorded a device in '
                      f'the served tree ({ctx})')
        if proc.returncode != 0:
            test_fail(f'the device was denied, but the transfer itself failed '
                      f'({ctx})')

for opt in ('-D', '--specials'):
    forwarded = forwarded_opts(f'rsync --server --sender {opt} . .')
    for forced in ('--drop-D', '--no-D'):
        if forced in forwarded:
            test_fail(f"restricted rrsync forced {forced} for `{opt}` on the "
                      'SENDING side, where there is nothing to deny: a sender '
                      'creates no device or special entry in the served tree '
                      f'({forwarded})')

print('rrsync blocks receiver-side devices and special files without breaking the transfer')
