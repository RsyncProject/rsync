#!/usr/bin/env python3

import os
import subprocess
import sys

from fixtures.rrsync import rrsync_shell, rsync_wrapper, patched_rrsync
from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv
from harness.xattrs import RSYNC_PREFIX, set_value as xattr_set, is_supported as xattrs_supported
from harness import metadata

metadata(transports={'pipe'})

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
stub.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
                f'Path({str(record)!r}).write_text("".join(arg + "\\n" for arg in sys.argv[1:]))\n')
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
rsync_wrapper(shim)
rrsync_push = patched_rrsync(pushwrap, rsync_path=str(shim))
rsh = base / 'fake-rsh'
rrsync_shell(rsh, rrsync_push, served)

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
        rsync_wrapper(devshim, '--fake-super')
        rrsync_dev = patched_rrsync(devwrap, rsync_path=str(devshim))
        devrsh = base / 'fake-rsh-dev'
        rrsync_shell(devrsh, rrsync_dev, devserved)
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
