#!/usr/bin/env python3
"""Exercise filename conversion across remote-shell and daemon transfers."""

import os
import subprocess

from rsyncfns import (
    RSYNC, RSYNC_PEER, SCRATCHDIR,
    makepath, rmtree, rsync_argv, rsync_path_arg, rsh_cmd,
    split_rsync_cmd, start_test_daemon, test_fail, test_skipped,
    write_daemon_conf,
)


UTF8_NAME = b'caf\xc3\xa9.txt'
LATIN1_NAME = b'caf\xe9.txt'
UTF8_TARGET = b'cibl\xc3\xa9.txt'
LATIN1_TARGET = b'cibl\xe9.txt'
PAYLOAD = b'contents are not converted\n\x00\xff'

base = SCRATCHDIR / 'iconv'
rmtree(base)
makepath(base)


def iconv_works(command, label):
    source = base / f'probe-{label}-src'
    destination = base / f'probe-{label}-dst'
    makepath(source, destination)
    (source / 'plain').write_bytes(b'probe\n')
    proc = subprocess.run(
        split_rsync_cmd(command) + [
            '-r', '--iconv=UTF-8,ISO-8859-1',
            f'{source}/', f'{destination}/',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode == 0 and (destination / 'plain').is_file()


def forced_protocol(command):
    for arg in split_rsync_cmd(command):
        if arg.startswith('--protocol='):
            return int(arg.partition('=')[2])
    return None


if not iconv_works(RSYNC, 'client'):
    test_skipped('rsync under test does not provide working --iconv support', capability='iconv')
if RSYNC_PEER != RSYNC and not iconv_works(RSYNC_PEER, 'peer'):
    test_skipped('selected peer does not provide working --iconv support', capability='iconv')


def raw_path(directory, name):
    return os.path.join(os.fsencode(directory), name)


def write_raw(directory, name, contents=PAYLOAD):
    makepath(directory)
    with open(raw_path(directory, name), 'wb') as stream:
        stream.write(contents)


def assert_file(directory, name, label):
    path = raw_path(directory, name)
    if not os.path.isfile(path):
        test_fail(f'{label}: missing filename bytes {name!r}')
    with open(path, 'rb') as stream:
        if stream.read() != PAYLOAD:
            test_fail(f'{label}: file contents changed during filename conversion')


def assert_only_names(directory, expected, label):
    actual = set(os.listdir(os.fsencode(directory)))
    if actual != set(expected):
        test_fail(f'{label}: names {actual!r}, expected {set(expected)!r}')


def invoke(*args, env=None, expected=0):
    proc = subprocess.run(
        rsync_argv(*args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if proc.returncode != expected:
        test_fail(
            f'rsync exited {proc.returncode}, expected {expected} for {args!r}: '
            f'{proc.stderr.decode(errors="replace")}'
        )
    return proc


# A destination charset with single-byte non-ASCII names is required to prove
# that conversion happened. Some filesystems expose filenames only as Unicode.
probe = raw_path(base, b'probe-\xe9')
try:
    fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    preserves_bytes = b'probe-\xe9' in os.listdir(os.fsencode(base))
    os.unlink(probe)
    if not preserves_bytes:
        test_skipped('filesystem does not preserve ISO-8859-1 filename bytes',
                     capability='raw_filename_bytes')
except OSError as exc:
    test_skipped(f'filesystem cannot represent ISO-8859-1 filename bytes: {exc}',
                 capability='raw_filename_bytes')


ssh = rsh_cmd()
rpath = f'--rsync-path={rsync_path_arg()}'
convert = '--iconv=UTF-8,ISO-8859-1'
protocols = [p for p in (forced_protocol(RSYNC), forced_protocol(RSYNC_PEER)) if p]
convert_symlink_target = not protocols or min(protocols) >= 30

# Push and pull must use the same LOCAL,REMOTE charset order. File contents are
# opaque and symlink targets follow the filename conversion.
push_src = base / 'push-src'
push_dst = base / 'push-dst'
pull_dst = base / 'pull-dst'
makepath(push_src, push_dst, pull_dst)
write_raw(push_src, UTF8_NAME)
write_raw(push_src, UTF8_TARGET)
os.symlink(UTF8_TARGET, raw_path(push_src, b'link'))

invoke('-rl', convert, '-e', ssh, rpath,
       f'{push_src}/', f'localhost:{push_dst}/')
assert_only_names(push_dst, (LATIN1_NAME, LATIN1_TARGET, b'link'), 'push')
assert_file(push_dst, LATIN1_NAME, 'push')
push_target = LATIN1_TARGET if convert_symlink_target else UTF8_TARGET
actual_target = os.readlink(raw_path(push_dst, b'link'))
if actual_target != push_target:
    test_fail(f'push: symlink target {actual_target!r}, expected {push_target!r}')

invoke('-rl', convert, '-e', ssh, rpath,
       f'localhost:{push_dst}/', f'{pull_dst}/')
assert_only_names(pull_dst, (UTF8_NAME, UTF8_TARGET, b'link'), 'pull')
assert_file(pull_dst, UTF8_NAME, 'pull')
if os.readlink(raw_path(pull_dst, b'link')) != UTF8_TARGET:
    test_fail('pull: symlink target was not converted to UTF-8')

# A local files-from list names the local source in its charset. A remote list
# names the remote source in the remote charset and is converted with -s.
files_push = base / 'files-push'
files_pull = base / 'files-pull'
makepath(files_push, files_pull)
local_list = base / 'local-list'
local_list.write_bytes(UTF8_NAME + b'\n')
invoke('-r', '-s', convert, '-e', ssh, rpath,
       f'--files-from={local_list}', f'{push_src}/',
       f'localhost:{files_push}/')
assert_only_names(files_push, (LATIN1_NAME,), 'local --files-from')
assert_file(files_push, LATIN1_NAME, 'local --files-from')

remote_list = base / 'remote-list'
remote_list.write_bytes(LATIN1_NAME + b'\n')
invoke('-r', '-s', convert, '-e', ssh, rpath,
       f'--files-from=localhost:{remote_list}',
       f'localhost:{push_dst}/', f'{files_pull}/')
assert_only_names(files_pull, (UTF8_NAME,), 'remote --files-from')
assert_file(files_pull, UTF8_NAME, 'remote --files-from')

# With secluded args the requested remote source name is converted before the
# peer interprets it.
arg_pull = base / 'arg-pull'
makepath(arg_pull)
remote_name = os.fsdecode(UTF8_NAME)
invoke('-s', convert, '-e', ssh, rpath,
       f'localhost:{push_dst}/{remote_name}', f'{arg_pull}/')
assert_only_names(arg_pull, (UTF8_NAME,), '--secluded-args')
assert_file(arg_pull, UTF8_NAME, '--secluded-args')

# An invalid UTF-8 source name is omitted and reported. The resulting I/O error
# blocks deletion unless --ignore-errors explicitly permits it.
invalid_src = base / 'invalid-src'
invalid_dst = base / 'invalid-dst'
makepath(invalid_src, invalid_dst)
write_raw(invalid_src, b'bad-\xff')
(invalid_src / 'sentinel').write_bytes(b'keep\n')
(invalid_dst / 'sentinel').write_bytes(b'keep\n')
(invalid_dst / 'stale').write_bytes(b'do not delete yet\n')
proc = invoke('-r', '--delete', convert, '-e', ssh, rpath,
              f'{invalid_src}/', f'localhost:{invalid_dst}/', expected=23)
if b'cannot convert filename' not in proc.stderr:
    test_fail('invalid UTF-8 filename did not report a conversion failure')
assert_only_names(invalid_dst, (b'sentinel', b'stale'), 'I/O-safe deletion')
if (invalid_dst / 'sentinel').read_bytes() != b'keep\n':
    test_fail('invalid filename transfer changed the existing destination')

invoke('-r', '--delete', '--ignore-errors', convert, '-e', ssh, rpath,
       f'{invalid_src}/', f'localhost:{invalid_dst}/', expected=23)
assert_only_names(invalid_dst, (b'sentinel',), '--ignore-errors')
if (invalid_dst / 'sentinel').read_bytes() != b'keep\n':
    test_fail('--ignore-errors changed the retained payload')

# An explicit --no-iconv overrides the environment default on both sides.
noiconv_dst = base / 'noiconv-dst'
makepath(noiconv_dst)
env = os.environ.copy()
env['RSYNC_ICONV'] = 'UTF-8,ISO-8859-1'
invoke('-rl', '--no-iconv', '-e', ssh, rpath,
       f'{push_src}/', f'localhost:{noiconv_dst}/', env=env)
assert_only_names(noiconv_dst, (UTF8_NAME, UTF8_TARGET, b'link'), '--no-iconv')
assert_file(noiconv_dst, UTF8_NAME, '--no-iconv')
if os.readlink(raw_path(noiconv_dst, b'link')) != UTF8_TARGET:
    test_fail('--no-iconv converted the symlink target from the environment default')

# A daemon module's charset overrides the remote charset supplied by the
# client. Supplying only the local charset exercises that daemon contract.
daemon_dst = base / 'daemon-dst'
daemon_ignore_dst = base / 'daemon-ignore-dst'
makepath(daemon_dst, daemon_ignore_dst)
(daemon_ignore_dst / 'sentinel').write_bytes(b'keep\n')
(daemon_ignore_dst / 'stale').write_bytes(b'delete through module policy\n')
conf = write_daemon_conf([
    ('encoded', {
        'path': str(daemon_dst),
        'read only': 'no',
        'charset': 'ISO-8859-1',
        # Isolate charset conversion from the daemon's symlink munging.
        'munge symlinks': 'no',
    }),
    ('encoded-ignore', {
        'path': str(daemon_ignore_dst),
        'read only': 'no',
        'charset': 'ISO-8859-1',
        'ignore errors': 'yes',
    }),
], name='iconv-rsyncd.conf')
url = start_test_daemon(conf, 18732)
invoke('-rl', '--iconv=UTF-8', f'{push_src}/', f'{url}encoded/')
assert_only_names(daemon_dst, (LATIN1_NAME, LATIN1_TARGET, b'link'),
                  'daemon charset')
assert_file(daemon_dst, LATIN1_NAME, 'daemon charset')
daemon_target = LATIN1_TARGET if convert_symlink_target else UTF8_TARGET
actual_target = os.readlink(raw_path(daemon_dst, b'link'))
if actual_target != daemon_target:
    test_fail(f'daemon: symlink target {actual_target!r}, expected {daemon_target!r}')

invoke('-r', '--delete', '--iconv=UTF-8', f'{invalid_src}/',
       f'{url}encoded-ignore/', expected=23)
assert_only_names(daemon_ignore_dst, (b'sentinel',), 'daemon ignore errors')
if (daemon_ignore_dst / 'sentinel').read_bytes() != b'keep\n':
    test_fail('daemon ignore errors changed the retained payload')

print('iconv: names, arguments, file lists, symlinks, error deletion and daemon charset')
