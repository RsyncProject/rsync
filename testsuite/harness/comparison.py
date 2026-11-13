import filecmp
import os
import shlex
import subprocess
import sys
from pathlib import Path
from . import environment
from .filesystem import acl_get
from .results import test_fail
from .rsync import rsync_argv

def rsync_ls_lR(directory) -> str:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    excluded = {'.git', 'auto-build-save', 'testtmp'}
    entries = ['.']
    def walk_error(error):
        raise error

    for parent, dirs, files in os.walk(directory, onerror=walk_error):
        dirs[:] = [name for name in dirs if name not in excluded]
        for name in [*dirs, *files]:
            if name not in excluded:
                entries.append('./' + (Path(parent) / name).relative_to(directory).as_posix())
    entries.sort(key=os.fsencode)
    command = [str(environment.TOOLDIR / 'tls'), *shlex.split(environment.TLS_ARGS)]
    limit = max(1024, min(32768, os.sysconf('SC_ARG_MAX') // 4))
    output = []
    batch = []
    size = 0

    def listing(paths):
        result = subprocess.run([*command, *paths], capture_output=True, encoding='utf-8',
                                errors='backslashreplace', cwd=directory)
        if result.returncode:
            test_fail(f"metadata listing exited {result.returncode}:\n{result.stderr}")
        return result.stdout

    for name in entries:
        length = len(os.fsencode(name)) + 1
        if batch and size + length > limit:
            output.append(listing(batch))
            batch, size = [], 0
        batch.append(name)
        size += length
    if batch:
        output.append(listing(batch))
    return ''.join(output)

def checkit(args, expected_dir, actual_dir, skip_file_diff: bool = False,
            allowed_codes=(0,)) -> None:
    before = rsync_ls_lR(expected_dir) if '--atimes' in environment.TLS_ARGS else None
    print(f"Running: rsync {' '.join(args)}")
    proc = subprocess.run(rsync_argv(*args))
    if proc.returncode not in allowed_codes:
        test_fail(f"Failed: status={proc.returncode}")
    if not os.path.isdir(actual_dir):
        test_fail(f"Failed: destination directory missing: {actual_dir}")
    verify_dirs(expected_dir, actual_dir, skip_file_diff, expected_listing=before)

def verify_dirs(expected_dir, actual_dir, skip_file_diff: bool = False,
                label: str = '', *, expected_listing=None) -> None:
    expected_dir = str(expected_dir)
    actual_dir = str(actual_dir)
    tag = f"{label}: " if label else ""

    ls_expected = rsync_ls_lR(expected_dir) if expected_listing is None else expected_listing
    ls_actual = rsync_ls_lR(actual_dir)
    if ls_expected != ls_actual:
        ls_expected_path = environment.TMPDIR / 'ls-from'
        ls_actual_path = environment.TMPDIR / 'ls-to'
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

def _acl_sig(path) -> str:
    if environment._SYSTEM == 'Darwin':
        result = subprocess.run(['ls', '-lde', str(path)], capture_output=True, text=True)
        if result.returncode:
            raise OSError(result.stderr.strip() or 'cannot read ACL')
        return ';'.join(line.strip() for line in result.stdout.splitlines()[1:] if line.strip())
    if not (environment._SYSTEM in ('Linux', 'FreeBSD') or environment._CYGWIN):
        return ''
    out = acl_get(path)
    return ';'.join(sorted(l for l in out.splitlines()
                           if l.strip() and not l.startswith('#')))

def _xattr_sig(path) -> str:
    p = str(path)
    if hasattr(os, 'listxattr') and not environment._CYGWIN:
        names = sorted(os.listxattr(p, follow_symlinks=False))
        if environment._SYSTEM == 'Linux':
            names = [name for name in names if name.startswith('user.')]
        out = []
        for name in names:
            value = os.getxattr(p, name, follow_symlinks=False)
            out.append(name + '=' + value.decode('utf-8', 'surrogateescape'))
        return ';'.join(out)
    if environment._CYGWIN:
        d = subprocess.check_output(
            ['getfattr', '--no-dereference', '-d', p],
            text=True, stderr=subprocess.DEVNULL)
        return ';'.join(sorted(l for l in d.splitlines()
                               if l and not l.startswith('# file:')))
    return ''

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
