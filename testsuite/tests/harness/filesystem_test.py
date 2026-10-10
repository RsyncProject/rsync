#!/usr/bin/env python3

import rsyncfns
from harness import (TestContext, assert_exists, assert_hardlinked, assert_is_symlink, assert_mode,
                     assert_mtime_close, assert_not_exists, assert_not_hardlinked, assert_same, cp_p,
                     is_a_link, make_data_file, make_text_file, make_tree, makepath, requires, rmtree,
                     run, test_fail, walk_dirs, walk_files)


@requires(protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe'}, min_peer='2.6.0', tags={'harness'})
def test(context: TestContext):
    helpers = (
        ('assert_exists', assert_exists), ('assert_hardlinked', assert_hardlinked),
        ('assert_is_symlink', assert_is_symlink), ('assert_mode', assert_mode),
        ('assert_mtime_close', assert_mtime_close), ('assert_not_exists', assert_not_exists),
        ('assert_not_hardlinked', assert_not_hardlinked), ('assert_same', assert_same),
        ('cp_p', cp_p), ('is_a_link', is_a_link), ('make_data_file', make_data_file),
        ('make_text_file', make_text_file), ('make_tree', make_tree), ('makepath', makepath),
        ('rmtree', rmtree), ('walk_dirs', walk_dirs), ('walk_files', walk_files),
    )
    for name, helper in helpers:
        if getattr(rsyncfns, name) is not helper:
            test_fail(f'{name} is not exported through rsyncfns')

    root = context.scratch / 'filesystem'
    source = root / 'source'
    copy = root / 'copy'
    data = root / 'data'
    makepath(root)
    make_text_file(source, 3)
    cp_p(source, copy)
    assert_same(source, copy)
    assert_exists(copy)
    assert_not_exists(root / 'missing')
    make_data_file(data, 4096)
    if data.stat().st_size != 4096:
        test_fail('make_data_file wrote the wrong size')

    dirs, files = make_tree(root / 'tree', depth=2, content_lines=2)
    if set(walk_dirs(root / 'tree')) != set(dirs) or set(walk_files(root / 'tree')) != set(files):
        test_fail('tree traversal does not match make_tree')

    rmtree(root)
    assert_not_exists(root)


if __name__ == '__main__':
    run(test)
