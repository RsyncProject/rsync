from contextlib import contextmanager
import grp
import json
import os
import re
import subprocess

from .interoperability import MTIME, snapshot, transfer_cases
from . import xattrs

FEATURES = {
    'linux': ('posix', True), 'freebsd': ('posix', True), 'netbsd': (None, False),
    'openbsd': (None, False), 'macos': ('darwin', True), 'solaris': ('posix', True),
    'cygwin': ('posix', True),
}


def command(*args, **kwargs):
    try:
        return subprocess.run(list(map(str, args)), check=True, capture_output=True, timeout=60,
                              env=dict(os.environ, LC_ALL='C'), **kwargs).stdout
    except FileNotFoundError as error:
        raise ValueError(f'{args[0]}: required command not found') from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f'{args[0]}: exceeded 60 seconds') from error
    except subprocess.CalledProcessError as error:
        message = (error.stderr or b'').decode('utf-8', 'replace').strip()[-2048:]
        raise ValueError(f'{args[0]} exited {error.returncode}: {message}') from error


def metadata_cases():
    cases = []
    for source, destination, client, transport in transfer_cases():
        left, right = FEATURES[source], FEATURES[destination]
        kinds = ['xattr']
        if left[1] and source != 'cygwin':
            kinds.append('empty-xattr')
        if not left[0] or not right[0] or left[0] == right[0]:
            kinds.append('acl')
        if left[0] and right[0] and right[1]:
            kinds.append('backup')
        cases.extend((source, destination, client, transport, kind) for kind in kinds)
    return cases


def supported(case):
    source, destination, _, _, kind = case
    if kind == 'acl':
        return bool(FEATURES[source][0] and FEATURES[destination][0])
    if kind in ('xattr', 'empty-xattr'):
        if kind == 'empty-xattr' and destination == 'cygwin':
            return False
        return FEATURES[source][1] and FEATURES[destination][1]
    return kind == 'backup'


def parse_acl(text):
    result = {}
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(':')]
        prefix = 'default:' if fields[0] == 'default' else ''
        if prefix:
            fields = fields[1:]
        if not fields:
            raise ValueError('Empty POSIX ACL entry')
        if len(fields) == 2 and fields[0] in ('mask', 'other'):
            fields.insert(1, '')
        fields[-1] = fields[-1].replace(' ', '')
        if (len(fields) != 3 or fields[0] not in ('user', 'group', 'mask', 'other')
                or not re.fullmatch(r'[r-][w-][x-]', fields[-1])):
            raise ValueError('Unrecognised POSIX ACL entry')
        if fields[0] == 'group' and fields[1] and not fields[1].isdigit():
            fields[1] = str(grp.getgrnam(fields[1]).gr_gid)
        key = prefix + ':'.join(fields[:-1])
        if key in result:
            raise ValueError('Duplicate POSIX ACL entry')
        result[key] = fields[-1]
    if not {'user:', 'group:', 'other:'} <= result.keys():
        raise ValueError('Incomplete POSIX ACL')
    return result


def read_acl(path, role):
    if FEATURES[role][0] == 'darwin':
        text = command('ls', '-lde', path).decode()
        entries = [match[1] for line in text.splitlines()
                   for match in [re.fullmatch(r'\s*\d+: (.+)', line)] if match]
        return entries
    if role == 'solaris':
        text = command('/usr/bin/ls', '-Vd', path).decode()
        entries = [match[1] for line in text.splitlines()
                   for match in [re.fullmatch(r'\s*\d+:(.+)', line)] if match]
        return parse_acl('\n'.join(entries))
    result = parse_acl(command('getfacl', '-n', path).decode())
    if role == 'freebsd' and path.is_dir():
        default = parse_acl(command('getfacl', '-n', '-d', path).decode())
        result.update({'default:' + key: value for key, value in default.items()})
    return result


def set_acl(path, role, phase):
    directory = path.is_dir()
    if FEATURES[role][0] == 'darwin':
        command('chmod', '-N', path)
        command('chmod', '+a', 'user:daemon deny delete,writeattr,writeextattr', path)
        rights = 'read,readattr,readextattr,readsecurity' if phase == 1 else 'read,write,readattr,readextattr,readsecurity'
        if directory:
            rights = rights.replace('read,', 'list,').replace('write,', 'add_file,')
            rights += ',search,file_inherit,directory_inherit'
        command('chmod', '+a', 'user:daemon allow ' + rights, path)
        return
    group = ('r-x' if phase == 1 else 'rwx') if directory else ('r--' if phase == 1 else 'rw-')
    entries = ['user::' + ('rwx' if directory else 'rw-'), 'group::---', 'group:545:' + group,
               'mask::' + group, 'other::---']
    if phase == 1:
        entries.append('group:544:---')
    if directory and role != 'freebsd':
        entries += ['default:' + entry for entry in entries]
    if role == 'solaris':
        entries = [entry.replace('mask::', 'mask:').replace('other::', 'other:') for entry in entries]
        command('/usr/bin/chmod', 'A=' + ','.join(entries), path)
    elif role == 'cygwin':
        command('setfacl', '-s', ','.join(entries), path)
    else:
        command('setfacl', '-b', path)
        if directory:
            command('setfacl', '-k', path)
        command('setfacl', '-m', ','.join(entries), path)
        if directory and role == 'freebsd':
            command('setfacl', '-d', '-m', ','.join(entries), path)


def seed_metadata(root, role, kind, phase=1):
    if phase == 1:
        root.mkdir()
        (root / 'directory').mkdir()
        (root / 'directory' / 'file').write_text('metadata transfer\n')
        root.chmod(0o755)
        (root / 'directory').chmod(0o750)
        (root / 'directory' / 'file').chmod(0o640)
    paths = root / 'directory', root / 'directory' / 'file'
    if kind in ('acl', 'backup') and FEATURES[role][0]:
        for path in paths:
            set_acl(path, role, phase)
    if kind in ('xattr', 'backup') and FEATURES[role][1]:
        for path in paths:
            xattrs.set_value('rsync-ci', 'first' if phase == 1 else 'second', path)
            xattrs.set_value('rsync-ci-large', ('A' if phase == 1 else 'B') * 512, path)
            if phase == 1:
                xattrs.set_value('rsync-ci-remove', 'remove on update', path)
            else:
                xattrs.remove_value('rsync-ci-remove', path)
    if kind == 'empty-xattr':
        for path in paths:
            xattrs.set_value('rsync-ci-empty', '', path)
    for path in paths:
        os.utime(path, (MTIME, MTIME))


def metadata_snapshot(root, role, kind, phase=1):
    result = snapshot(root)
    for name in ('directory', 'directory/file'):
        path = root / name
        if kind in ('acl', 'backup') and FEATURES[role][0]:
            acl = read_acl(path, role)
            if FEATURES[role][0] == 'posix':
                group = ('r-x' if phase == 1 else 'rwx') if path.is_dir() else ('r--' if phase == 1 else 'rw-')
                expected = {'user:': 'rwx' if path.is_dir() else 'rw-', 'group:': '---', 'group:545': group,
                            'mask:': group, 'other:': '---'}
                if phase == 1:
                    expected['group:544'] = '---'
                if path.is_dir():
                    expected.update({'default:' + key: value for key, value in expected.copy().items()})
                if acl != expected:
                    raise ValueError(f'{role}: {name}: POSIX ACL differs: expected {expected!r}, actual {acl!r}')
            else:
                if len(acl) != 2 or not acl[0].startswith('user:daemon deny ') or not acl[1].startswith('user:daemon allow '):
                    raise ValueError('Darwin ACL order or identity differs from the fixture')
                denied = set(acl[0].split(' deny ', 1)[1].split(','))
                allowed = set(acl[1].split(' allow ', 1)[1].split(','))
                expected = {'list' if path.is_dir() else 'read', 'readattr', 'readextattr', 'readsecurity'}
                if phase == 2:
                    expected.add('add_file' if path.is_dir() else 'write')
                if path.is_dir():
                    expected.update(('search', 'file_inherit', 'directory_inherit'))
                if denied != {'delete', 'writeattr', 'writeextattr'} or allowed != expected:
                    raise ValueError('Darwin ACL permissions differ from the fixture')
            result['entries'][name]['acl'] = acl
        if kind in ('xattr', 'backup') and FEATURES[role][1]:
            values = {key: xattrs.get_value(key, path).hex() for key in ('rsync-ci', 'rsync-ci-large')}
            if phase == 1:
                values['rsync-ci-remove'] = xattrs.get_value('rsync-ci-remove', path).hex()
            elif 'rsync-ci-remove' in xattrs.dump_values(path):
                raise ValueError('Removed xattr remains on the destination')
            expected = {'rsync-ci': (b'first' if phase == 1 else b'second').hex(),
                        'rsync-ci-large': ((b'A' if phase == 1 else b'B') * 512).hex()}
            if phase == 1:
                expected['rsync-ci-remove'] = b'remove on update'.hex()
            if values != expected:
                raise ValueError('Translated xattr values differ from the fixture')
            result['entries'][name]['xattrs'] = values
        if kind == 'empty-xattr':
            if xattrs.get_value('rsync-ci-empty', path) != b'':
                raise ValueError('Empty xattr value changed during transfer')
            result['entries'][name]['xattrs'] = {'rsync-ci-empty': ''}
    return result


def qualify(binary, root, role):
    features = json.loads(command(binary, '-VV'))['capabilities']
    acl, extended = FEATURES[role]
    if features['ACLs'] is not bool(acl) or features['xattrs'] is not extended:
        raise ValueError(role + ': binary metadata features differ from the required configuration')
    if acl or extended:
        seed_metadata(root, role, 'backup' if acl else 'xattr')
        metadata_snapshot(root, role, 'backup' if acl else 'xattr')
    return dict(acl=acl, xattrs=extended)


@contextmanager
def metadata_filesystem(root, role):
    data = root / 'data'
    data.mkdir()
    detach = None
    image = None
    mounted = False
    try:
        if role in ('freebsd', 'solaris'):
            image = root / 'metadata.img'
            with image.open('xb') as stream:
                stream.truncate(256 * 1024 * 1024)
            if role == 'freebsd':
                unit = command('mdconfig', '-a', '-t', 'vnode', '-f', image).decode().strip()
                if not re.fullmatch(r'md\d+', unit):
                    raise ValueError('Invalid metadata disk unit')
                detach = ('mdconfig', '-d', '-u', unit[2:])
                device = '/dev/' + unit
                command('newfs', '-U', device)
                command('tunefs', '-a', 'enable', device)
                command('mount', '-t', 'ufs', '-o', 'acls,nosuid', device, data)
            else:
                device = command('lofiadm', '-a', image).decode().strip()
                if not re.fullmatch(r'/dev/lofi/\d+', device):
                    raise ValueError('Invalid metadata loop device')
                detach = ('lofiadm', '-d', device)
                command('newfs', '-m', '0', device.replace('/dev/lofi/', '/dev/rlofi/'), input=b'y\n')
                command('mount', '-F', 'ufs', '-o', 'nosuid', device, data)
            mounted = True
        yield data
    finally:
        try:
            if mounted or detach and os.path.ismount(data):
                command('umount', data)
        finally:
            if detach:
                command(*detach)
            if image:
                image.unlink()
