from __future__ import annotations

import errno
import filecmp
import functools as _functools
import os
import platform
import re
import shutil
import socket as _socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .daemon import (
    USE_TCP, claim_free_port, claim_ports, require_asan, require_tcp,
    start_rsyncd, start_test_daemon,
)
from .filesystem import (
    allocated_size, assert_exists, assert_hardlinked, assert_is_symlink,
    assert_mode, assert_mtime_close, assert_not_exists, assert_not_hardlinked,
    assert_same, cp_p, is_a_link, make_data_file, make_text_file, make_tree,
    makepath, rmtree, set_supported_mode, walk_dirs, walk_files, write_text_file,
)
from .process import (forced_protocol as _forced_protocol, rsh_cmd as _rsh_cmd,
                      rsync_argv as _rsync_argv, rsync_argv_for as _rsync_argv_for,
                      rsync_command_binary as _rsync_command_binary,
                      rsync_path_arg as _rsync_path_arg, rsync_supports as _rsync_supports,
                      run_rsync as _run_rsync, split_rsync_cmd,
                      under_valgrind as _under_valgrind)
from .results import Exit, test_fail, test_skipped, test_xfail
from .xattrs import (
    RSYNC_PREFIX, RUSR, dump_values as xattr_dump, get_value as xattr_get,
    is_supported as xattrs_supported, set_value as xattr_set,
)

def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(
            f"test harness: required environment variable {name} is not set; "
            "run this test via runtests.py rather than directly.\n"
        )
        sys.exit(Exit.ERROR)
    return v

SCRATCHDIR = Path(_required('scratchdir'))
SRCDIR = Path(_required('srcdir'))
TOOLDIR = Path(_required('TOOLDIR'))
SUITEDIR = Path(os.environ.get('suitedir', SRCDIR / 'testsuite'))

os.umask(0o022)

os.environ['HOME'] = str(SCRATCHDIR)
RSYNC = _required('RSYNC')

RSYNC_PEER = os.environ.get('RSYNC_PEER', RSYNC)

def rsync_command_binary(cmd: str = None) -> str:
    return _rsync_command_binary(RSYNC if cmd is None else cmd)

def under_valgrind():
    return _under_valgrind(RSYNC, RSYNC_PEER)

TLS_ARGS = os.environ.get('TLS_ARGS', '')

all_plus = '+++++++++'
allspace = '         '
dots = '.....'

TMPDIR = SCRATCHDIR
FROMDIR = SCRATCHDIR / 'from'
TODIR = SCRATCHDIR / 'to'
CHKDIR = SCRATCHDIR / 'chk'
CHKFILE = SCRATCHDIR / 'rsync.chk'
OUTFILE = SCRATCHDIR / 'rsync.out'

def rsh_cmd(cmd: str = None, *opts: str) -> str:
    command = str(SRCDIR / 'support' / 'lsh.sh') if cmd is None else cmd
    return _rsh_cmd(command, *opts)

def rsync_path_arg(cmd: str = None) -> str:
    return _rsync_path_arg(RSYNC_PEER if cmd is None else cmd)

def rsync_argv(*args: str) -> list:
    return _rsync_argv(RSYNC, *args)

def rsync_argv_for(binary, *args: str, command: str = None) -> list:
    return _rsync_argv_for(RSYNC if command is None else command, binary, *args)

def rsync_supports(flag: str) -> bool:
    return _rsync_supports(RSYNC, flag)

def forced_protocol():
    return _forced_protocol(RSYNC)

def run_rsync(*args: str, check: bool = True,
              capture_output: bool = False) -> subprocess.CompletedProcess:
    return _run_rsync(RSYNC, *args, check=check, capture_output=capture_output)

def get_testuid() -> int:
    return os.getuid()

def get_rootuid() -> int:
    return 0

def get_rootgid() -> int:
    return 0

def rsync_getgroups() -> list:
    out = subprocess.check_output([str(TOOLDIR / 'getgroups')], text=True)
    return out.split()

_SYSTEM = platform.system()
_CYGWIN = _SYSTEM.startswith('CYGWIN')

def runtest(label: str, fn, *args, **kwargs):
    print(f"Test {label}: ", end="", flush=True)
    fn(*args, **kwargs)
    print("done.")

def cp_touch(src, dst) -> None:
    shutil.copy2(src, dst)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    st = os.stat(dst, follow_symlinks=False)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)

def build_symlinks() -> None:
    FROMDIR.mkdir(parents=True, exist_ok=True)
    (FROMDIR / 'referent').write_text(
        subprocess.check_output(['date'], text=True)
    )
    os.symlink('referent', FROMDIR / 'relative')
    os.symlink(str(FROMDIR / 'referent'), FROMDIR / 'absolute')
    os.symlink('nonexistent', FROMDIR / 'dangling')
    os.symlink(str(SRCDIR / 'rsync.c'), FROMDIR / 'unsafe')

def hands_setup() -> None:
    rmtree(FROMDIR)
    rmtree(TODIR)
    TMPDIR.mkdir(parents=True, exist_ok=True)
    FROMDIR.mkdir(parents=True, exist_ok=True)
    TODIR.mkdir(parents=True, exist_ok=True)

    (FROMDIR / 'empty').touch()
    (FROMDIR / 'emptydir').mkdir(exist_ok=True)

    (FROMDIR / 'filelist').write_text(rsync_ls_lR(SRCDIR))

    (FROMDIR / 'nolf').write_text("This file has no trailing lf")

    old_umask = os.umask(0)
    try:
        os.symlink('nolf', FROMDIR / 'nolf-symlink')
    finally:
        os.umask(old_umask)

    text = bytearray()
    for c in sorted(SRCDIR.glob('*.c')):
        text.extend(c.read_bytes())
    (FROMDIR / 'text').write_bytes(bytes(text))

    (FROMDIR / 'dir').mkdir(exist_ok=True)
    shutil.copy(FROMDIR / 'text', FROMDIR / 'dir')
    (FROMDIR / 'dir' / 'subdir').mkdir(exist_ok=True)
    (FROMDIR / 'dir' / 'subdir' / 'foobar.baz').write_text("some data\n")
    (FROMDIR / 'dir' / 'subdir' / 'subsubdir').mkdir(exist_ok=True)

    make_text_file(FROMDIR / 'dir' / 'subdir' / 'subsubdir' / 'etc-ltr-list', 120)

    (FROMDIR / 'dir' / 'subdir' / 'subsubdir2').mkdir(exist_ok=True)
    make_text_file(FROMDIR / 'dir' / 'subdir' / 'subsubdir2' / 'bin-lt-list', 200)

def rsync_ls_lR(directory) -> str:
    cmd = (
        "find . -name .git -prune -o -name auto-build-save -prune "
        "-o -name testtmp -prune -o -print | sort | sed 's/ /\\\\ /g' | "
        f"xargs '{TOOLDIR}/tls' {TLS_ARGS}"
    )

    proc = subprocess.run(['sh', '-c', cmd], capture_output=True,
                          encoding='utf-8', errors='backslashreplace',
                          cwd=str(directory))
    return proc.stdout

def checkit(args, expected_dir, actual_dir, skip_file_diff: bool = False,
            allowed_codes=(0,)) -> None:
    expected_dir = str(expected_dir)
    actual_dir = str(actual_dir)

    failed = []

    ls_from = None
    if '--atimes' in TLS_ARGS:
        ls_from = rsync_ls_lR(expected_dir)

    print(f"Running: rsync {' '.join(args)}")
    proc = subprocess.run(rsync_argv(*args))
    if proc.returncode not in allowed_codes:
        failed.append(f"status={proc.returncode}")

    if ls_from is None:
        ls_from = rsync_ls_lR(expected_dir)
    ls_to = rsync_ls_lR(actual_dir)

    print("-------------")
    print("check how the directory listings compare with diff:")
    print()
    if ls_from != ls_to:
        ls_from_path = TMPDIR / 'ls-from'
        ls_to_path = TMPDIR / 'ls-to'
        ls_from_path.write_text(ls_from)
        ls_to_path.write_text(ls_to)
        diff = subprocess.run(
            ['diff', '-u', str(ls_from_path), str(ls_to_path)],
            capture_output=True, text=True,
        )
        sys.stdout.write(diff.stdout)
        failed.append("dir-diff")

    print("-------------")
    print("check how the files compare with diff:")
    print()
    if skip_file_diff:
        print("  === Skipping (as directed) ===")
    else:
        diff = subprocess.run(['diff', '-r', '-u', expected_dir, actual_dir])
        if diff.returncode != 0:
            failed.append("file-diff")

    print("-------------")
    if failed:
        test_fail("Failed: " + " ".join(failed))

def verify_dirs(expected_dir, actual_dir, skip_file_diff: bool = False,
                label: str = '') -> None:
    expected_dir = str(expected_dir)
    actual_dir = str(actual_dir)
    tag = f"{label}: " if label else ""

    ls_expected = rsync_ls_lR(expected_dir)
    ls_actual = rsync_ls_lR(actual_dir)
    if ls_expected != ls_actual:
        ls_expected_path = TMPDIR / 'ls-from'
        ls_actual_path = TMPDIR / 'ls-to'
        ls_expected_path.write_text(ls_expected)
        ls_actual_path.write_text(ls_actual)
        diff = subprocess.run(
            ['diff', '-u', str(ls_expected_path), str(ls_actual_path)],
            capture_output=True, text=True,
        )
        sys.stdout.write(diff.stdout)
        test_fail(f"{tag}directory listings differ between "
                  f"{expected_dir} and {actual_dir}")

    if not skip_file_diff:
        diff = subprocess.run(['diff', '-r', '-u', expected_dir, actual_dir])
        if diff.returncode != 0:
            test_fail(f"{tag}file content differs between "
                      f"{expected_dir} and {actual_dir}")

def v_filt(text: str) -> str:
    out = []
    skip_prefix = (
        'building file list ',
        'sending incremental file list',
        'created directory ',
        'total: ',
        'client charset: ',
        'server charset: ',
    )
    for line in text.splitlines():
        if line == '':
            break
        if line.startswith(skip_prefix):
            continue
        if line == 'done':
            continue
        if line.endswith(' --whole-file'):
            continue
        out.append(line)
    return '\n'.join(out) + ('\n' if out else '')

def checkdiff(args, expected: str, *, filter=None, allowed_codes=(0,),
              direct: bool = False) -> None:
    if direct:
        argv = list(args)
        label = ' '.join(argv)
    else:
        argv = rsync_argv(*args)
        label = 'rsync ' + ' '.join(args)
    print(f"Running: {label}")
    proc = subprocess.run(argv, capture_output=True, text=True)
    stdout = proc.stdout
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.stdout.write(stdout)

    failed = []
    if proc.returncode not in allowed_codes:
        failed.append(f"status={proc.returncode}")

    if filter is not None:
        stdout = filter(stdout)

    if stdout != expected:
        from difflib import unified_diff
        diff = unified_diff(
            expected.splitlines(keepends=True),
            stdout.splitlines(keepends=True),
            fromfile='expected', tofile='got',
        )
        sys.stdout.write(''.join(diff))
        failed.append("output differs")

    if failed:
        test_fail("Failed: " + " ".join(failed))

def check_perms(path, expected: str) -> None:
    mode = os.stat(path, follow_symlinks=False).st_mode
    bits = [
        (0o400, 'r'), (0o200, 'w'), (0o100, 'x'),
        (0o040, 'r'), (0o020, 'w'), (0o010, 'x'),
        (0o004, 'r'), (0o002, 'w'), (0o001, 'x'),
    ]
    chars = [c if mode & bit else '-' for bit, c in bits]

    if mode & 0o4000:
        chars[2] = 's' if mode & 0o100 else 'S'
    if mode & 0o2000:
        chars[5] = 's' if mode & 0o010 else 'S'
    if mode & 0o1000:
        chars[8] = 't' if mode & 0o001 else 'T'
    perms = ''.join(chars)
    if perms != expected:
        print(f"permissions: {perms} on {path}")
        print(f"should be:   {expected}")
        test_fail(f"check_perms failed for {path}")

_psf_cache = None

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

def expect_fail(argv, text, env=None, cwd=None):
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env, cwd=cwd)
    out = (proc.stdout or '') + (proc.stderr or '')
    if proc.returncode == 0:
        test_fail(f"command unexpectedly succeeded: {argv!r}\n{out}")
    if text not in out:
        test_fail(f"expected {text!r} in command output:\n{out}")
    return proc

def patched_rrsync(workdir, rsync_path=None):
    import re
    if rsync_path is None:
        rsync_path = shutil.which('true') or '/usr/bin/true'
    src = SRCDIR / 'support' / 'rrsync'
    dst = Path(workdir) / 'rrsync-under-test'

    text, n = re.subn(r"(?m)^RSYNC\s*=.*$",
                      lambda _m: f"RSYNC = {rsync_path!r}",
                      src.read_text())
    if n != 1:
        test_fail(f"patched_rrsync: expected exactly one 'RSYNC =' line in {src}, found {n}")
    dst.write_text(text)
    dst.chmod(0o755)
    return dst

def run_rrsync_denied(command, expected):
    base = SCRATCHDIR / expected.replace(' ', '_').replace('/', '_')
    base.mkdir(parents=True, exist_ok=True)
    restricted = base / 'restricted'
    restricted.mkdir(exist_ok=True)
    rrsync = patched_rrsync(base)
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': command}
    expect_fail([str(rrsync), '-ro', '-no-lock', str(restricted)], expected, env=env)

def build_patched_rsync(name, replacements, append_cflags=None):
    if sys.platform == 'cygwin' or platform.system().startswith('CYGWIN'):
        test_skipped(f"{name}: build_patched_rsync is unreliable on Cygwin "
                     "(prebuilt-object staleness / -fno-common relink); the "
                     "patched-peer fix is validated on the POSIX targets",
                     capability='native_build')
    if not (SRCDIR / 'Makefile').is_file():
        test_skipped(f"{name}: needs a configured rsync source tree with a Makefile",
                     capability='native_build')
    if not shutil.which('make'):
        test_skipped(f"{name}: make(1) not on PATH", capability='native_build')
    if not shutil.which('gcc') and not shutil.which('cc'):
        test_skipped(f"{name}: no C compiler on PATH", capability='native_build')

    work = SCRATCHDIR / name
    rmtree(work)
    shutil.copytree(
        SRCDIR, work, symlinks=True,
        ignore=shutil.ignore_patterns(
            'testtmp', '.git', 'auto-build-save', 'autom4te.cache', '__pycache__'))

    for relpath, old, new in replacements:
        path = work / relpath
        text = path.read_text()
        if old not in text:
            test_skipped(f"{name}: could not find patch target in {relpath}: {old!r}",
                         capability='native_build')
        path.write_text(text.replace(old, new, 1))

        obj = (work / relpath).with_suffix('.o')
        if obj.exists():
            obj.unlink()

    for stale in (work / 'rsync', work / 'rsync.exe'):
        if stale.exists():
            stale.unlink()

    if append_cflags:
        import re
        mkpath = work / 'Makefile'
        mk = mkpath.read_text()
        mk2 = re.sub(r'(?m)^(CFLAGS=.*)$', r'\1 ' + append_cflags, mk, count=1)
        if mk2 == mk:
            test_skipped(f"{name}: could not append {append_cflags!r} to CFLAGS in the Makefile",
                         capability='native_build')
        mkpath.write_text(mk2)

        for obj in work.rglob('*.o'):
            obj.unlink()

    env = {**os.environ, 'CCACHE_DISABLE': '1'}
    build = subprocess.run(['make', '-j2', 'rsync'], cwd=str(work), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    rsync = work / 'rsync'
    if build.returncode != 0 or not rsync.is_file() or not os.access(rsync, os.X_OK):
        test_skipped(
            f"{name}: patched rsync build failed (rc={build.returncode}). "
            "Tail of build output:\n" + '\n'.join(build.stdout.splitlines()[-20:]),
            capability='native_build')
    return rsync

def acls_supported() -> bool:
    vv = run_rsync('-VV', check=True, capture_output=True).stdout
    if '"ACLs": true' not in vv:
        return False
    if _SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN:
        return (shutil.which('setfacl') is not None
                and shutil.which('getfacl') is not None)
    if _SYSTEM == 'Darwin':
        return shutil.which('chmod') is not None
    return False

@_functools.lru_cache(maxsize=1)
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
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
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
    return subprocess.run(['getfacl', str(path)], capture_output=True,
                          text=True, env=_acl_env()).stdout

def _acl_sig(path) -> str:
    if not (_SYSTEM in ('Linux', 'FreeBSD') or _CYGWIN):
        return ''
    try:
        out = acl_get(path)
    except OSError:
        return ''
    return ';'.join(sorted(l for l in out.splitlines()
                           if l.strip() and not l.startswith('#')))

def _xattr_sig(path) -> str:
    p = str(path)
    if _SYSTEM == 'Linux':
        try:
            names = sorted(n for n in os.listxattr(p, follow_symlinks=False)
                           if n.startswith('user.'))
        except OSError:
            return ''
        out = []
        for n in names:
            try:
                v = os.getxattr(p, n, follow_symlinks=False)
            except OSError:
                continue
            out.append(n + '=' + v.decode('utf-8', 'surrogateescape'))
        return ';'.join(out)
    if _CYGWIN:
        try:
            d = subprocess.check_output(
                ['getfattr', '--no-dereference', '-d', p],
                text=True, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, OSError):
            return ''
        return ';'.join(sorted(l for l in d.splitlines()
                               if l and not l.startswith('# file:')))
    return ''

def _variety_fill(path, size: int, key: str) -> None:
    import hashlib
    buf = bytearray()
    i = 0
    while len(buf) < size:
        buf += hashlib.sha256(f'{key}:{i}'.encode()).digest()
        i += 1
    with open(str(path), 'wb') as f:
        f.write(bytes(buf[:size]))

def _all_entries(root) -> list:
    root = Path(root)
    res = []
    for dp, dns, fns in os.walk(root):
        d = Path(dp)
        res.append(d)
        for n in fns:
            res.append(d / n)
        for n in dns:
            sub = d / n
            if sub.is_symlink():
                res.append(sub)
    return res

def make_variety_tree(root, *, depth: int = 8, with_acls=None, with_xattrs=None,
                      with_devices=None, with_owners=None,
                      seed: int = 0x5A17) -> dict:
    root = Path(root)
    rmtree(root)
    if with_xattrs is None:
        with_xattrs = xattrs_supported()
    if with_acls is None:
        with_acls = acls_supported()
    if with_devices is None:
        with_devices = devices_supported()
    if with_owners is None:
        with_owners = owners_supported()

    root.mkdir(parents=True)
    above = root / 'above'
    above.mkdir()
    troot = root / 'transfer_root'
    troot.mkdir()

    counts = {}
    def bump(t):
        counts[t] = counts.get(t, 0) + 1

    perm_cycle = [0o400, 0o640, 0o644, 0o600, 0o755]

    def reg(p, size, mode=0o644):
        _variety_fill(p, size, f'{seed:x}:{os.path.relpath(p, root)}')
        os.chmod(p, mode)
        bump('file')
        return p

    def mkdir1(p, mode=None):
        p.mkdir()
        if mode is not None:
            os.chmod(p, mode)
        bump('dir')
        return p

    def lnk(target, p):
        os.symlink(target, p)
        bump('symlink')
        return p

    above_targets = {}
    above_targets['dir'] = mkdir1(above / 'a_dir')
    reg(above / 'a_dir' / 'inner', 256)
    above_targets['file'] = reg(above / 'a_file', 8192)
    above_targets['fifo'] = above / 'a_fifo'
    make_fifo(above_targets['fifo'])
    bump('fifo')
    above_targets['sock'] = above / 'a_sock'
    make_socket(above_targets['sock'])
    bump('socket')
    if with_devices:
        above_targets['dev'] = above / 'a_dev_c'
        make_device(above_targets['dev'], 'c', 1, 3)
        bump('device')
        make_device(above / 'a_dev_b', 'b', 7, 0)
        bump('device')
    above_targets['link'] = lnk('a_file', above / 'a_link')

    cur = troot
    for n in range(depth):
        reg(cur / f'f{n}', 1024 * (n + 1))
        if n % 2 == 0:
            os.link(cur / f'f{n}', cur / f'hl{n}')
            bump('hardlink')
        reg(cur / f'perm{n}', 700, perm_cycle[(seed + n) % len(perm_cycle)])
        reg(cur / f'setuid{n}', 512, 0o4755)
        mkdir1(cur / f'setgid{n}', 0o2775)
        mkdir1(cur / f'sticky{n}', 0o1777)
        for k in range(3):
            reg(cur / f'g{n}_{k}', 300)
        make_fifo(cur / f'fifo{n}')
        bump('fifo')
        make_socket(cur / f'sk{n}')
        bump('socket')
        if with_devices:
            make_device(cur / f'cdev{n}', 'c', 1, 5)
            bump('device')
            make_device(cur / f'bdev{n}', 'b', 7, n)
            bump('device')

        lnk(f'f{n}', cur / f'ln2file{n}')
        lnk(f'fifo{n}', cur / f'ln2fifo{n}')
        lnk(f'sk{n}', cur / f'ln2sock{n}')
        if with_devices:
            lnk(f'cdev{n}', cur / f'ln2dev{n}')
        lnk(f'ln2file{n}', cur / f'ln2ln{n}')
        lnk(f'nonexistent_{n}', cur / f'dangling{n}')
        if n < depth - 1:
            nxt = mkdir1(cur / f'd{n + 1}')
            lnk(f'd{n + 1}', cur / f'ln2dir{n}')
            cur = nxt

    absd = mkdir1(troot / 'abs_links')
    lnk(str((troot / 'f0').resolve()), absd / 'abs_file')
    lnk(str(above_targets['file'].resolve()), absd / 'abs_above')

    escd = mkdir1(troot / 'escape')
    lnk('../../above/a_dir', escd / 'esc_dir')
    lnk('../../above/a_file', escd / 'esc_file')
    lnk('../../above/a_fifo', escd / 'esc_fifo')
    lnk('../../above/a_sock', escd / 'esc_sock')
    if with_devices:
        lnk('../../above/a_dev_c', escd / 'esc_dev')
    lnk('../../above/a_link', escd / 'esc_link')

    lnk(f'../../../{root.name}/above/a_file', escd / 'esc_deep')

    if with_xattrs:
        for p in walk_dirs(troot) + walk_files(troot):
            try:
                xattr_set('variety', os.path.basename(str(p)), p)
            except OSError:
                pass

    if with_acls:
        dirs = walk_dirs(troot)
        files = walk_files(troot)
        for i, d in enumerate(dirs):
            if i % 3 == 0:
                acl_set('u:0:rwx', d)
            if i % 5 == 0:
                acl_set('d:u:0:rwx', d)
        for i, f in enumerate(files):
            if i % 4 == 0:
                acl_set('g:0:r-x', f)

    entries = sorted(_all_entries(root), key=lambda x: str(x))

    if with_owners:
        idset = [(0, 0), (1, 1), (2, 2)]
        for i, p in enumerate(entries):
            uid, gid = idset[(seed + i) % len(idset)]
            try:
                os.chown(str(p), uid, gid, follow_symlinks=False)
            except OSError:
                pass

    base = 1_000_000_000
    for i, p in enumerate(entries):
        t = base + (i * 7) % 1_000_000
        try:
            os.utime(str(p), (t, t), follow_symlinks=False)
        except (OSError, NotImplementedError, ValueError):
            if not os.path.islink(str(p)):
                try:
                    os.utime(str(p), (t, t))
                except OSError:
                    pass

    return {'transfer_root': troot, 'above_targets': above_targets,
            'counts': counts}

def _rel_nonlink_entries(root) -> list:
    root = Path(root)
    res = []
    for dirpath, _dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        if d != root:
            res.append(d.relative_to(root))
        for fn in filenames:
            fp = d / fn
            if not fp.is_symlink():
                res.append(fp.relative_to(root))
    return sorted(res, key=lambda p: str(p))

def _safe_walk_files(root) -> list:
    root = Path(root)
    res = []
    for dp, _dns, fns in os.walk(root):
        d = Path(dp)
        for n in fns:
            p = d / n
            try:
                if p.is_file() and not p.is_symlink():
                    res.append(p)
            except OSError:
                continue
    return sorted(res, key=lambda x: str(x))

def compare_trees(a, b, label: str = '', *,
                          with_acls: bool = True,
                          with_xattrs: bool = True) -> list:
    a = Path(a)
    b = Path(b)
    pre = f"{label}: " if label else ""
    diffs = []

    la = rsync_ls_lR(a)
    lb = rsync_ls_lR(b)
    if la != lb:
        import difflib
        ud = ''.join(difflib.unified_diff(
            la.splitlines(keepends=True), lb.splitlines(keepends=True),
            fromfile=f'{a} (tls)', tofile=f'{b} (tls)'))
        diffs.append(f"{pre}tls listings differ:\n{ud}")

    files_a = sorted(p.relative_to(a) for p in _safe_walk_files(a))
    files_b = sorted(p.relative_to(b) for p in _safe_walk_files(b))
    set_b = set(files_b)
    if set(files_a) != set_b:
        only_a = sorted(str(p) for p in set(files_a) - set_b)
        only_b = sorted(str(p) for p in set_b - set(files_a))
        diffs.append(f"{pre}regular-file set differs: "
                     f"only in a={only_a} only in b={only_b}")
    for rel in files_a:
        if rel not in set_b:
            continue
        try:
            same = filecmp.cmp(str(a / rel), str(b / rel), shallow=False)
        except OSError as e:
            diffs.append(f"{pre}cannot compare contents of {rel} "
                         f"(permission denied?): {e}")
            continue
        if not same:
            diffs.append(f"{pre}content differs: {rel}")

    def _hl_groups(rootp):
        from collections import defaultdict
        ino = defaultdict(list)
        for p in _safe_walk_files(rootp):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_nlink > 1:
                ino[(st.st_dev, st.st_ino)].append(str(p.relative_to(rootp)))
        return sorted(tuple(sorted(v)) for v in ino.values() if len(v) > 1)
    ga, gb = _hl_groups(a), _hl_groups(b)
    if ga != gb:
        diffs.append(f"{pre}hard-link grouping differs: a={ga} b={gb}")

    if with_xattrs or with_acls:
        for rel in _rel_nonlink_entries(a):
            pa = a / rel
            pb = b / rel
            if not pb.exists():
                continue
            if with_xattrs:
                xa, xb = _xattr_sig(pa), _xattr_sig(pb)
                if xa != xb:
                    diffs.append(f"{pre}xattr differs: {rel} "
                                 f"(a={xa!r} b={xb!r})")
            if with_acls:
                aa, ab = _acl_sig(pa), _acl_sig(pb)
                if aa != ab:
                    diffs.append(f"{pre}ACL differs: {rel} "
                                 f"(a={aa!r} b={ab!r})")

    return diffs

def assert_trees_equal(a, b, label: str = '', **kwargs) -> None:
    diffs = compare_trees(a, b, label, **kwargs)
    if diffs:
        test_fail('\n'.join(diffs))
