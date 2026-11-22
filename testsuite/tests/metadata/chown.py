#!/usr/bin/env python3

import os

from harness import metadata
from harness.comparison import checkit
from harness.environment import FROMDIR, TODIR
from harness.results import test_skipped

metadata(features={'ownership'}, transports={'pipe'})

FROMDIR.mkdir(parents=True)
for name, uid, gid in (('name1', 5000, 5002), ('name2', 5001, 5003)):
    path = FROMDIR / name
    path.write_text(f'{name}\n')
    try:
        os.chown(path, uid, gid)
    except OSError:
        test_skipped('cannot set source ownership', capability='ownership')
os.chdir(FROMDIR.parent)
checkit(['--super', '-aHvv', 'from/', 'to/'], FROMDIR, TODIR)
