#!/usr/bin/env python3

import os
import subprocess

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.results import test_fail
from harness.rsync import rsync_argv, run_rsync

metadata(transports={'pipe'})

base = SCRATCHDIR / 'time-only'
src = base / 'source'
basis = base / 'basis'
full_basis = base / 'full-basis'
stamp = 1_700_000_000
content = b'archive source data\n'

rmtree(base)
makepath(src, basis, full_basis)
(src / 'record').write_bytes(content)
(basis / 'record').write_bytes(b'')
(full_basis / 'record').write_bytes(content)
for path in (src / 'record', basis / 'record', full_basis / 'record'):
    os.utime(path, (stamp, stamp))


def destination(name, data=None, mtime=stamp):
    directory = base / name
    directory.mkdir()
    path = directory / 'record'
    if data is not None:
        path.write_bytes(data)
        os.utime(path, (mtime, mtime))
    return directory, path


def transfer(directory, *options):
    run_rsync('-a', '--time-only', *options, f'{src}/', f'{directory}/')


stub_dir, stub = destination('same-time-stub', b'')
transfer(stub_dir)
if stub.read_bytes() != b'' or int(stub.stat().st_mtime) != stamp:
    test_fail('same-time destination stub was replaced')

default_dir, default = destination('default-check', b'')
run_rsync('-a', f'{src}/', f'{default_dir}/')
if default.read_bytes() != content:
    test_fail('default comparison also skipped a same-time zero-byte stub')

window_dir, window = destination('modify-window', b'', stamp + 1)
transfer(window_dir, '--modify-window=2')
if window.read_bytes() != b'':
    test_fail('--modify-window did not apply to --time-only')

direct = base / 'direct-file'
direct.write_bytes(b'')
os.utime(direct, (stamp, stamp))
run_rsync('-t', '--time-only', str(src / 'record'), str(direct))
if direct.read_bytes() != b'':
    test_fail('direct file-to-file transfer replaced a same-time stub')

fresh_dir, fresh = destination('fresh')
transfer(fresh_dir)
if fresh.read_bytes() != content:
    test_fail('missing destination was skipped')

changed_dir, changed = destination('changed-time', b'X' * len(content))
os.utime(changed, (stamp + 60, stamp + 60))
transfer(changed_dir)
if changed.read_bytes() != content:
    test_fail('changed mtime did not trigger a transfer')

for mode in ('link', 'copy', 'compare'):
    target_dir, target = destination(f'{mode}-missing')
    transfer(target_dir, f'--{mode}-dest={basis}')
    if target.read_bytes() != content:
        test_fail(f'--{mode}-dest selected a same-time zero-byte basis')

full_dir, full = destination('full-basis-missing')
transfer(full_dir, f'--link-dest={full_basis}')
if full.read_bytes() != content:
    test_fail('matching full basis was not used')

existing_dir, existing = destination('existing-with-basis', b'')
transfer(existing_dir, f'--link-dest={full_basis}')
if existing.read_bytes() != b'':
    test_fail('--link-dest replaced a same-time existing stub')

for options in (('--time-only', '--size-only'), ('--size-only', '--time-only')):
    target_dir, target = destination(f'combined-{options[0][2:]}', b'')
    run_rsync('-a', *options, f'{src}/', f'{target_dir}/')
    if target.read_bytes() != content:
        test_fail('combined size and time checks ignored a size difference')
    target.write_bytes(b'X' * len(content))
    os.utime(target, (stamp + 60, stamp + 60))
    run_rsync('-a', *options, f'{src}/', f'{target_dir}/')
    if target.read_bytes() != content:
        test_fail('combined size and time checks ignored a time difference')

for flag in ('--checksum', '--ignore-times',
             '--remove-source-files', '--remove-sent-files', '--append', '--append-verify'):
    target_dir, target = destination(f'conflict-{flag[2:]}', b'')
    result = subprocess.run(rsync_argv('-a', '--time-only', flag,
                                       f'{src}/', f'{target_dir}/'),
                            capture_output=True, text=True)
    if result.returncode == 0 or '--time-only cannot be combined' not in result.stderr:
        test_fail(f'{flag} was not refused: {result.returncode}, {result.stderr!r}')
    if target.read_bytes() != b'' or not (src / 'record').exists():
        test_fail(f'{flag} changed a file despite the refusal')

print('time-only destination and alternate-basis rules verified')
