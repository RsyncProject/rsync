import errno
import filecmp
import functools
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from .results import test_fail

_SYSTEM = platform.system()
_CYGWIN = _SYSTEM.startswith('CYGWIN')

def makepath(*paths) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)

def rmtree(path) -> None:
    path = Path(path)
    for _ in range(8):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        try:
            if stat.S_ISDIR(mode):
                shutil.rmtree(path)
            else:
                path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            continue

def is_a_link(path) -> bool:
    return os.path.islink(path)

def cp_p(source, destination) -> None:
    shutil.copy2(source, destination)

def allocated_size(path) -> int:
    return os.stat(path).st_blocks * 512

def set_supported_mode(path, modes) -> None:
    for mode in modes:
        try:
            os.chmod(path, mode)
            return
        except PermissionError:
            pass
    os.chmod(path, modes[-1])

def make_data_file(path, size: int) -> None:
    path = str(path)
    if os.path.exists('/dev/urandom'):
        try:
            with open('/dev/urandom', 'rb') as source, open(path, 'wb') as destination:
                remaining = size
                while remaining:
                    chunk = source.read(min(remaining, 1 << 16))
                    if not chunk:
                        break
                    destination.write(chunk)
                    remaining -= len(chunk)
            if remaining == 0:
                return
        except OSError:
            pass

    path_seed = int.from_bytes(path.encode(), 'big') & 0xFFFFFFFF
    state = (os.getpid() + path_seed) % 2147483648
    with open(path, 'wb') as output:
        data = bytearray(size)
        for index in range(size):
            state = (state * 1103515245 + 12345) % 2147483648
            data[index] = ((state >> 16) % 94) + 33
        output.write(bytes(data))

def make_text_file(path, lines: int = 100) -> None:
    content = ''.join(
        'line %06d  the quick brown fox jumps over the lazy dog  %d %d\n'
        % (index, (index * 31) % 97, (index * 131) % 89)
        for index in range(1, lines + 1)
    )
    with open(str(path), 'w') as output:
        output.write(content)

def write_text_file(path, content, mode=None):
    path = Path(path)
    path.write_text(content)
    if mode is not None:
        path.chmod(mode)
    return path

def make_tree(root, depth: int = 3, *, data: bool = False,
              content_lines: int = 20, data_size: int = 4096,
              dirname: str = 'd', leaf: str = 'f'):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dirs = []
    files = []
    current = root
    for level in range(depth + 1):
        path = current / f'{leaf}{level}'
        if data:
            make_data_file(path, data_size)
        else:
            make_text_file(path, content_lines)
        files.append(path)
        if level < depth:
            current = current / f'{dirname}{level + 1}'
            current.mkdir(exist_ok=True)
            dirs.append(current)
    return dirs, files

def walk_files(root) -> list:
    root = Path(root)
    return sorted(path for path in root.rglob('*') if path.is_file() and not path.is_symlink())

def walk_dirs(root) -> list:
    root = Path(root)
    return sorted(path for path in root.rglob('*') if path.is_dir() and not path.is_symlink())

def _tag(label: str) -> str:
    return f'{label}: ' if label else ''

def assert_same(first, second, label: str = '') -> None:
    if not filecmp.cmp(str(first), str(second), shallow=False):
        test_fail(f'{_tag(label)}content differs between {first} and {second}')

def assert_mode(path, expected_octal: int, label: str = '') -> None:
    mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    if mode != expected_octal:
        test_fail(f'{_tag(label)}mode {mode:04o} != expected {expected_octal:04o} on {path}')

def assert_mtime_close(first, second, tol: float = 1.0, label: str = '') -> None:
    first_mtime = os.stat(first, follow_symlinks=False).st_mtime
    second_mtime = second if isinstance(second, (int, float)) else os.stat(
        second, follow_symlinks=False).st_mtime
    if abs(first_mtime - second_mtime) > tol:
        test_fail(f'{_tag(label)}mtime {first_mtime} vs {second_mtime} differ by > {tol}s (checking {first})')

def assert_is_symlink(path, target: str = None, label: str = '') -> None:
    if not os.path.islink(path):
        test_fail(f'{_tag(label)}{path} is not a symlink')
    if target is not None:
        actual = os.readlink(path)
        if actual != target:
            test_fail(f'{_tag(label)}{path} -> {actual!r}, expected {target!r}')

def assert_hardlinked(first, second, label: str = '') -> None:
    first_stat = os.stat(first, follow_symlinks=False)
    second_stat = os.stat(second, follow_symlinks=False)
    if (first_stat.st_dev, first_stat.st_ino) != (second_stat.st_dev, second_stat.st_ino):
        test_fail(f'{_tag(label)}{first} and {second} are not hard-linked '
                  f'(ino {first_stat.st_ino} vs {second_stat.st_ino})')

def assert_not_hardlinked(first, second, label: str = '') -> None:
    first_stat = os.stat(first, follow_symlinks=False)
    second_stat = os.stat(second, follow_symlinks=False)
    if (first_stat.st_dev, first_stat.st_ino) == (second_stat.st_dev, second_stat.st_ino):
        test_fail(f'{_tag(label)}{first} and {second} unexpectedly share inode {first_stat.st_ino}')

def assert_exists(path, label: str = '') -> None:
    if not os.path.lexists(path):
        test_fail(f'{_tag(label)}{path} does not exist')

def assert_not_exists(path, label: str = '') -> None:
    if os.path.lexists(path):
        test_fail(f'{_tag(label)}{path} exists but should not')

_psf_cache = None

def cp_touch(src, dst) -> None:
    shutil.copy2(src, dst)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    st = os.stat(dst, follow_symlinks=False)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)

def proc_self_fd_pins() -> bool:
    global _psf_cache
    if _psf_cache is not None:
        return _psf_cache
    if not sys.platform.startswith(('linux', 'android')):
        _psf_cache = False
        return _psf_cache

    def resolves(path):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return False
        try:
            return os.readlink('/proc/self/fd/%d' % fd) == os.path.realpath(path)
        except OSError:
            return False
        finally:
            os.close(fd)

    _psf_cache = resolves('/') and resolves(os.path.realpath(__file__))
    return _psf_cache

def acls_supported() -> bool:
    from .rsync import run_rsync
    vv = run_rsync('-VV', check=True, capture_output=True).stdout
    if '"ACLs": true' not in vv:
        return False
    if _SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN:
        return (shutil.which('setfacl') is not None
                and shutil.which('getfacl') is not None)
    if _SYSTEM == 'Darwin':
        return shutil.which('chmod') is not None
    return False

@functools.lru_cache(maxsize=1)
def devices_supported() -> bool:
    if os.geteuid() != 0 or not hasattr(os, 'mknod'):
        return False
    with tempfile.TemporaryDirectory(prefix='rsync-devprobe.') as d:
        try:
            os.mknod(os.path.join(d, 'p'), 0o600 | stat.S_IFCHR, os.makedev(1, 3))
        except (PermissionError, OSError):
            return False
    return True

def hardlink_symlinks_supported(where=None) -> bool:
    d = tempfile.mkdtemp(prefix='rsync-hlsym.', dir=str(where) if where else None)
    try:
        link, hard = os.path.join(d, 's'), os.path.join(d, 'h')
        os.symlink('target-need-not-exist', link)
        try:
            os.link(link, hard, follow_symlinks=False)
        except NotImplementedError:
            return False
        except OSError as e:
            if e.errno in (errno.ENOTSUP, getattr(errno, 'EOPNOTSUPP', errno.ENOTSUP)):
                return False
            raise
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)

def owners_supported() -> bool:
    return os.geteuid() == 0

def make_fifo(path) -> None:
    os.mkfifo(str(path))

def make_socket(path) -> None:
    path = Path(path)
    old = os.getcwd()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.chdir(path.parent)
        s.bind(path.name)
    finally:
        s.close()
        os.chdir(old)

def make_device(path, kind: str, major: int, minor: int,
                mode: int = 0o644) -> None:
    fmt = stat.S_IFCHR if kind == 'c' else stat.S_IFBLK
    os.mknod(str(path), mode | fmt, os.makedev(major, minor))

def _acl_env() -> dict:
    env = dict(os.environ)
    env.pop('POSIXLY_CORRECT', None)
    env['LC_ALL'] = 'C'
    return env

def acl_set(spec: str, path) -> bool:
    if not (_SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN):
        return False
    proc = subprocess.run(['setfacl', '-m', spec, str(path)],
                          capture_output=True, text=True, env=_acl_env())
    return proc.returncode == 0

def acl_get(path) -> str:
    return subprocess.run(['getfacl', str(path)], capture_output=True, check=True,
                          text=True, env=_acl_env()).stdout
