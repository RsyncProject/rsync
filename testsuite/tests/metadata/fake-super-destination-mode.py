#!/usr/bin/env python3

import os
import shutil
import stat

from harness import TestContext, requires, run
from harness.results import test_fail, test_skipped
from harness.rsync import forced_protocol, rsh_cmd, rsync_path_arg, run_rsync
from harness.xattrs import get_value, is_supported, set_value


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


@requires(features={'xattr_runtime', 'hardlinks'}, transports={'pipe'}, min_peer='4.0.0',
          mutates={'filesystem', 'process'}, tags={'metadata', 'regression'})
def test(context: TestContext):
    if not is_supported():
        test_skipped('fake-super requires xattrs', capability='xattr_runtime')

    source = context.source
    (source / 'dir').mkdir(parents=True)
    (source / 'dir').chmod(0o750)
    original = source / 'dir' / 'entry'
    original.write_bytes(b'backup mode test\n')
    original.chmod(0o640)
    set_value('backup-mode-test', 'kept', original)

    change = '--chmod-dest=g+w,Du-rwx,Fu-rw,a+s'
    same = '--chmod-dest=Du=rwx,Dg=rx,Do=,Fu=rw,Fg=r,Fo='
    shell = ('-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}')
    wire_xattrs = (forced_protocol() or 30) >= 30
    archive = '-aX' if wire_xattrs else '-a'

    for label, options, remote_source, remote_target in (
            ('local', ('-M--fake-super', f'-M{change}'), '', ''),
            ('pull', ('--fake-super', change, *shell), 'lh:', ''),
            ('push', ('-M--fake-super', f'-M{change}', *shell), '', 'lh:')):
        backup = context.scratch / f'{label}-backup'
        restore = context.scratch / f'{label}-restore'
        run_rsync(archive, *options, f'{remote_source}{source}/', f'{remote_target}{backup}/')
        saved = backup / 'dir' / 'entry'
        if mode(saved) != 0o660 or mode(saved.parent) != 0o770:
            test_fail(f'{label}: wrong physical modes: {mode(saved):o}, {mode(saved.parent):o}')
        run_rsync(archive, '--fake-super', '-M--no-super', f'{backup}/', f'{restore}/')
        recovered = restore / 'dir' / 'entry'
        if mode(recovered) != 0o640 or mode(recovered.parent) != 0o750:
            test_fail(f'{label}: original modes were not restored')
        if (recovered.read_bytes() != original.read_bytes()
                or (wire_xattrs and get_value('backup-mode-test', recovered) != b'kept')):
            test_fail(f'{label}: the backup lost file data or an ordinary xattr')

    backup = context.scratch / 'local-backup'
    saved = backup / 'dir' / 'entry'
    transfer = (f'{source}/', f'{backup}/')
    run_rsync('-an', '-M--fake-super', f'-M{same}', *transfer)
    if mode(saved) != 0o660:
        test_fail('dry run changed the saved permissions')

    alias = context.scratch / 'shared-entry'
    os.link(saved, alias)
    result = run_rsync('-a', '-M--fake-super', f'-M{same}', *transfer,
                       check=False, capture_output=True)
    if result.returncode != 23 or mode(alias) != 0o660 or mode(saved) != 0o660:
        test_fail('overriding the mode of a shared inode was not refused')
    alias.unlink()

    run_rsync('-a', '-M--fake-super', f'-M{same}', *transfer)
    if mode(saved) != 0o640 or mode(saved.parent) != 0o750:
        test_fail('an unchanged file or directory did not get its new physical mode')
    restore = context.scratch / 'local-restore'
    shutil.rmtree(restore)
    run_rsync('-a', '--fake-super', '-M--no-super', f'{backup}/', f'{restore}/')
    if mode(restore / 'dir' / 'entry') != 0o640:
        test_fail('changing the physical mode changed the restored mode')

    for refused in ('--inplace', '--partial', '--backup', '--link-dest=../other'):
        result = run_rsync('-a', '--fake-super', same, refused, *transfer,
                           check=False, capture_output=True)
        if result.returncode != 1 or '--chmod-dest' not in result.stderr:
            test_fail(f'expected an explicit refusal for {refused}: {result.stderr}')


if __name__ == '__main__':
    run(test)
