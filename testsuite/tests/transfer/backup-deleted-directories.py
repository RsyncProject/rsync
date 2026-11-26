#!/usr/bin/env python3

import os
import shutil
import stat
import tempfile
from pathlib import Path

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import run_rsync


@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'transfer'})
def test(context: TestContext):
    src = context.source
    src.mkdir()

    def transfer(dst, backup, *options, status=0):
        args = ['-a', '--backup', *options]
        if backup is not None:
            args.append(f'--backup-dir={backup}')
        result = run_rsync(*args, f'{src}/', f'{dst}/', capture_output=True, check=False)
        if result.returncode != status:
            case = dst.relative_to(context.scratch)
            test_fail(f'{case}: expected exit {status}, got {result.returncode}: {result.stderr}')

    for mode in ('--delete-before', '--delete-during', '--delete-delay', '--delete-after'):
        root = context.scratch / mode[2:]
        dst, backup = root / 'dest', root / 'backup'
        outer, inner = dst / 'outer', dst / 'outer' / 'empty'
        inner.mkdir(parents=True)
        (outer / 'data').write_bytes(b'backed up')
        snapshots = {}
        for path, permissions, stamp in ((inner, 0o751, 1_501_234_567),
                                         (outer, 0o510, 1_505_432_109)):
            path.chmod(permissions)
            os.utime(path, (stamp, stamp))
            snapshots[path.relative_to(dst)] = path.stat()
        try:
            transfer(dst, backup, mode)
            if outer.exists() or (backup / 'outer' / 'data').read_bytes() != b'backed up':
                test_fail(f'{mode}: incorrect directory deletion or child backup')
            for name, original in snapshots.items():
                saved = backup / name
                if not saved.is_dir():
                    test_fail(f'{mode}: missing directory backup for {name}')
                actual = saved.stat()
                if (actual.st_mode, actual.st_mtime, actual.st_uid, actual.st_gid) != (
                        original.st_mode, original.st_mtime, original.st_uid, original.st_gid):
                    test_fail(f'{mode}: backup did not preserve metadata for {name}')
        finally:
            for path in (outer, backup / 'outer'):
                if path.is_dir():
                    path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)

    dst = context.scratch / 'protected'
    backup = context.scratch / 'protected-backup'
    kept = dst / 'p' / 'inner' / 'keep'
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b'protected')
    (dst / 'p' / 'sibling').write_bytes(b'deleted')
    transfer(dst, backup, '--delete-during', '--filter=P p/inner/keep')
    if kept.read_bytes() != b'protected' or (backup / 'p' / 'inner').exists():
        test_fail('a protected child or its directory was backed up')
    if (backup / 'p' / 'sibling').read_bytes() != b'deleted':
        test_fail('the unprotected sibling was not backed up')

    collisions = ['file', 'nested-file', 'symlink']
    if os.geteuid() != 0:
        collisions.append('unwritable')
    for collision in collisions:
        root = context.scratch / collision
        dst, backup = root / 'dest', root / 'backup'
        name = 'outer/empty' if collision == 'nested-file' else 'empty'
        empty = dst / name
        empty.mkdir(parents=True)
        empty.chmod(0o551)
        os.utime(empty, (1_501_234_567, 1_501_234_567))
        backup.mkdir()
        if collision in ('file', 'nested-file'):
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            (backup / name).write_bytes(b'occupied')
        elif collision == 'symlink':
            (root / 'other').mkdir()
            (backup / 'empty').symlink_to(root / 'other', target_is_directory=True)
        try:
            if collision == 'unwritable':
                backup.chmod(0o500)
                probe = backup / 'write-probe'
                try:
                    probe.mkdir()
                except PermissionError:
                    pass
                else:
                    probe.rmdir()
                    continue
            transfer(dst, backup, '--delete-during', status=23)
        finally:
            backup.chmod(0o700)
        original = empty.stat()
        if stat.S_IMODE(original.st_mode) != 0o551 or original.st_mtime != 1_501_234_567:
            test_fail(f'{collision}: failed backup changed the original directory')
        if collision == 'symlink':
            if not (backup / 'empty').is_symlink() or list((root / 'other').iterdir()):
                test_fail('failed backup changed the existing symlink or its target')
        elif collision in ('file', 'nested-file') and (backup / name).read_bytes() != b'occupied':
            test_fail(f'{collision}: failed backup overwrote an existing file')

    # As with --group, a non-root backup must skip a group the user cannot
    # grant (setgid shares, BSD/macOS inherited groups) rather than fail.
    groups = {os.getegid(), *os.getgroups()}
    if os.geteuid() == 0:
        foreign = next(g for g in range(54321, 65534) if g not in groups)
        parent, extra = context.scratch / 'foreign-group', ['--no-super']
        parent.mkdir()
    else:
        try:
            parent, extra = Path(tempfile.mkdtemp(prefix='rsync-foreign-', dir='/tmp')), []
        except OSError:
            parent = None
        if parent is not None:
            foreign = parent.stat().st_gid
            if foreign in groups:
                shutil.rmtree(parent)
                parent = None
    if parent is not None:
        try:
            dst, backup = parent / 'dest', context.scratch / 'foreign-group-backup'
            inner = dst / 'old' / 'empty'
            inner.mkdir(parents=True)
            snapshots = {}
            for path in (inner, inner.parent):
                if os.geteuid() == 0:
                    os.chown(path, -1, foreign)
                os.utime(path, (1_501_234_567, 1_501_234_567))
                snapshots[path.relative_to(dst)] = path.stat()
            result = run_rsync('-a', '--backup', '--delete-during', *extra,
                               f'--backup-dir={backup}', f'{src}/', f'{dst}/',
                               capture_output=True, check=False)
            if result.returncode != 0:
                test_fail(f'foreign-group: expected exit 0, got {result.returncode}: {result.stderr}')
            if inner.parent.exists():
                test_fail('foreign-group: directory was not deleted')
            for name, original in snapshots.items():
                actual = (backup / name).stat()
                if (actual.st_mode, actual.st_mtime) != (original.st_mode, original.st_mtime):
                    test_fail(f'foreign-group: backup did not preserve metadata for {name}')
                if actual.st_gid == foreign:
                    test_fail(f'foreign-group: backup of {name} kept an ungrantable group')
        finally:
            shutil.rmtree(parent)

    dst = context.scratch / 'suffix-only'
    (dst / 'empty').mkdir(parents=True)
    transfer(dst, None, '--delete-during')
    if list(dst.iterdir()):
        test_fail('backup suffix mode changed how empty directories are deleted')

    dst, backup = context.scratch / 'with-suffix', context.scratch / 'suffixed-backup'
    empty, sibling = dst / 'entry', dst / 'entry.saved'
    empty.mkdir(parents=True)
    sibling.mkdir()
    (sibling / 'data').write_bytes(b'previous contents')
    empty.chmod(0o700)
    sibling.chmod(0o751)
    os.utime(empty, (1_501_234_567, 1_501_234_567))
    os.utime(sibling, (1_505_432_109, 1_505_432_109))
    transfer(dst, backup, '--delete-during', '--suffix=.saved')
    for name, mode, mtime in (('entry', 0o700, 1_501_234_567),
                              ('entry.saved', 0o751, 1_505_432_109)):
        path = backup / name
        if not path.is_dir():
            test_fail(f'suffix: missing backup directory for {name}')
        actual = path.stat()
        if (stat.S_IMODE(actual.st_mode), actual.st_mtime) != (mode, mtime):
            test_fail(f'suffix: directory metadata does not match {name}')
    if (backup / 'entry.saved' / 'data.saved').read_bytes() != b'previous contents':
        test_fail('suffix: the file did not keep its suffixed backup name')
    if list(dst.iterdir()) or (backup / 'entry.saved.saved').exists():
        test_fail('suffix: incorrect directory deletion or an extra backup directory')

    for first in ('file', 'directory'):
        root = context.scratch / f'suffix-{first}-first'
        dst, backup = root / 'dest', root / 'backup'
        dst.mkdir(parents=True)
        entry, directory = dst / 'entry', dst / 'entry.saved'
        saved = backup / 'entry.saved'
        if first == 'file':
            entry.write_bytes(b'previous file')
        else:
            directory.mkdir()
            directory.chmod(0o751)
        transfer(dst, backup, '--delete-during', '--suffix=.saved')
        if first == 'file':
            directory.mkdir()
            child = directory / 'data'
            child.write_bytes(b'new directory entry')
        else:
            entry.write_bytes(b'new file')
        transfer(dst, backup, '--delete-during', '--suffix=.saved', status=23)
        if first == 'file':
            if saved.read_bytes() != b'previous file' or child.read_bytes() != b'new directory entry':
                test_fail('suffix: creating a directory replaced a previous file backup')
        elif stat.S_IMODE(saved.stat().st_mode) != 0o751 or entry.read_bytes() != b'new file':
            test_fail('suffix: backing up a file replaced a previous directory backup')

    (src / 'entry').write_bytes(b'new contents')
    for case, subdir in (('replace-empty', 'entry'), ('replace-nested', 'entry/empty')):
        root = context.scratch / case
        dst, backup = root / 'dest', root / 'backup'
        (dst / subdir).mkdir(parents=True)
        backup.mkdir()
        (backup / 'entry').write_bytes(b'previous backup')
        transfer(dst, backup, '--force')
        if (dst / 'entry').read_bytes() != b'new contents':
            test_fail(f'{case}: replacement did not reach the destination')
        if (backup / 'entry').read_bytes() != b'previous backup':
            test_fail(f'{case}: directory replacement changed the previous backup')


if __name__ == '__main__':
    run(test)
