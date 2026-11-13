#!/usr/bin/env python3

import argparse
from contextlib import redirect_stdout
import errno
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.interoperability import digest, seed, snapshot, update
from harness.process import quote_command


def run(*args, check=True, **kwargs):
    print(quote_command(list(map(str, args))), flush=True)
    result = subprocess.run(list(map(str, args)), capture_output=True, timeout=90,
                            env=dict(os.environ, LC_ALL='C'), **kwargs)
    if check and result.returncode:
        raise ValueError(f'{Path(args[0]).name} exited {result.returncode}: '
                         + result.stderr.decode(errors='replace')[-2048:])
    return result


def refused(result, code, message):
    if result.returncode != code or message.encode() not in result.stderr:
        raise ValueError(f'Expected exit {code} with {message!r}, got {result.returncode}: '
                         + result.stderr.decode(errors='replace')[-2048:])


def mount_options(path):
    info = json.loads(run('findmnt', '--json', '--mountpoint', path, '-o', 'TARGET,FSTYPE,OPTIONS').stdout)
    rows = info['filesystems']
    if len(rows) != 1 or rows[0]['target'] != str(path):
        raise ValueError('Storage fixture is not a separate mount')
    return rows[0]['fstype'], set(rows[0]['options'].split(','))


class EncryptedVolume:
    def __init__(self, root):
        self.mount = root / 'encrypted'
        self.mount.mkdir()
        self.image = root / 'encrypted.img'
        with self.image.open('xb') as stream:
            stream.truncate(256 * 1024 * 1024)
        self.name = 'rsync-ci-' + secrets.token_hex(8)
        self.device = Path('/dev/mapper') / self.name
        if self.device.exists():
            raise ValueError('Storage mapping name is already in use')
        self.key = secrets.token_bytes(32)
        self.loop = None

    def open(self):
        run('cryptsetup', 'open', '--key-file=-', self.loop, self.name, input=self.key)

    def attach(self):
        run('mount', '-t', 'ext4', '-o', 'nosuid,nodev,noexec', self.device, self.mount)
        kind, options = mount_options(self.mount)
        if kind != 'ext4' or not {'rw', 'nosuid', 'nodev', 'noexec'} <= options:
            raise ValueError('Encrypted filesystem restrictions are missing')

    def close(self):
        if os.path.ismount(self.mount):
            run('umount', self.mount)
        status = run('cryptsetup', 'status', self.name, check=False)
        if status.returncode == 0:
            run('cryptsetup', 'close', self.name)
            status = run('cryptsetup', 'status', self.name, check=False)
        if status.returncode != 4 or self.device.exists():
            raise ValueError('Encrypted mapping cleanup could not be verified')

    def __enter__(self):
        try:
            self.loop = run('losetup', '--find', '--show', '--nooverlap', self.image).stdout.decode().strip()
            info = json.loads(run('losetup', '--list', '--json', self.loop).stdout)['loopdevices']
            if len(info) != 1 or Path(info[0]['back-file']).resolve() != self.image.resolve():
                raise ValueError('Loop device does not belong to the fixture')
            run('cryptsetup', 'luksFormat', '--batch-mode', '--type', 'luks2', '--pbkdf', 'pbkdf2',
                '--iter-time', '10', '--key-file=-', self.loop, input=self.key)
            with self.image.open('rb') as stream:
                if stream.read(8) != b'LUKS\xba\xbe\x00\x02':
                    raise ValueError('Storage image is not LUKS2')
            self.open()
            run('mkfs.ext4', '-q', '-F', '-m', '0', self.device)
            self.attach()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        finally:
            loops = json.loads(run('losetup', '--list', '--json', '--associated', self.image).stdout)['loopdevices']
            for device in loops:
                if (not re.fullmatch(r'/dev/loop\d+', device['name'])
                        or Path(device['back-file']).resolve() != self.image.resolve()):
                    raise ValueError('Storage cleanup found an unrelated loop device')
                run('losetup', '--detach', device['name'])
            if json.loads(run('losetup', '--list', '--json', '--associated', self.image).stdout)['loopdevices']:
                raise ValueError('Storage loop device remains attached')
            self.key = b''


def encrypted(binary, root, checks):
    source = root / 'source'
    seed(source)
    run('setfacl', '-m', 'u:60001:r--', source / 'space name')
    os.setxattr(source / 'space name', 'user.rsync-ci', b'first attribute')
    acl = run('getfacl', '-cn', source / 'space name').stdout
    with EncryptedVolume(root) as volume:
        destination = volume.mount / 'backup'
        for phase in (1, 2):
            if phase == 2:
                update(source)
                os.setxattr(source / 'space name', 'user.rsync-ci', b'second attribute')
            run(binary, '-rltpHAX', '--checksum', '--delete', str(source) + '/', str(destination) + '/')
            if snapshot(source) != snapshot(destination):
                raise ValueError('Encrypted transfer changed data or metadata')
            if (os.getxattr(destination / 'space name', 'user.rsync-ci') != os.getxattr(source / 'space name', 'user.rsync-ci')
                    or run('getfacl', '-cn', destination / 'space name').stdout != acl):
                raise ValueError('Encrypted transfer lost ACLs or xattrs')
            checks.append('encrypted transfer ' + str(phase))
        linked = volume.mount / 'linked'
        run(binary, '-rltpHAX', '--link-dest=' + str(destination), str(source) + '/', str(linked) + '/')
        if (linked / 'space name').stat().st_ino != (destination / 'space name').stat().st_ino:
            raise ValueError('Encrypted link-dest did not reuse the basis file')
        checks.append('encrypted link-dest with xattrs')
        executable = volume.mount / 'rsync'
        shutil.copyfile(binary, executable)
        executable.chmod(0o755)
        try:
            run(executable, '--version')
        except PermissionError:
            pass
        else:
            raise ValueError('The noexec mount allowed execution')
        executable.unlink()
        checks.append('noexec data transfer')
        saved = snapshot(volume.mount)
        volume.close()
        wrong = run('cryptsetup', 'open', '--key-file=-', volume.loop, volume.name,
                    input=secrets.token_bytes(32), check=False)
        if wrong.returncode != 2 or volume.device.exists():
            raise ValueError('Encrypted volume accepted an incorrect key')
        volume.open()
        volume.attach()
        if snapshot(volume.mount) != saved:
            raise ValueError('Encrypted backup changed after reopening')
        if (os.getxattr(destination / 'space name', 'user.rsync-ci') != b'second attribute'
                or run('getfacl', '-cn', destination / 'space name').stdout != acl):
            raise ValueError('Encrypted backup lost metadata after reopening')
        checks.append('encrypted reopen')
        run('mount', '-o', 'remount,ro', volume.mount)
        if 'ro' not in mount_options(volume.mount)[1]:
            raise ValueError('Storage fixture did not become read-only')
        try:
            (destination / 'write-probe').touch()
        except OSError as error:
            if error.errno != errno.EROFS:
                raise
        else:
            raise ValueError('Read-only positive control remained writable')
        (source / 'new file').write_text('must not be written\n')
        result = run(binary, '-rc', str(source) + '/', str(destination) + '/', check=False)
        refused(result, 23, 'Read-only file system')
        if snapshot(volume.mount) != saved:
            raise ValueError('Read-only failure changed the destination')
        restored = root / 'restored'
        run(binary, '-rltpHAX', str(destination) + '/', str(restored) + '/')
        if snapshot(destination) != snapshot(restored):
            raise ValueError('Reading the read-only backup failed')
        checks.append('read-only destination and readable source')


def restricted(binary, root, checks):
    root.chmod(0o755)
    source, destination, outside = root / 'readable', root / 'restricted', root / 'outside'
    for path in (source, destination, outside):
        path.mkdir()
    (source / 'file').write_bytes(b'new contents\n')
    (destination / 'file').write_bytes(b'old contents\n')
    (outside / 'sentinel').write_bytes(b'outside transfer root\n')
    (destination / 'escape').symlink_to(outside, target_is_directory=True)
    before, untouched = snapshot(destination), snapshot(outside)
    destination.chmod(0o555)
    command = ['setpriv', '--reuid=65534', '--regid=65534', '--clear-groups',
               '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs']
    if run(*command, 'id', '-u').stdout.strip() != b'65534':
        raise ValueError('Permission check retained the privileged UID')
    result = run(*command, binary, '-rc', str(source) + '/', str(destination) + '/', check=False)
    refused(result, 23, 'Permission denied')
    if snapshot(destination) != before or snapshot(outside) != untouched:
        raise ValueError('Restricted transfer changed protected data')
    checks.append('non-root permission refusal')
    # A writable receiver must still reject a foreign-owned parent link.
    destination.chmod(0o777)
    os.chown(destination / 'escape', 65534, 65534, follow_symlinks=False)
    (source / 'escape').mkdir()
    (source / 'escape' / 'sentinel').write_text('must not escape\n')
    result = run(binary, '-rcK', str(source) + '/', str(destination) + '/', check=False)
    refused(result, 23, 'Too many levels of symbolic links')
    if (snapshot(outside) != untouched
            or (destination / 'file').read_bytes() != b'new contents\n'):
        raise ValueError('Confined receiver followed a foreign-owned directory link')
    checks.append('foreign symlink refusal')


def full_filesystem(binary, root, checks):
    source, destination = root / 'large', root / 'small'
    source.mkdir()
    destination.mkdir()
    payload = b''.join(value.to_bytes(8, 'little') * 128 for value in range(8192))
    (source / 'file').write_bytes(payload)
    run('mount', '-t', 'tmpfs', '-o', 'size=4m,nosuid,nodev,noexec', 'tmpfs', destination)
    try:
        if mount_options(destination)[0] != 'tmpfs':
            raise ValueError('Full-filesystem test is not on tmpfs')
        for label, options in (('atomic', []), ('partial', ['--partial-dir=.partial']),
                               ('inplace', ['--inplace']), ('append-verify', ['--append-verify'])):
            target = destination / 'file'
            target.write_bytes(b'original destination\n')
            result = run(binary, '-rc', *options, str(source) + '/', str(destination) + '/', check=False)
            refused(result, 11, 'No space left on device')
            if label in ('atomic', 'partial') and target.read_bytes() != b'original destination\n':
                raise ValueError('Full-filesystem failure replaced the original destination')
            if label == 'partial' and not (destination / '.partial' / 'file').is_file():
                raise ValueError('Full-filesystem failure discarded the resumable partial file')
            run('mount', '-o', 'remount,size=32m', destination)
            resumed = run(binary, '-rc', '--no-whole-file', '--stats', *options, str(source) + '/', str(destination) + '/')
            if digest(target) != digest(source / 'file') or (destination / '.partial').exists():
                raise ValueError('Full-filesystem recovery did not complete correctly')
            if label != 'atomic':
                matched = re.search(rb'^Matched data: ([0-9,]+) bytes$', resumed.stdout, re.MULTILINE)
                if not matched or int(matched[1].replace(b',', b'')) <= 0:
                    raise ValueError('Full-filesystem recovery did not reuse partial data')
            target.unlink()
            run('mount', '-o', 'remount,size=4m', destination)
            checks.append('ENOSPC recovery ' + label)
    finally:
        run('umount', destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parent-namespace', help=argparse.SUPPRESS)
    args = parser.parse_args()
    binary, output = args.binary.resolve(strict=True), args.output.resolve()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Storage checks require Linux root in a disposable environment')
    namespace = os.readlink('/proc/self/ns/mnt')
    if args.parent_namespace is None:
        with subprocess.Popen(['unshare', '--mount', '--propagation', 'private', '--', sys.executable, '-B', '-S',
                               str(Path(__file__).resolve()), '--binary', str(binary), '--output', str(output),
                               '--parent-namespace', namespace], start_new_session=True) as child:
            def interrupted(signum, frame):
                raise SystemExit(128 + signum)
            previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                return child.wait(timeout=600)
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    if namespace == args.parent_namespace:
        parser.error('Storage checks require a separate mount namespace')
    output.mkdir(parents=True)
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    checks = []
    with (output / 'run.log').open('w') as log, redirect_stdout(log):
        with tempfile.TemporaryDirectory(prefix='rsync-storage-') as directory:
            root = Path(directory)
            encrypted(binary, root, checks)
            restricted(binary, root, checks)
            full_filesystem(binary, root, checks)
    receipt = dict(checks=checks, binary_sha256=digest(binary), kernel=os.uname().release,
                   filesystem='LUKS2/ext4 and size-limited tmpfs', cleaned=True)
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Storage checks failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
