#!/usr/bin/env python3

import os

from harness import (TestContext, assert_exists, assert_hardlinked, assert_is_symlink, assert_mode,
                     assert_mtime_close, assert_not_exists, assert_not_hardlinked, assert_same, cp_p,
                     is_a_link, make_data_file, make_text_file, make_tree, makepath, requires, rmtree,
                     run, test_fail, walk_dirs, walk_files, write_text_file)

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
    cp_p(source, copy)
    assert_same(source, copy)
    assert_exists(copy)
    assert_not_exists(root / 'missing')
    os.link(source, link)
    assert_hardlinked(source, link)
    assert_not_hardlinked(source, copy)
    symlink.symlink_to(source.name)
    assert_is_symlink(symlink, source.name)
    if not is_a_link(symlink):
        test_fail('is_a_link rejected a symlink')
    source.chmod(0o600)
    assert_mode(source, 0o600)
    assert_mtime_close(source, source.stat().st_mtime)
    make_data_file(data, 4096)
    if data.stat().st_size != 4096:
        test_fail('make_data_file wrote the wrong size')
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

    rmtree(root)
    assert_not_exists(root)

if __name__ == '__main__':
    run(test)
