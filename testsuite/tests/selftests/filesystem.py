#!/usr/bin/env python3

import os
from shutil import copy2
from unittest.mock import patch

from harness import TestContext, requires, run
from harness.filesystem import (
    assert_exists, assert_hardlinked, assert_is_symlink, assert_mode, assert_mtime_close,
    assert_not_exists, assert_not_hardlinked, assert_same, make_data_file, make_text_file,
    make_tree, makepath, rmtree, walk_dirs, walk_files, write_text_file,
)
from harness.results import test_fail

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    root = context.scratch / 'filesystem'
    source = root / 'source'
    copy = root / 'copy'
    link = root / 'link'
    symlink = root / 'symlink'
    data = root / 'data'
    makepath(root)
    make_text_file(source, 3)
    copy2(source, copy)
    assert_same(source, copy)
    assert_exists(copy)
    assert_not_exists(root / 'missing')
    os.link(source, link)
    assert_hardlinked(source, link)
    assert_not_hardlinked(source, copy)
    symlink.symlink_to(source.name)
    assert_is_symlink(symlink, source.name)
    source.chmod(0o600)
    assert_mode(source, 0o600)
    assert_mtime_close(source, source.stat().st_mtime)
    for size in (0, 1, 4096, 65536, 65537, 131089):
        make_data_file(data, size)
        if data.stat().st_size != size:
            test_fail(f'make_data_file wrote the wrong size: {size}')
    if data.read_bytes()[:65536] == data.read_bytes()[65536:131072]:
        test_fail('make_data_file repeated its first block')
    try:
        make_data_file(data, -1)
    except ValueError:
        pass
    else:
        test_fail('make_data_file accepted a negative size')
    if data.stat().st_size != 131089:
        test_fail('an invalid size changed an existing data file')
    fixture = write_text_file(root / 'fixture', 'fixture\n', 0o600)
    if fixture.read_text() != 'fixture\n':
        test_fail('write_text_file wrote the wrong content')
    assert_mode(fixture, 0o600)

    dirs, files = make_tree(root / 'tree', depth=2, content_lines=2)
    if set(walk_dirs(root / 'tree')) != set(dirs) or set(walk_files(root / 'tree')) != set(files):
        test_fail('tree traversal does not match make_tree')

    target = root / 'target'
    target.mkdir()
    removable_link = root / 'removable-link'
    removable_link.symlink_to(target.name, target_is_directory=True)
    rmtree(removable_link)
    assert_not_exists(removable_link)
    assert_exists(target)

    removable_file = root / 'removable-file'
    removable_file.write_text('data\n')
    rmtree(removable_file)
    assert_not_exists(removable_file)

    for error in (PermissionError('blocked'), FileNotFoundError('missing child')):
        with patch('harness.filesystem.shutil.rmtree', side_effect=error) as remove:
            try:
                rmtree(target)
            except OSError:
                pass
            else:
                test_fail('directory removal silently ignored a persistent failure')
            if remove.call_count != 8:
                test_fail('directory removal did not retain its bounded retries')
    with patch('harness.filesystem.shutil.rmtree', side_effect=[OSError('busy'), None]) as remove:
        rmtree(target)
        if remove.call_count != 2:
            test_fail('directory removal did not retry a transient failure')

    rmtree(root)
    assert_not_exists(root)

if __name__ == '__main__':
    run(test)
