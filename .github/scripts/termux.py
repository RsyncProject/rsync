from contextlib import redirect_stdout
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from android import check_transfer as transfer, quote_command, run

PACKAGE = 'com.termux'
VERSION = '0.118.3'
APK_SHA256 = '3550e61f4d9eb49b712fd1bd9519dc37085a4d8eb597c57a340f0a64859b7144'
PREFIX = '/data/data/com.termux/files/usr'
HOME = '/data/data/com.termux/files/home'


def download_apk(path):
    url = f'https://github.com/termux/termux-app/releases/download/v{VERSION}/termux-app_v{VERSION}%2Bgithub-debug_x86_64.apk'
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=30) as source, path.open('xb') as output:
        while True:
            data = source.read(65536)
            if not data:
                break
            size += len(data)
            if size > 64 * 1024 * 1024:
                raise ValueError('Termux APK exceeds the size limit')
            digest.update(data)
            output.write(data)
    if digest.hexdigest() != APK_SHA256:
        raise ValueError('Termux APK checksum differs')


def check_identity(uid, context, expected_uid):
    if not 10000 <= expected_uid < 20000 or uid != str(expected_uid):
        raise ValueError('Termux command did not use the application UID')
    if not re.fullmatch(r'u:r:untrusted_app_27:s0:c\d+(?:,c\d+)*', context):
        raise ValueError('Termux command did not use the application SELinux domain')


def wait_for_bootstrap(adb, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = adb('shell', 'pidof', '-s', PACKAGE, capture_output=True, check=False).stdout.strip()
        if re.fullmatch(r'\d+', pid):
            log = adb('logcat', '-d', '--pid=' + pid, '-v', 'brief', '-t', '1000',
                      capture_output=True, encoding='utf-8', errors='replace').stdout
            if 'Bootstrap Error:' in log:
                raise ValueError('Termux bootstrap failed')
            if 'Bootstrap packages installed successfully.' in log:
                return
        time.sleep(1)
    raise TimeoutError('Termux bootstrap did not finish')


class Termux:
    def __init__(self, adb, root):
        if not re.fullmatch(re.escape(HOME) + r'/rsync-ci\.[A-Za-z0-9]{8}', root):
            raise ValueError('Invalid Termux scratch directory')
        self.adb, self.root, self.sequence = adb, root, 0

    def run_as(self, *args, **kwargs):
        return self.adb('shell', '-T', '-e', 'none', quote_command(['run-as', PACKAGE, *args]), **kwargs)

    def path(self, path):
        if not path.startswith(self.root + '/') or '..' in PurePosixPath(path).parts or '\0' in path:
            raise ValueError('Path is outside the Termux fixture')
        return path

    def write_file(self, path, data):
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('Termux fixture input exceeds the size limit')
        self.run_as('sh', '-c', 'base64 -d >' + quote_command([self.path(path)]), input=base64.b64encode(data),
                 text=False, capture_output=True)

    def read_file(self, path, optional=False):
        self.path(path)
        if optional:
            exists = self.run_as('test', '-f', path, check=False, capture_output=True)
            if exists.returncode == 1:
                return b''
            exists.check_returncode()
        encoded = self.run_as('sh', '-c', 'set -o pipefail; head -c 1048577 ' + quote_command([path]) + ' | base64',
                              text=False, capture_output=True).stdout
        data = base64.b64decode(b''.join(encoded.split()), validate=True)
        if len(data) > 1048576:
            raise ValueError('Termux fixture output exceeds the size limit')
        return data

    def command(self, *args, capture_output=False, check=True, text=True, timeout=30):
        self.sequence += 1
        script = f'{self.root}/command-{self.sequence}'
        result = f'{self.root}/result-{self.sequence}'
        self.write_file(script, ('#!' + PREFIX + '/bin/sh\nexec ' + quote_command(args) + '\n').encode())
        self.run_as('chmod', '700', script)
        self.run_as(PREFIX + '/bin/am', 'startservice', '-n', PACKAGE + '/.app.RunCommandService',
                 '-a', PACKAGE + '.RUN_COMMAND', '--es', PACKAGE + '.RUN_COMMAND_PATH', script,
                 '--es', PACKAGE + '.RUN_COMMAND_WORKDIR', self.root,
                 '--ez', PACKAGE + '.RUN_COMMAND_BACKGROUND', 'true',
                 '--es', PACKAGE + '.RUN_COMMAND_RESULT_DIRECTORY', result, capture_output=True)
        deadline = time.monotonic() + timeout
        # Termux writes err atomically after the other result files.
        while self.run_as('test', '-f', result + '/err', check=False, capture_output=True).returncode:
            if time.monotonic() >= deadline:
                raise TimeoutError('Termux command did not produce a result')
            time.sleep(0.2)
        error = self.read_file(result + '/err').decode().strip()
        if error != '-1':
            raise ValueError('Termux rejected the command: ' + error)
        code = self.read_file(result + '/exit_code').decode().strip()
        if not re.fullmatch(r'-?\d+', code):
            raise ValueError('Invalid Termux command exit status')
        stdout = self.read_file(result + '/stdout', optional=True)
        stderr = self.read_file(result + '/stderr', optional=True)
        value = subprocess.CompletedProcess(args, int(code), stdout.decode() if text else stdout,
                                            stderr.decode() if text else stderr)
        if check:
            value.check_returncode()
        if not capture_output:
            sys.stdout.write(stdout.decode(errors='replace'))
            sys.stderr.write(stderr.decode(errors='replace'))
        return value


def check_transfer(adb, binary, output, device):
    def shell(*args, **kwargs):
        return adb('shell', '-T', quote_command(args), **kwargs)
    packages = shell('pm', 'list', 'packages', PACKAGE, capture_output=True).stdout.splitlines()
    if 'package:' + PACKAGE in packages:
        raise ValueError('Termux is already installed on the test device')
    owned = False
    try:
        with tempfile.TemporaryDirectory(prefix='rsync-termux-apk-') as directory:
            apk = Path(directory) / 'termux.apk'
            download_apk(apk)
            owned = True
            adb('install', str(apk), timeout=120, capture_output=True)
        def run_as(*args, **kwargs):
            return shell('run-as', PACKAGE, *args, **kwargs)
        run_as('mkdir', '-p', 'files/home/.termux')
        run_as('sh', '-c', 'cat >files/home/.termux/termux.properties', input='allow-external-apps=true\n', capture_output=True)
        shell('am', 'start', '-n', PACKAGE + '/.app.TermuxActivity', capture_output=True)
        wait_for_bootstrap(adb)
        run_as('test', '-x', 'files/usr/bin/am', capture_output=True)
        root = run_as('mktemp', '-d', HOME + '/rsync-ci.XXXXXXXX', capture_output=True).stdout.strip()
        session = Termux(adb, root)
        package = shell('pm', 'list', 'packages', '-U', '--user', '0', PACKAGE, capture_output=True).stdout
        match = re.fullmatch(r'package:com\.termux uid:(\d+)\s*', package)
        if not match:
            raise ValueError('Cannot identify the Termux application UID')
        uid = session.command('/system/bin/id', '-u', capture_output=True).stdout.strip()
        context = session.command('/system/bin/id', '-Z', capture_output=True).stdout.strip()
        check_identity(uid, context, int(match[1]))
        candidate = root + '/rsync'
        data = binary.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        session.write_file(candidate, data)
        session.run_as('chmod', '700', candidate)
        actual = session.command('sha256sum', candidate, capture_output=True).stdout.split()[0]
        if actual != digest:
            size = session.run_as('stat', '-c', '%s', candidate, capture_output=True).stdout.strip()
            raise ValueError(f'Termux binary hash differs from the candidate: {actual}, {size} bytes')
        with (output / 'transfer.log').open('w') as log:
            with redirect_stdout(log):
                transfer(session.command, candidate, root, hardlinks=False,
                         write_file=session.write_file, read_file=session.read_file)
        private = root + '/source/private'
        session.write_file(private, b'must not be copied\n')
        session.command('chmod', '000', private)
        refused = session.command(candidate, '-r', root + '/source/', root + '/refused/',
                                  capture_output=True, check=False)
        (output / 'permissions.log').write_text(refused.stdout + refused.stderr)
        if refused.returncode != 23 or 'Permission denied' not in refused.stderr:
            raise ValueError('Unreadable Termux source was not rejected')
        session.command('test', '!', '-e', root + '/refused/private')
        shell('test', '-d', '/data/system', capture_output=True)
        session.command('test', '!', '-r', '/data/system')
        receipt = dict(device=device, app=dict(package=PACKAGE, version=VERSION, apk_sha256=APK_SHA256,
                                              uid=uid, selinux_context=context), binary_sha256=digest,
                       source=run('git', 'rev-parse', 'HEAD', capture_output=True).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        if owned:
            try:
                pid = shell('pidof', '-s', PACKAGE, capture_output=True).stdout.strip()
                if re.fullmatch(r'\d+', pid):
                    log = adb('logcat', '-d', '--pid=' + pid, '-v', 'brief', '-t', '200',
                              capture_output=True, encoding='utf-8', errors='replace')
                    (output / 'termux.log').write_text(log.stdout + log.stderr)
            except (OSError, subprocess.SubprocessError):
                print('Termux log capture failed', file=sys.stderr)
        raise
    finally:
        if owned:
            failed = sys.exc_info()[0] is not None
            errors = []
            for command in (('shell', 'am', 'force-stop', PACKAGE), ('uninstall', PACKAGE)):
                try:
                    value = adb(*command, timeout=30, capture_output=True, check=False)
                    if value.returncode:
                        errors.append(command[0])
                except (OSError, subprocess.SubprocessError):
                    errors.append(command[0])
            try:
                remaining = shell('pm', 'list', 'packages', PACKAGE, capture_output=True).stdout.splitlines()
                if 'package:' + PACKAGE in remaining:
                    errors.append('installation remains')
                running = shell('pidof', '-s', PACKAGE, capture_output=True, check=False)
                if running.returncode not in (0, 1):
                    errors.append('process check')
                elif running.stdout.strip() or running.returncode == 0:
                    errors.append('application remains running')
            except (OSError, subprocess.SubprocessError):
                errors.append('cleanup verification')
            if errors:
                message = 'Termux cleanup failed: ' + ', '.join(errors)
                if not failed:
                    raise ValueError(message)
                print(message, file=sys.stderr)
    receipt['app']['cleaned'] = True
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
