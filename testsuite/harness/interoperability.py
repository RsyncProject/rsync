import os
import platform
import stat
from .process import file_digest

PLATFORMS = {'linux': 'Linux', 'freebsd': 'FreeBSD', 'netbsd': 'NetBSD', 'openbsd': 'OpenBSD',
             'macos': 'Darwin', 'solaris': 'SunOS', 'cygwin': 'CYGWIN'}
MTIME = 1600000000


def transfer_cases():
    return [(source, destination, client, transport)
            for source in PLATFORMS for destination in PLATFORMS if source != destination
            for client in ('push', 'pull') for transport in ('ssh', 'daemon')]


def endpoint_identity(binary, expected=None):
    system = platform.system()
    kind = next((name for name, value in PLATFORMS.items() if system == value or name == 'cygwin' and system.startswith('CYGWIN_')), None)
    if kind is None or expected is not None and kind != expected:
        raise ValueError('Transfer endpoint is not the expected operating system')
    return dict(platform=kind, system=system, release=platform.release(), architecture=platform.machine(),
                uid=os.geteuid(), gid=os.getegid(), binary_sha256=file_digest(binary))


def seed(root):
    root.mkdir()
    (root / 'nested').mkdir()
    (root / 'empty').mkdir()
    (root / 'nested' / 'payload').write_bytes(bytes(range(256)) * 4096)
    os.link(root / 'nested' / 'payload', root / 'hardlink')
    (root / 'space name').write_text('first revision\n')
    (root / 'obsolete').write_text('remove on update\n')
    (root / 'relative-link').symlink_to('nested/payload')
    (root / 'dangling-link').symlink_to('absent')
    for path in sorted(root.rglob('*'), reverse=True):
        if not path.is_symlink():
            path.chmod(0o750 if path.is_dir() else 0o640)
            os.utime(path, (MTIME, MTIME))


def update(root):
    with (root / 'nested' / 'payload').open('r+b') as stream:
        stream.seek(32768)
        stream.write(b'second revision'.ljust(32, b'.'))
    os.utime(root / 'nested' / 'payload', (MTIME, MTIME))
    (root / 'obsolete').unlink()
    (root / 'new file').write_text('second revision\n')
    (root / 'new file').chmod(0o640)
    os.utime(root / 'new file', (MTIME, MTIME))


def snapshot(root):
    entries, links = {}, {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if stat.S_ISLNK(info.st_mode):
            entries[name] = dict(type='symlink', target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            entries[name] = dict(type='directory', mode=stat.S_IMODE(info.st_mode), mtime=int(info.st_mtime))
        elif stat.S_ISREG(info.st_mode):
            entries[name] = dict(type='file', mode=stat.S_IMODE(info.st_mode), mtime=int(info.st_mtime),
                                 size=info.st_size, sha256=file_digest(path))
            links.setdefault((info.st_dev, info.st_ino), []).append(name)
        else:
            raise ValueError('Unexpected object in the transfer fixture')
    return dict(entries=entries, hardlinks=sorted(sorted(names) for names in links.values() if len(names) > 1))
