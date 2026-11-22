#!/usr/bin/env python3

import os

from harness import metadata, environment
from harness.comparison import checkit
from harness.environment import FROMDIR, TODIR
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync
from harness.xattrs import RSYNC_PREFIX, get_value, set_value as xattr_set, is_supported as xattrs_supported

metadata(features={'xattr_runtime'}, transports={'pipe'})

if not xattrs_supported():
    test_skipped('fake ownership tests need xattrs', capability='xattr_runtime')
environment.RSYNC += ' --fake-super'
environment.TLS_ARGS = (environment.TLS_ARGS + ' --fake-super').strip()
FROMDIR.mkdir(parents=True)
for name, uid, gid in (('name1', 5000, 5002), ('name2', 5001, 5003)):
    path = FROMDIR / name
    path.write_text(f'{name}\n')
    xattr_set(f'{RSYNC_PREFIX}.%stat', f'{path.stat().st_mode:o} 0,0 {uid}:{gid}', path)
os.chdir(FROMDIR.parent)
checkit(['-aHvv', 'from/', 'to/'], FROMDIR, TODIR)

source = FROMDIR / 'name1'
target = TODIR / 'name1'
mtime = 946684800  # 2000-01-01T00:00:00Z
for path in (source, target):
    os.utime(path, (mtime, mtime))
run_rsync('-a', '--chmod=a=', str(source), str(target))
mode = int(get_value(f'{RSYNC_PREFIX}.%stat', target).split()[0], 8)
if mode & 0o7777:
    test_fail('fake-super did not store the changed mode')
if target.stat().st_mtime != mtime:
    test_fail('writing fake-super metadata changed the preserved mtime')
