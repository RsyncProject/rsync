#!/usr/bin/env python3

from harness import TestContext, requires, run
from harness.filesystem import acl_get, acl_set
from harness.results import test_fail
from harness.rsync import run_rsync
from harness.xattrs import dump_values, get_value, set_value


@requires(features={'backup_metadata', 'linux_acl'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'metadata'})
def test(context: TestContext):
    dest, backup = context.scratch / 'dest', context.scratch / 'backup'
    context.source.mkdir()
    old = dest / 'old'
    old.mkdir(parents=True)
    if not acl_set('u:60003:r-x', old):
        test_fail('cannot set directory ACL')
    value = 'data' * 500
    set_value('backup-directory', value, old)
    saved = backup / 'old'
    saved.mkdir(parents=True)
    set_value('backup-directory', 'previous value', saved)
    set_value('backup-stale', 'previous snapshot', saved)
    set_value('backup-excluded', 'keep this', saved)
    set_value('backup-excluded', 'do not copy this', old)
    run_rsync('-aAX', '--delete', '--backup', f'--backup-dir={backup}',
              '--filter=-x user.backup-excluded',
              f'{context.source}/', f'{dest}/')
    if old.exists():
        test_fail('backed-up directory is still present at the destination')
    if not saved.is_dir():
        test_fail('the directory backup is missing')
    if 'user:60003:r-x' not in acl_get(saved):
        test_fail('the directory backup lost its access ACL')
    if get_value('backup-directory', saved) != value.encode():
        test_fail('the directory backup lost the large extended attribute')
    if get_value('backup-excluded', saved) != b'keep this':
        test_fail('the directory backup changed an excluded extended attribute')
    if 'user.backup-stale=' in dump_values(saved):
        test_fail('the directory backup retained a stale extended attribute')


if __name__ == '__main__':
    run(test)
