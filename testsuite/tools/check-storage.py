#!/usr/bin/env python3

import argparse
from contextlib import ExitStack, contextmanager, redirect_stdout
import errno
import grp
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.interoperability import digest, seed, snapshot, update
from harness.process import quote_command
from harness.ssh import ProcessServer


def run(*args, check=True, **kwargs):
    print(quote_command(list(map(str, args))), flush=True)
    argv, data = list(map(str, args)), kwargs.pop('input', None)
    with subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                          env=dict(os.environ, LC_ALL='C'), **kwargs) as child:
        try:
            stdout, stderr = child.communicate(data, timeout=90)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate(timeout=5)
            raise
        result = subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
    if check and result.returncode:
        raise ValueError(f'{Path(args[0]).name} exited {result.returncode}: '
                         + result.stderr.decode(errors='replace')[-2048:])
    return result


def refused(result, code, message):
    if result.returncode != code or message.encode() not in result.stderr:
        raise ValueError(f'Expected exit {code} with {message!r}, got {result.returncode}: '
                         + result.stderr.decode(errors='replace')[-2048:])


def mount_options(path, required=True):
    result = run('findmnt', '--json', '--mountpoint', path, '-o', 'TARGET,FSTYPE,OPTIONS', check=False)
    if result.returncode == 1 and not required:
        return None
    if result.returncode:
        raise ValueError('Cannot inspect the storage fixture mount')
    info = json.loads(result.stdout)
    rows = info['filesystems']
    if len(rows) != 1 or rows[0]['target'] != str(path):
        raise ValueError('Storage fixture is not a separate mount')
    return rows[0]['fstype'], set(rows[0]['options'].split(','))


@contextmanager
def mounted(path, *options):
    try:
        run('mount', *options, path)
        if mount_options(path, required=False) is None:
            raise ValueError('Storage fixture did not become a mount point')
        yield path
    finally:
        if mount_options(path, required=False) is not None:
            run('umount', path)


def check_loop(device, image, readonly=False):
    if not re.fullmatch(r'/dev/loop\d+', str(device)):
        raise ValueError('Storage fixture did not allocate a loop device')
    rows = json.loads(run('losetup', '--list', '--json', '--output', 'NAME,BACK-FILE,RO,OFFSET,SIZELIMIT', device).stdout)['loopdevices']
    if (len(rows) != 1 or rows[0]['name'] != str(device)
            or Path(rows[0]['back-file']).resolve() != image.resolve()
            or rows[0]['ro'] is not readonly or rows[0]['offset'] != 0 or rows[0]['sizelimit'] != 0):
        raise ValueError('Loop device does not match the fixture image or access mode')


def detach_loops(image):
    loops = json.loads(run('losetup', '--list', '--json', '--associated', image).stdout)['loopdevices']
    for device in loops:
        if (not re.fullmatch(r'/dev/loop\d+', device['name'])
                or Path(device['back-file']).resolve() != image.resolve()):
            raise ValueError('Storage cleanup found an unrelated loop device')
        run('losetup', '--detach', device['name'])
    deadline = time.monotonic() + 5
    while json.loads(run('losetup', '--list', '--json', '--associated', image).stdout)['loopdevices']:
        if time.monotonic() >= deadline:
            raise ValueError('Storage loop device remains attached')
        time.sleep(0.05)


@contextmanager
def loop_device(image, readonly=False):
    info = image.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise ValueError('Loop image is not a regular file owned by the fixture')
    fd = None
    try:
        options = ['--read-only'] if readonly else []
        device = Path(run('losetup', '--find', '--show', '--nooverlap', *options, image).stdout.decode().strip())
        check_loop(device, image, readonly)
        fd = os.open(device, os.O_RDONLY if readonly else os.O_RDWR)
        if not stat.S_ISBLK(os.fstat(fd).st_mode):
            raise ValueError('Loop allocation is not a block device')
        yield device
    finally:
        if fd is not None:
            os.close(fd)
        detach_loops(image)


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
        self.resources = ExitStack()

    def open(self):
        run('cryptsetup', 'open', '--key-file=-', self.loop, self.name, input=self.key)

    def attach(self):
        run('mount', '-t', 'ext4', '-o', 'nosuid,nodev,noexec', self.device, self.mount)
        kind, options = mount_options(self.mount)
        if kind != 'ext4' or not {'rw', 'nosuid', 'nodev', 'noexec'} <= options:
            raise ValueError('Encrypted filesystem restrictions are missing')

    def close(self):
        if mount_options(self.mount, required=False) is not None:
            run('umount', self.mount)
        status = run('cryptsetup', 'status', self.name, check=False)
        if status.returncode == 0:
            run('cryptsetup', 'close', self.name)
            status = run('cryptsetup', 'status', self.name, check=False)
        if status.returncode != 4 or self.device.exists():
            raise ValueError('Encrypted mapping cleanup could not be verified')

    def __enter__(self):
        try:
            self.loop = self.resources.enter_context(loop_device(self.image))
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
            self.resources.close()
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


def filesystem_boundaries(binary, root, checks):
    base = root / 'boundaries'
    source, same = base / 'source', base / 'same-device'
    nested = source / 'nested'
    for path in (nested / 'boundary', nested / 'same', same):
        path.mkdir(parents=True)
    (source / 'local-file').write_bytes(b'local filesystem\n')
    (same / 'same-file').write_bytes(b'same-device bind mount\n')
    (nested / 'directory-link').symlink_to('boundary', target_is_directory=True)
    (nested / 'file-link').symlink_to('boundary/boundary-file')
    with ExitStack() as mounts:
        mounts.enter_context(mounted(nested / 'boundary', '-t', 'tmpfs', '-o', 'size=1m,nosuid,nodev,noexec', 'tmpfs'))
        mounts.enter_context(mounted(nested / 'same', '--bind', same))
        (nested / 'boundary/boundary-file').write_bytes(b'other filesystem\n')
        if (nested / 'boundary').stat().st_dev == source.stat().st_dev or (nested / 'same').stat().st_dev != source.stat().st_dev:
            raise ValueError('Filesystem boundary controls did not provide different and matching devices')
        complete = snapshot(source)['entries']
        control = base / 'control'
        run(binary, '-rltp', str(source) + '/', str(control) + '/')
        if snapshot(control)['entries'] != complete:
            raise ValueError('Unrestricted mount traversal did not copy the complete fixture')
        checks.append('filesystem traversal control')
        for recursion, extra in (('incremental', []), ('full-list', ['--no-inc-recursive'])):
            for option in ('-x', '-xx'):
                expected = {key: value for key, value in complete.items()
                            if not key.startswith('nested/boundary/') and not (option == '-xx' and key == 'nested/boundary')}
                for link_mode, follow in (('preserve-links', False), ('follow-links', True)):
                    wanted = dict(expected)
                    if follow:
                        wanted['nested/file-link'] = complete['nested/boundary/boundary-file']
                        if option == '-xx':
                            del wanted['nested/directory-link']
                        else:
                            wanted['nested/directory-link'] = complete['nested/boundary']
                    label = f'{option}-{recursion}-{link_mode}'
                    target = base / label
                    run(binary, '-rLtp' if follow else '-rltp', option, *extra, str(source) + '/', str(target) + '/')
                    if snapshot(target)['entries'] != wanted:
                        raise ValueError(label + ': filesystem traversal or followed-link boundaries differ')
                    checks.append('filesystem traversal ' + label)
        for index, reset in enumerate(('--no-x', '--no-one-file-system')):
            target = base / ('reset-' + str(index))
            run(binary, '-rltpxx', reset, str(source) + '/', str(target) + '/')
            if snapshot(target)['entries'] != complete:
                raise ValueError(reset + ': filesystem traversal did not reset')
            checks.append('filesystem boundary reset ' + reset)
        target = base / 'explicit'
        run(binary, '-rltpx', source / 'local-file', str(nested / 'boundary') + '/', str(target) + '/')
        expected = {'local-file': complete['local-file'], 'boundary-file': complete['nested/boundary/boundary-file']}
        if snapshot(target)['entries'] != expected:
            raise ValueError('One-file-system rejected an explicit source on another filesystem')
        checks.append('explicit cross-filesystem sources')

        empty, destination, delete_same = base / 'empty', base / 'delete', base / 'delete-same'
        for path in (empty / 'nested/boundary', empty / 'nested/same', destination / 'nested/boundary',
                     destination / 'nested/same', delete_same):
            path.mkdir(parents=True)
        mounts.enter_context(mounted(destination / 'nested/boundary', '-t', 'tmpfs', '-o', 'size=1m,nosuid,nodev,noexec', 'tmpfs'))
        mounts.enter_context(mounted(destination / 'nested/same', '--bind', delete_same))
        sentinel = destination / 'nested/boundary/sentinel'
        sentinel.write_bytes(b'mounted data\n')
        run(binary, '-r', '--delete-before', str(empty) + '/', str(destination) + '/')
        if sentinel.exists():
            raise ValueError('Unrestricted deletion did not reach the mounted filesystem')
        checks.append('filesystem deletion control')
        for option in ('-x', '-xx'):
            for deletion in ('--delete-before', '--delete-during', '--delete-delay', '--delete-after'):
                sentinel.write_bytes(b'mounted data\n')
                (destination / 'nested/same/remove').write_bytes(b'same-device data\n')
                (destination / 'remove').write_bytes(b'ordinary data\n')
                before = snapshot(destination / 'nested/boundary')
                run(binary, '-r', option, deletion, str(empty) + '/', str(destination) + '/')
                if (snapshot(destination / 'nested/boundary') != before or (destination / 'nested/same/remove').exists()
                        or (destination / 'remove').exists()):
                    raise ValueError(option + ' ' + deletion + ': deletion crossed or invented a filesystem boundary')
                checks.append('filesystem deletion ' + option + ' ' + deletion)


def device_alias(path, device):
    info = device.stat()
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError('Fixture device is not a block device')
    os.mknod(path, stat.S_IFBLK | 0o600, info.st_rdev)
    return path


def device_identity(path):
    info = path.lstat()
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError('Device transfer replaced the device instead of writing in place')
    return info.st_ino, info.st_rdev


def write_device(binary, source, target, device, image, *options, readonly=False, check=True):
    if target.parent != image.parent:
        raise ValueError('Device write target is outside the fixture')
    check_loop(device, image, readonly)
    identity = device_identity(target)
    if identity[1] != device.stat().st_rdev:
        raise ValueError('Device write target is not a private alias for the fixture loop')
    result = run(binary, '--write-devices', '--checksum', *options, source, target, check=check)
    if device_identity(target) != identity:
        raise ValueError('Device transfer replaced the device instead of writing in place')
    check_loop(device, image, readonly)
    return result


def device_transfers(binary, root, checks):
    base = root / 'devices'
    base.mkdir()
    with mounted(base, '-t', 'tmpfs', '-o', 'size=32m,mode=0700,dev,nosuid,noexec', 'tmpfs'):
        kind, options = mount_options(base)
        if kind != 'tmpfs' or 'nodev' in options or not {'nosuid', 'noexec'} <= options:
            raise ValueError('Device aliases require a private device-enabled filesystem')
        device_contents(binary, base, checks)


def device_contents(binary, base, checks):
    data = b''.join(value.to_bytes(8, 'little') * 512 for value in range(256))
    guard = b'\xfe' * (2 * len(data))
    source_image, target_image, readonly_image = (base / name for name in ('source.img', 'target.img', 'readonly.img'))
    for path, content in ((source_image, data), (target_image, guard), (readonly_image, guard)):
        with path.open('xb') as stream:
            stream.write(content)
    source = base / 'source-file'
    source.write_bytes(data)
    with loop_device(source_image) as sending, loop_device(target_image) as receiving:
        source_device = device_alias(base / 'source-device', sending)
        target_device = device_alias(base / 'target-device', receiving)
        if source_device.read_bytes() != data or target_device.read_bytes() != guard:
            raise ValueError('Private device aliases did not expose the expected backing data')
        control = base / 'skipped'
        skipped = run(binary, source_device, control, check=False)
        if skipped.returncode or control.exists() or b'skipping non-regular file' not in skipped.stdout + skipped.stderr:
            raise ValueError('A device source was not skipped without copy-devices')
        copied = base / 'copied'
        run(binary, '--copy-devices', '--checksum', source_device, copied)
        if not stat.S_ISREG(copied.lstat().st_mode) or copied.read_bytes() != data:
            raise ValueError('Copy-devices did not copy the complete device to a regular file')
        checks.append('device to regular file')
        archived = base / 'archived'
        run(binary, '-a', '--copy-devices', source_device, archived)
        if not stat.S_ISREG(archived.lstat().st_mode) or archived.read_bytes() != data:
            raise ValueError('Archive mode turned copy-devices into device-node preservation')
        checks.append('copy-devices with archive mode')

        expected = data + guard[len(data):]
        write_device(binary, source, target_device, receiving, target_image)
        if target_device.read_bytes() != expected or int(run('blockdev', '--getsize64', target_device).stdout) != len(guard):
            raise ValueError('Write-devices changed the capacity or unwritten device tail')
        checks.append('regular file to device with implicit inplace')
        updated = data[:65536] + b'newblock' + data[65544:]
        source.write_bytes(updated)
        result = write_device(binary, source, target_device, receiving, target_image, '--no-whole-file', '--stats')
        matched = re.search(rb'^Matched data: ([0-9,]+) bytes$', result.stdout, re.MULTILINE)
        literal = re.search(rb'^Literal data: ([0-9,]+) bytes$', result.stdout, re.MULTILINE)
        if (target_device.read_bytes() != updated + guard[len(data):] or not matched or not literal
                or int(matched[1].replace(b',', b'')) <= 0 or not 0 < int(literal[1].replace(b',', b'')) < len(data)):
            raise ValueError('Device delta update changed data or did not reuse the device basis')
        checks.append('device delta update')
        write_device(binary, source_device, target_device, receiving, target_image, '--copy-devices')
        if target_device.read_bytes() != expected:
            raise ValueError('Device-to-device transfer changed data or the unwritten tail')
        checks.append('device to device')

        alias = device_alias(base / 'disabled-write', receiving)
        run(binary, '--write-devices', '--no-write-devices', '--checksum', source, alias)
        if not stat.S_ISREG(alias.lstat().st_mode) or alias.read_bytes() != updated or target_device.read_bytes() != expected:
            raise ValueError('No-write-devices did not replace only the private alias')
        checks.append('device writes disabled')
        link = base / 'device-link'
        link.symlink_to(target_device)
        run(binary, '--write-devices', '--checksum', source, link)
        if not stat.S_ISREG(link.lstat().st_mode) or link.read_bytes() != updated or target_device.read_bytes() != expected:
            raise ValueError('Write-devices followed a destination symlink')
        checks.append('device symlink replacement')

        with loop_device(readonly_image, readonly=True) as protected:
            protected_alias = device_alias(base / 'readonly-device', protected)
            code = 23
            try:
                fd = os.open(protected_alias, os.O_WRONLY)
            except OSError as error:
                failure = error
            else:
                try:
                    code = 11
                    os.write(fd, b'X')
                except OSError as error:
                    failure = error
                else:
                    raise ValueError('Read-only loop device accepted a write')
                finally:
                    os.close(fd)
            messages = {errno.EPERM: 'Operation not permitted', errno.EROFS: 'Read-only file system'}
            if failure.errno not in messages:
                raise failure
            result = write_device(binary, source, protected_alias, protected, readonly_image, readonly=True, check=False)
            refused(result, code, messages[failure.errno])
            if protected_alias.read_bytes() != guard:
                raise ValueError('Read-only device refusal changed the backing image')
        checks.append('read-only device refusal')

        conf = base / 'rsyncd.conf'
        conf.write_text(f'use chroot = no\nuid = {os.geteuid()}\ngid = {os.getegid()}\n'
                        f'[data]\npath = {base}\nread only = no\n'
                        f'[enabled]\npath = {base}\nread only = no\n'
                        f'refuse options = !copy-devices !write-devices\n')
        log = base / 'daemon'
        log.mkdir()
        env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
        with ProcessServer([str(binary), '--daemon', '--no-detach', '--config=' + str(conf)], env, log) as server:
            remote = f'rsync://127.0.0.1:{server.port}/data/'
            normal = base / 'daemon-control'
            run(binary, remote + source.name, normal)
            if normal.read_bytes() != updated:
                raise ValueError('Device daemon positive control failed')
            for option, operands in (('--copy-devices', [remote + source_device.name, base / 'refused-copy']),
                                     ('--write-devices', [source, remote + target_device.name])):
                result = run(binary, option, *operands, check=False)
                if result.returncode == 0 or ('configured to refuse ' + option).encode() not in result.stderr:
                    raise ValueError('The daemon did not explicitly refuse ' + option)
                if (base / 'refused-copy').exists() or target_device.read_bytes() != expected:
                    raise ValueError('Refused daemon device operation changed data')
                checks.append('daemon refusal ' + option)
            enabled = f'rsync://127.0.0.1:{server.port}/enabled/'
            run(binary, '--copy-devices', enabled + source_device.name, base / 'daemon-device-copy')
            if (base / 'daemon-device-copy').read_bytes() != data:
                raise ValueError('Enabled daemon device source did not transfer its contents')
            checks.append('daemon device source opt-in')
            for options, operand, contents in ((['--write-devices'], source, updated),
                                                (['--copy-devices', '--write-devices'], source_device, data)):
                check_loop(receiving, target_image)
                identity = device_identity(target_device)
                run(binary, '--checksum', *options, operand, enabled + target_device.name)
                expected = contents + guard[len(data):]
                if device_identity(target_device) != identity or target_device.read_bytes() != expected:
                    raise ValueError('Enabled daemon device write changed its identity or data')
                check_loop(receiving, target_image)
                checks.append('daemon device destination ' + ' '.join(options))
    if source_image.read_bytes() != data or target_image.read_bytes() != expected or readonly_image.read_bytes() != guard:
        raise ValueError('Detached device backing files differ from the verified device data')


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


def copy_account():
    candidates = []
    if os.environ.get('SUDO_USER'):
        candidates.append(pwd.getpwnam(os.environ['SUDO_USER']))
    candidates.extend(account for account in pwd.getpwall() if 1000 <= account.pw_uid < 65534)
    for account in candidates:
        groups = sorted(set(os.getgrouplist(account.pw_name, account.pw_gid)) - {0, account.pw_gid})
        if account.pw_uid and account.pw_gid and groups:
            return account, groups[0]
    raise ValueError('Copy-as checks require a non-root account with a supplementary group')


def copied_as(path, contents, uid, gid):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != (uid, gid)
            or path.read_bytes() != contents):
        raise ValueError('Copy-as did not preserve data or use the requested UID and GID')


def copy_as(binary, root, checks):
    account, extra = copy_account()
    uid, gid, name = account.pw_uid, account.pw_gid, account.pw_name
    group = grp.getgrgid(extra).gr_name
    base = root / 'copy-as'
    base.mkdir(mode=0o755)
    root.chmod(0o755)
    group_source = base / 'group-source'
    group_source.mkdir(mode=0o750)
    os.chown(group_source, 0, extra)
    data = b'supplementary group data\n'
    source = group_source / 'file'
    source.write_bytes(data)
    os.chown(source, 0, extra)
    source.chmod(0o640)
    public = base / 'public'
    public.write_bytes(b'public copy data\n')
    private = base / 'private'
    private.write_bytes(b'root-only copy data\n')
    private.chmod(0o600)
    protected = base / 'protected'
    protected.mkdir(mode=0o700)
    (protected / 'sentinel').write_bytes(b'protected destination\n')
    before = snapshot(protected)
    group_root = base / 'root-group'
    group_root.mkdir(mode=0o750)
    (group_root / 'secret').write_bytes(b'root group data\n')
    (group_root / 'secret').chmod(0o640)

    def destination(label):
        path = base / label
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
        return path

    for label, user in (('named', name), ('numeric', str(uid))):
        target = destination('default-' + label) / 'file'
        run(binary, '-r', '--copy-as=' + user, source, target)
        copied_as(target, data, uid, gid)
        checks.append('copy-as default groups ' + label)
    for index, spec in enumerate((f'{name}:{group}', f'{name}:{extra}', f'{uid}:{group}', f'{uid}:{extra}')):
        target = destination('explicit-' + str(index)) / 'file'
        run(binary, '-r', '--copy-as=' + spec, source, target)
        copied_as(target, data, uid, extra)
    checks.append('copy-as named and numeric users and explicit groups')

    def unused_id(lookup):
        for value in range(60000, 60100):
            try:
                lookup(value)
            except KeyError:
                return value
        raise ValueError('No unused numeric identity for the copy-as fixture')
    anonymous_uid, anonymous_gid = unused_id(pwd.getpwuid), unused_id(grp.getgrgid)
    anonymous = base / 'numeric-only'
    anonymous.mkdir(mode=0o700)
    os.chown(anonymous, anonymous_uid, anonymous_gid)
    run(binary, '-r', f'--copy-as={anonymous_uid}:{anonymous_gid}', public, anonymous / 'file')
    copied_as(anonymous / 'file', public.read_bytes(), anonymous_uid, anonymous_gid)
    result = run(binary, '-r', f'--copy-as={anonymous_uid}', public, anonymous / 'no-group', check=False)
    refused(result, 1, 'getpwuid failed')
    if (anonymous / 'no-group').exists():
        raise ValueError('Copy-as inferred groups for an unknown numeric UID')
    checks.append('copy-as numeric identity without an account')

    denied = destination('denied')
    run(binary, '-r', private, base / 'root-control')
    copied_as(base / 'root-control', private.read_bytes(), 0, 0)
    result = run(binary, '-r', '--copy-as=' + name, private, denied / 'file', check=False)
    refused(result, 23, 'Permission denied')
    if (denied / 'file').exists():
        raise ValueError('Copy-as disclosed a root-only source')
    checks.append('copy-as sender permission refusal')
    result = run(binary, '-r', '--copy-as=' + name, public, protected / 'file', check=False)
    refused(result, 3, 'Permission denied')
    if snapshot(protected) != before:
        raise ValueError('Copy-as changed a root-only destination')
    checks.append('copy-as receiver permission refusal')

    result = run('setpriv', '--groups=0', binary, '-r', f'--copy-as={uid}:{extra}',
                 group_root / 'secret', denied / 'inherited', check=False)
    refused(result, 23, 'Permission denied')
    if (denied / 'inherited').exists():
        raise ValueError('Copy-as retained inherited privileged groups')
    checks.append('copy-as clears inherited groups')
    for option, message in ((f'--copy-as=rsync-missing-{secrets.token_hex(8)}', 'Invalid copy-as user'),
                            (f'--copy-as={name}:rsync-missing-{secrets.token_hex(8)}', 'Invalid copy-as group')):
        result = run(binary, '-r', option, public, denied / 'invalid', check=False)
        refused(result, 1, message)
        if (denied / 'invalid').exists():
            raise ValueError('Invalid copy-as identity wrote destination data')
    checks.append('copy-as invalid identity refusal')

    transport = base / 'transport'
    transport.mkdir(mode=0o700)
    shell = transport / 'lsh.sh'
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'support/lsh.sh', shell)
    rsh = quote_command(['/bin/sh', str(shell)])
    options = ['-r', '-e', rsh, '--rsync-path=' + quote_command([str(binary)])]
    user_target = destination('remote-user')
    result = run('setpriv', f'--reuid={uid}', f'--regid={gid}', '--clear-groups',
                 'test', '-r', shell, check=False)
    if result.returncode != 1:
        raise ValueError('Copy-as transport is not restricted to the privileged launch identity')
    run(binary, *options, '--copy-as=' + name, public, 'lh:' + str(protected / 'local-push'))
    copied_as(protected / 'local-push', public.read_bytes(), 0, 0)
    run(binary, *options, '--copy-as=' + name, 'lh:' + str(private), user_target / 'local-pull')
    copied_as(user_target / 'local-pull', private.read_bytes(), uid, gid)
    checks.append('copy-as local remote-shell scope after privileged connection')
    remote_option = f'-M--copy-as={uid}:{extra}'
    run(binary, *options, remote_option, private, 'lh:' + str(user_target / 'remote-push'))
    copied_as(user_target / 'remote-push', private.read_bytes(), uid, extra)
    run(binary, *options, remote_option, 'lh:' + str(public), protected / 'remote-pull')
    copied_as(protected / 'remote-pull', public.read_bytes(), 0, 0)
    checks.append('copy-as remote-option scope')
    after = snapshot(protected)
    result = run(binary, *options, remote_option, public, 'lh:' + str(protected / 'refused'), check=False)
    refused(result, 3, 'Permission denied')
    result = run(binary, *options, remote_option, 'lh:' + str(private), denied / 'remote-read', check=False)
    refused(result, 23, 'Permission denied')
    if snapshot(protected) != after or (denied / 'remote-read').exists():
        raise ValueError('Remote copy-as bypassed source or destination permissions')
    checks.append('copy-as remote permission refusals')


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
            filesystem_boundaries(binary, root, checks)
            device_transfers(binary, root, checks)
            copy_as(binary, root, checks)
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
