import filecmp
import os
import shutil
import stat
from pathlib import Path

from .results import test_fail

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
