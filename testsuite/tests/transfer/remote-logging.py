#!/usr/bin/env python3

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_path_arg, rsh_cmd, run_rsync, test_fail,
)

base = SCRATCHDIR / 'remote-logging'
src = base / 'src'
local_dst = base / 'local-dst'
push_dst = base / 'push-dst'
pull_dst = base / 'pull-dst'
rmtree(base)
makepath(src, local_dst, push_dst, pull_dst)
(src / 'payload').write_text('payload\n')

ssh = rsh_cmd()
rpath = f'--rsync-path={rsync_path_arg()}'

def assert_log(path, marker, label):
    if not path.is_file():
        test_fail(f'{label}: log file was not created')
    text = path.read_text()
    if f'{marker}:payload' not in text:
        test_fail(f'{label}: formatted payload entry missing from {text!r}')

def assert_payload(directory, label):
    path = directory / 'payload'
    if not path.is_file() or path.read_text() != 'payload\n':
        test_fail(f'{label}: payload was not transferred')

local_log = base / 'local.log'
run_rsync('-r', f'--log-file={local_log}', '--log-file-format=LOCAL:%n',
          f'{src}/', f'{local_dst}/')
assert_log(local_log, 'LOCAL', 'client log')
assert_payload(local_dst, 'client log')

push_log = base / 'push-remote.log'
run_rsync('-r', '-e', ssh, rpath,
          f'--remote-option=--log-file={push_log}',
          '--remote-option=--log-file-format=PUSH:%n',
          f'{src}/', f'localhost:{push_dst}/')
assert_log(push_log, 'PUSH', 'remote receiver log')
assert_payload(push_dst, 'remote receiver log')

pull_log = base / 'pull-remote.log'
run_rsync('-r', '-e', ssh, rpath,
          f'-M--log-file={pull_log}', f'-M--log-file-format=PULL:%n',
          f'localhost:{src}/', f'{pull_dst}/')
assert_log(pull_log, 'PULL', 'remote sender log')
assert_payload(pull_dst, 'remote sender log')

print('remote-logging: client, remote receiver and remote sender formats verified')
