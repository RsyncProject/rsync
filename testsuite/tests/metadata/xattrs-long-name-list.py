#!/usr/bin/env python3

from harness import metadata
from harness.environment import FROMDIR, TODIR
from harness.filesystem import assert_same, makepath
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness.xattrs import get_value, is_supported, set_value

metadata(features={'xattr_runtime'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'metadata', 'regression'})

if not is_supported():
    test_skipped('rsync has no usable xattr support', capability='xattr_runtime')

makepath(FROMDIR)
source = FROMDIR / 'many-xattrs'
source.write_text('file with many attribute names\n')

attrs = {f'entry-{i:02d}-' + 'x' * 64: f'value-{i:02d}' for i in range(16)}
if sum(map(len, attrs)) <= 1024:
    test_fail('attribute names do not exceed the initial list buffer')

try:
    for name, value in attrs.items():
        set_value(name, value, source)
except OSError as error:
    test_skipped(f'cannot store the long xattr name list: {error}', capability='xattr_runtime')

run_rsync('-aX', f'{FROMDIR}/', f'{TODIR}/')
dest = TODIR / source.name
assert_same(source, dest, label='long xattr name list')
for name, value in attrs.items():
    try:
        actual = get_value(name, dest)
    except OSError as error:
        test_fail(f'could not read destination xattr {name!r}: {error}')
    if actual != value.encode():
        test_fail(f'destination xattr {name!r}: expected {value!r}, got {actual!r}')
