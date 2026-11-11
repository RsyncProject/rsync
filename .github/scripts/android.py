import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def run(*command, timeout=30, **kwargs):
    kwargs.setdefault('check', True)
    kwargs.setdefault('text', True)
    return subprocess.run(command, timeout=timeout, **kwargs)


def check_transfer(command, binary, work, executor=(), hardlinks=True, write_file=None, read_file=None):
    source, destination = f'{work}/source', f'{work}/destination'
    data_path, delta_path = f'{source}/dir/data', f'{source}/dir/delta'
    data = b'initial data\n'
    delta = b''.join(f'{i:08d}:0123456789abcdef0123456789abcdef\n'.encode() for i in range(8192))

    if write_file is None:
        def write_file(path, data):
            command('tee', path, input=data, text=False, stdout=subprocess.DEVNULL)
    if read_file is None:
        def read_file(path):
            return command('cat', path, capture_output=True, text=False).stdout

    def stat(path, field):
        return command('stat', '-c', field, path, capture_output=True).stdout.strip()

    command('mkdir', '-p', f'{source}/dir', destination)
    write_file(data_path, data)
    write_file(delta_path, delta)
    command('chmod', '640', data_path)
    linked = command('ln', data_path, f'{source}/dir/hard-link', capture_output=True, check=False)
    if hardlinks:
        linked.check_returncode()
    elif linked.returncode == 0 or 'Permission denied' not in linked.stderr:
        raise ValueError('Expected Android shell hard-link creation to be denied')
    command('ln', '-s', 'data', f'{source}/dir/symbolic-link')
    write_file(f'{source}/obsolete', b'remove me\n')
    print(command(*executor, binary, '--version', capture_output=True).stdout.splitlines()[0], flush=True)
    for updated in (False, True):
        options = ['-aH']
        if updated:
            data = b'updated Android data\n'
            change = b'changed delta block'
            delta = delta[:65536] + change + delta[65536 + len(change):]
            write_file(data_path, data)
            write_file(delta_path, delta)
            command('chmod', '600', data_path)
            command('rm', f'{source}/obsolete')
            options += ['--delete', '--no-whole-file', '--stats']
        command('touch', '-t', '200201020304.05' if updated else '200101020304.05', data_path, delta_path)
        result = command('env', 'LC_ALL=C', *executor, binary, *options, source + '/', destination + '/',
                         capture_output=True, check=False, timeout=120)
        if result.returncode:
            raise ValueError(f'Android transfer failed ({result.returncode}): {result.stdout}{result.stderr}')
        for name, expected in (('data', data), ('delta', delta)):
            path = f'{destination}/dir/{name}'
            received = read_file(path)
            if received != expected:
                raise ValueError(f'Android transfer changed {name}: expected {expected[:32]!r}, got {received[:32]!r}')
            if stat(f'{source}/dir/{name}', '%Y') != stat(path, '%Y'):
                raise ValueError(f'Android transfer lost the {name} timestamp')
        if stat(f'{destination}/dir/data', '%a') != ('600' if updated else '640'):
            raise ValueError('Android transfer lost file permissions')
        if hardlinks and stat(f'{destination}/dir/data', '%i') != stat(f'{destination}/dir/hard-link', '%i'):
            raise ValueError('Android transfer lost a hard link')
        if command('readlink', f'{destination}/dir/symbolic-link', capture_output=True).stdout.strip() != 'data':
            raise ValueError('Android transfer changed a symlink')
        if updated:
            command('test', '!', '-e', f'{destination}/obsolete')
            counts = {}
            for name in ('Matched', 'Literal'):
                match = re.search(rf'^{name} data: ([0-9,]+) bytes$', result.stdout, re.MULTILINE)
                if not match:
                    raise ValueError(f'Missing Android {name.lower()} data statistics: {result.stdout}')
                counts[name] = int(match[1].replace(',', ''))
            if counts['Matched'] <= 0 or not 0 < counts['Literal'] < len(delta):
                raise ValueError(f'Android update did not use the delta algorithm: {counts}')
            print(f'Delta: {counts["Matched"]} matched bytes, {counts["Literal"]} literal bytes', flush=True)
    print('Android transfer passed', flush=True)


def test_termux():
    image = os.environ['TERMUX_IMAGE']
    run('docker', 'pull', image, timeout=300)
    platform = run('docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', image,
                   capture_output=True).stdout.strip()
    if platform != 'linux/amd64':
        raise ValueError(f'Unexpected Termux image platform: {platform}')
    container = run('docker', 'run', '--detach', '--rm', '--network', 'none', '--user', '1000:1000',
                    '--cap-drop', 'ALL', '--pids-limit', '256', '--memory', '1g',
                    '--security-opt', 'no-new-privileges', '--mount',
                    f'type=bind,source={Path("rsync").resolve()},target=/workspace/rsync,readonly',
                    image, 'sleep', '300', capture_output=True).stdout.strip()
    try:
        def command(*args, **kwargs):
            return run('docker', 'exec', '-i', container, *args, **kwargs)
        work = command('mktemp', '-d', capture_output=True).stdout.strip()
        check_transfer(command, '/workspace/rsync', work)
    finally:
        run('docker', 'rm', '--force', container)


def restore_binary(directory, abi, destination):
    binaries = list(directory.glob(f'rsync-*-android-{abi}'))
    if len(binaries) != 1:
        raise ValueError('Expected one Android binary')
    binary = binaries[0]
    data = binary.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if binary.with_name(binary.name + '.sha256').read_text() != f'{digest}  {binary.name}\n':
        raise ValueError('Android binary checksum does not match')
    with destination.open('xb') as stream:
        stream.write(data)
    destination.chmod(0o755)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=['test', 'termux', 'package', 'restore'])
    parser.add_argument('--executor')
    parser.add_argument('--abi', choices=['arm64-v8a', 'armeabi-v7a', 'x86_64'])
    args = parser.parse_args()
    if args.task == 'termux':
        test_termux()
        return 0
    if args.task == 'test':
        description = subprocess.check_output(['file', 'rsync'], text=True, timeout=30)
        print(description, end='')
        if 'statically linked' not in description or 'dynamically linked' in description:
            raise ValueError('Android binary is not statically linked')
        if not args.executor:
            parser.error('--executor is required for Bionic testing')
        executor = [] if args.executor == 'direct' else [args.executor]
        with tempfile.TemporaryDirectory(prefix='rsync-android-') as work:
            check_transfer(run, str(Path('rsync').resolve()), work, executor)
        return 0
    if not args.abi:
        parser.error('--abi is required for packaging or restoring a binary')
    if args.task == 'restore':
        restore_binary(Path('dist'), args.abi, Path('rsync'))
        return 0
    match = re.search(r'RSYNC_VERSION\s+"([A-Za-z0-9.+-]+)"', Path('version.h').read_text())
    if not match:
        raise ValueError('Cannot determine the rsync version')
    name = f'rsync-{match[1]}-android-{args.abi}'
    output = Path('dist')
    output.mkdir(exist_ok=True)
    binary = output / name
    shutil.copy2('rsync', binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (output / f'{name}.sha256').write_text(f'{digest}  {name}\n')
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        stream.write(f'ARTIFACT_NAME=rsync-android-{args.abi}\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Android check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
