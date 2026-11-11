import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def run(*command, timeout=30, **kwargs):
    kwargs.setdefault('check', True)
    return subprocess.run(command, text=True, timeout=timeout, **kwargs)


def wait_for_boot(adb, proc, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('Emulator exited before boot completed')
        try:
            if adb('shell', 'getprop sys.boot_completed', capture_output=True, timeout=10).stdout.strip() == '1':
                return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        time.sleep(2)
    raise TimeoutError('Android boot did not complete')


def stop_emulator(adb, proc):
    if proc.poll() is not None:
        return
    try:
        adb('emu', 'kill', timeout=10, capture_output=True)
        proc.wait(timeout=20)
        return
    except subprocess.SubprocessError:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def check_device(adb):
    values = {}
    commands = {'api': ('getprop', 'ro.build.version.sdk'),
                'abi': ('getprop', 'ro.product.cpu.abi'),
                'emulator': ('getprop', 'ro.kernel.qemu'),
                'selinux': ('getenforce',), 'uid': ('id', '-u'),
                'kernel': ('uname', '-r'), 'fingerprint': ('getprop', 'ro.build.fingerprint')}
    for name, command in commands.items():
        values[name] = adb('shell', shlex.join(command), capture_output=True).stdout.strip()
    for name, expected in dict(api='36', abi='x86_64', emulator='1', selinux='Enforcing', uid='2000').items():
        if values[name] != expected:
            raise ValueError(f'Android {name}: expected {expected}, got {values[name]!r}')
    print(json.dumps(values, indent=2), flush=True)
    return values


def check_transfer(adb, binary, output):
    device = check_device(adb)
    work = adb('shell', 'mktemp -d /data/local/tmp/rsync-ci.XXXXXXXX', capture_output=True).stdout.strip()
    if not re.fullmatch(r'/data/local/tmp/rsync-ci\.[A-Za-z0-9]{8}', work):
        raise ValueError('Invalid Android scratch directory')
    scripts = Path(__file__).parent
    try:
        for path in (binary, scripts / 'android-transfer.sh'):
            adb('push', str(path), f'{work}/{path.name}', timeout=60)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        remote = adb('shell', shlex.join(['sha256sum', f'{work}/{binary.name}']),
                     capture_output=True).stdout.split()[0]
        if digest != remote:
            raise ValueError('Android binary hash does not match the candidate')
        adb('shell', shlex.join(['chmod', '700', f'{work}/{binary.name}']))
        with (output / 'transfer.log').open('w') as log:
            adb('shell', shlex.join(['env', f'TMPDIR={work}', 'sh', f'{work}/android-transfer.sh',
                                    'direct', f'{work}/{binary.name}', 'denied']),
                stdout=log, stderr=subprocess.STDOUT, timeout=180)
        check_permissions(adb, binary, work, output)
        receipt = {'device': device, 'binary_sha256': digest,
                   'source': run('git', 'rev-parse', 'HEAD', capture_output=True).stdout.strip()}
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    finally:
        adb('shell', shlex.join(['rm', '-rf', work]))


def check_permissions(adb, binary, work, output):
    with tempfile.TemporaryDirectory(prefix='rsync-unreadable-') as directory:
        private = Path(directory) / 'private'
        private.write_text('must not be copied\n')
        adb('shell', shlex.join(['mkdir', f'{work}/unreadable', f'{work}/refused']))
        adb('push', str(private), f'{work}/unreadable/private')
    adb('shell', shlex.join(['chmod', '000', f'{work}/unreadable/private']))
    result = adb('shell', shlex.join([f'{work}/{binary.name}', '-r', f'{work}/unreadable/',
                                    f'{work}/refused/']), capture_output=True, check=False)
    (output / 'permissions.log').write_text(result.stdout + result.stderr)
    if result.returncode != 23 or 'Permission denied' not in result.stderr:
        raise ValueError('Unreadable Android source did not fail with permission denied and exit 23')
    adb('shell', shlex.join(['test', '!', '-e', f'{work}/refused/private']))
    adb('shell', 'test -d /data/system')
    adb('shell', 'test ! -r /data/system')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, default=Path('rsync'))
    parser.add_argument('--output', type=Path, default=Path('test-results/android-emulator'))
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True)
    sdk = Path(os.environ['ANDROID_HOME'])
    image = sdk / 'system-images/android-36/google_apis/x86_64/source.properties'
    properties = image.read_text()
    if 'Pkg.Revision=7' not in properties.splitlines():
        raise ValueError('Expected Android API 36 Google APIs X86-64 image revision 7')
    (args.output / 'image.properties').write_text(properties)
    emulator = shutil.which('emulator')
    if not emulator:
        raise ValueError('Android emulator is not on PATH')
    (args.output / 'emulator-version.log').write_text(
        run(emulator, '-version', capture_output=True).stdout)
    run(emulator, '-accel-check')
    serial = 'emulator-5560'
    def adb(*command, **kwargs):
        return run('adb', '-s', serial, *command, **kwargs)
    if serial in run('adb', 'devices', capture_output=True).stdout.split():
        raise ValueError(f'{serial} is already in use')
    def cancel(signum, frame):
        raise SystemExit(128 + signum)
    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with tempfile.TemporaryDirectory(prefix='rsync-avd-') as directory:
            env = {**os.environ, 'ANDROID_AVD_HOME': directory}
            run('avdmanager', 'create', 'avd', '--name', 'rsync', '--package',
                'system-images;android-36;google_apis;x86_64', input='no\n', env=env, timeout=60)
            with (args.output / 'emulator.log').open('w') as log:
                proc = subprocess.Popen([emulator, '-avd', 'rsync', '-port', '5560', '-accel', 'on',
                                         '-no-window', '-no-audio', '-no-boot-anim', '-no-snapshot',
                                         '-gpu', 'swiftshader', '-memory', '2048', '-cores', '2'],
                                        env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_for_boot(adb, proc)
                    check_transfer(adb, binary, args.output)
                finally:
                    stop_emulator(adb, proc)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print((args.output / 'transfer.log').read_text(), end='')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Android emulator check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
