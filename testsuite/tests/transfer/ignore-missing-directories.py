#!/usr/bin/env python3

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.filesystem import assert_same
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_path_arg, run_rsync


DIRECTORIES_ONLY = '--ignore-non-existing-directory'


@requires(features={'daemon', 'remote-shell'}, transports={'pipe', 'tcp'},
          mutates={'environment', 'filesystem', 'process', 'socket'}, tags={'transfer'})
def test(context: TestContext):
    source = context.source
    for directory in ('present/new-child', 'absent', 'blocked'):
        (source / directory).mkdir(parents=True)
    for name in ('present/update', 'present/new', 'present/new-child/item',
                 'absent/item', 'blocked/item'):
        (source / name).write_text(f'{name}\n')

    def destination(name):
        path = context.scratch / name
        (path / 'present').mkdir(parents=True)
        (path / 'present' / 'update').write_text('old\n')
        (path / 'present' / 'extra').write_text('extra\n')
        (path / 'blocked').write_text('keep this file\n')
        return path

    def transfer(label, *args):
        result = run_rsync('-rt', *args, capture_output=True, check=False)
        if result.returncode:
            test_fail(f'{label}: transfer failed with exit {result.returncode}: {result.stderr}')

    def check_missing(path):
        for name in ('absent', 'present/new-child'):
            if (path / name).exists():
                test_fail(f'{path.name}: unexpectedly created {name}')
        if not (path / 'blocked').is_file() or (path / 'blocked').read_text() != 'keep this file\n':
            test_fail(f'{path.name}: replaced the existing non-directory entry')

    def check(path, *, deleted=False, existing=False):
        assert_same(source / 'present' / 'update', path / 'present' / 'update', label='updated file')
        if existing:
            if (path / 'present' / 'new').exists():
                test_fail(f'{path.name}: --existing allowed a new file')
        else:
            assert_same(source / 'present' / 'new', path / 'present' / 'new', label='new file')
        if (path / 'present' / 'extra').exists() == deleted:
            test_fail(f'{path.name}: wrong deletion result in an existing directory')
        check_missing(path)

    for label, options in (
        ('local', []),
        ('delete-during', ['--delete']),
        ('delete-before', ['--delete-before']),
        ('existing', ['--existing']),
    ):
        target = destination(label)
        transfer(label, DIRECTORIES_ONLY, *options, f'{source}/', f'{target}/')
        check(target, deleted=label.startswith('delete-'), existing=label == 'existing')

    for label, extra in (('relative', []), ('no-implied-dirs', ['--no-implied-dirs'])):
        target = destination(label)
        transfer(label, DIRECTORIES_ONLY, '--relative', *extra, f'{source}/./present/update',
                 f'{source}/./absent/item', f'{source}/./blocked/item', f'{target}/')
        assert_same(source / 'present' / 'update', target / 'present' / 'update', label=label)
        check_missing(target)

    for label, sender, receiver in (('push', '', 'lh:'), ('pull', 'lh:', '')):
        target = destination(label)
        transfer(label, DIRECTORIES_ONLY, '-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}',
                 f'{sender}{source}/', f'{receiver}{target}/')
        check(target)

    target = destination('daemon')
    config = write_daemon_conf([('files', {'path': str(target), 'read only': 'no', 'use chroot': 'no'})])
    url = start_test_daemon(config, 12985)
    transfer('daemon', DIRECTORIES_ONLY, f'{source}/', f'{url}files/')
    check(target)


if __name__ == '__main__':
    run(test)
