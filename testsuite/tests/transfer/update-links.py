#!/usr/bin/env python3

import errno
import json
import os

from harness import TestContext, requires, run
from harness.filesystem import hardlink_symlinks_supported
from harness.results import test_fail, test_skipped
from harness.rsync import forced_protocol, rsh_cmd, rsync_path_arg, run_rsync


STAMP = 1_600_000_000


def symlink(path, target, seconds, nanoseconds=0):
    path.symlink_to(target)
    stamp = seconds * 1_000_000_000 + nanoseconds
    os.utime(path, ns=(stamp, stamp), follow_symlinks=False)


def check_link(path, target):
    if not path.is_symlink() or os.readlink(path) != target:
        test_fail(f'{path.parent.name}/{path.name}: expected a symlink to {target}')


@requires(features={'remote-shell', 'symlink', 'symlink_timestamps'}, transports={'pipe'},
          min_peer='4.0.0', mutates={'filesystem', 'process'}, tags={'transfer'})
def test(context: TestContext):
    probe = context.scratch / 'timestamp-probe'
    try:
        symlink(probe, 'unneeded', STAMP)
    except NotImplementedError:
        test_skipped('Python cannot set a symbolic link timestamp', capability='symlink_timestamps')
    except OSError as error:
        if error.errno not in (errno.ENOSYS, errno.EOPNOTSUPP):
            raise
        test_skipped('The fixture filesystem cannot set symbolic link timestamps',
                     capability='symlink_timestamps')
    if probe.lstat().st_mtime != STAMP:
        test_fail('the symbolic link timestamp fixture did not retain its time')

    source = context.source
    source.mkdir()
    (source / 'incoming').write_bytes(b'source data\n')
    (source / 'regular').write_bytes(b'incoming regular file\n')
    os.utime(source / 'regular', (STAMP, STAMP))
    for name in ('newer', 'older', 'equal', 'missing', 'type-change'):
        symlink(source / name, 'incoming', STAMP)

    def destination(label):
        target = context.scratch / label
        target.mkdir()
        for name, stamp in (('newer', STAMP + 60), ('older', STAMP - 60), ('equal', STAMP)):
            symlink(target / name, 'existing', stamp)
        (target / 'type-change').write_bytes(b'existing regular file\n')
        (target / 'regular').write_bytes(b'old\n')
        os.utime(target / 'type-change', (STAMP + 60, STAMP + 60))
        os.utime(target / 'regular', (STAMP + 60, STAMP + 60))
        return target

    def transfer(label, options, *, push=False, pull=False):
        target = destination(label)
        shell = ('-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}') if push or pull else ()
        run_rsync('-rlt', *options, *shell,
                  f'{"lh:" if pull else ""}{source}/', f'{"lh:" if push else ""}{target}/')
        return target

    check_link(transfer('default', ()) / 'newer', 'incoming')
    check_link(transfer('regular-update', ('--update',)) / 'newer', 'incoming')
    for label, push, pull in (('local', False, False), ('push', True, False), ('pull', False, True)):
        target = transfer(label, ('--update-links',), push=push, pull=pull)
        check_link(target / 'newer', 'existing')
        for name in ('older', 'equal', 'missing', 'type-change'):
            check_link(target / name, 'incoming')
        if (target / 'regular').read_bytes() != b'incoming regular file\n':
            test_fail(f'{label}: update-links affected regular file selection')

    target = transfer('window', ('--update-links', '--modify-window=120'))
    check_link(target / 'newer', 'incoming')

    target = transfer('copy-links', ('--update-links', '--copy-links'))
    if (target / 'newer').is_symlink() or (target / 'newer').read_bytes() != b'source data\n':
        test_fail('update-links overrode copy-links')

    target = context.scratch / 'no-links'
    run_rsync('-r', '--update-links', f'{source}/', f'{target}/')
    if os.path.lexists(target / 'newer'):
        test_fail('update-links enabled copying symbolic links')

    src, dst = context.scratch / 'subsecond-source', context.scratch / 'subsecond-dest'
    src.mkdir()
    dst.mkdir()
    symlink(src / 'link', 'incoming', STAMP, 100_000_000)
    symlink(dst / 'link', 'existing', STAMP, 900_000_000)
    link_times = [(tree / 'link').lstat().st_mtime_ns for tree in (src, dst)]
    # Some filesystems round both times to one second.
    if link_times[0] < link_times[1] and all(value // 1_000_000_000 == STAMP for value in link_times):
        run_rsync('-rl', '--update-links', '--modify-window=-1', f'{src}/', f'{dst}/')
        protocol = forced_protocol()
        check_link(dst / 'link', 'existing' if protocol is None or protocol >= 31 else 'incoming')

    capabilities = json.loads(run_rsync('-VV', capture_output=True).stdout)['capabilities']
    if capabilities['hardlink_symlinks'] and hardlink_symlinks_supported(context.scratch):
        src, dst = context.scratch / 'hardlink-source', context.scratch / 'hardlink-dest'
        src.mkdir()
        dst.mkdir()
        symlink(src / 'first', 'incoming', STAMP)
        os.link(src / 'first', src / 'second', follow_symlinks=False)
        os.link(src / 'first', src / 'third', follow_symlinks=False)
        symlink(dst / 'first', 'existing', STAMP + 60)
        run_rsync('-rlH', '--update-links', f'{src}/', f'{dst}/')
        check_link(dst / 'first', 'existing')
        check_link(dst / 'second', 'incoming')
        check_link(dst / 'third', 'incoming')
        inodes = [(dst / name).lstat().st_ino for name in ('first', 'second', 'third')]
        if inodes[0] == inodes[1] or inodes[1] != inodes[2]:
            test_fail('the accepted symbolic links did not form their own hard-link group')


if __name__ == '__main__':
    run(test)
