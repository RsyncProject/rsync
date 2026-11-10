#!/usr/bin/env python3

from fixtures.devices import check_devices
from harness import metadata, rsync
from harness.rsync import RSYNC_PREFIX, test_skipped, xattr_set, xattrs_supported

metadata(features={'xattr_runtime'}, transports={'pipe'})

if not xattrs_supported():
    test_skipped('fake device tests need xattrs', capability='xattr_runtime')
rsync.RSYNC += ' --fake-super'
rsync.TLS_ARGS = (rsync.TLS_ARGS + ' --fake-super').strip()

def make_special(path, kind, major=0, minor=0):
    mode = {'p': 0o10644, 'c': 0o20644, 'b': 0o60644}[kind]
    path.touch()
    xattr_set(f'{RSYNC_PREFIX}.%stat', f'{mode:o} {major},{minor} 0:0', path)
    return True

check_devices(make_special)
