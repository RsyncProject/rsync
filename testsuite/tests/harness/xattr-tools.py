#!/usr/bin/env python3

from types import SimpleNamespace
from unittest.mock import patch

from harness import TestContext, requires, run, test_fail
from harness import xattrs

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    for name in ('user.marker', 'system.marker'):
        try:
            xattrs._full_name(name)
        except ValueError:
            pass
        else:
            test_fail('an xattr namespace prefix was accepted as a relative name')
    for linux, expected in ((True, 'user.rsync.marker'), (False, 'rsync.marker')):
        with patch.object(xattrs, 'LINUX_NAMESPACE', linux):
            if xattrs._full_name('rsync.marker') != expected:
                test_fail('the user namespace was applied incorrectly')
    path = context.scratch / 'file'
    for system, write, read, value in (
        ('FreeBSD', ['setextattr', '-h', 'user', 'rsync.marker', 'value', str(path)],
         ['getextattr', '-qqh', 'user', 'rsync.marker', str(path)], b'value'),
        ('Darwin', ['xattr', '-w', 'rsync.marker', 'value', str(path)],
         ['xattr', '-px', 'rsync.marker', str(path)], b'76616c7565'),
    ):
        with patch.object(xattrs, 'SYSTEM', system), patch.object(xattrs, 'CYGWIN', False), \
                patch.object(xattrs, 'LINUX_NAMESPACE', False), patch.object(xattrs, '_run') as tool:
            xattrs.set_value('rsync.marker', 'value', path)
            tool.assert_called_once_with(write, text=True)
            tool.reset_mock()
            tool.return_value = SimpleNamespace(stdout=value)
            if xattrs.get_value('rsync.marker', path) != b'value':
                test_fail(f'{system} changed the xattr value')
            tool.assert_called_once_with(read)

if __name__ == '__main__':
    run(test)
