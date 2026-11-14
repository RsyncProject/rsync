#!/usr/bin/env python3

import os

from harness import metadata, environment
from harness.comparison import checkit
from harness.environment import FROMDIR, TODIR
from harness.results import test_skipped
from harness.xattrs import RSYNC_PREFIX, set_value as xattr_set, is_supported as xattrs_supported

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
