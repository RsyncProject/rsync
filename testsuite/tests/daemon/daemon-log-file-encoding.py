#!/usr/bin/env python3

import os
import subprocess

from harness import metadata
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import FROMDIR, SCRATCHDIR, TODIR
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv

metadata(features={'daemon'}, transports={'pipe'})

FROMDIR.mkdir()
TODIR.mkdir()
name = b'turkish-I-\xc4\xb1'
with open(os.path.join(os.fsencode(FROMDIR), name), 'wb') as entry:
    entry.write(b'data\n')
if name not in os.listdir(os.fsencode(FROMDIR)):
    test_skipped('filesystem does not preserve the UTF-8 filename bytes')

conf = write_daemon_conf([('module', {'path': str(TODIR), 'read only': 'no'})],
                         global_options={'transfer logging': 'yes', 'log format': 'entry:%n'})
url = start_test_daemon(conf, 12894)
env = {**os.environ, 'LC_ALL': 'C'}
env.pop('RSYNC_ICONV', None)
proc = subprocess.run(rsync_argv('-r', f'{FROMDIR}/', f'{url}module/'),
                      env=env, capture_output=True, timeout=30)
if proc.returncode != 0:
    test_fail(f'daemon transfer failed ({proc.returncode}): {proc.stderr!r}')
if name not in os.listdir(os.fsencode(TODIR)):
    test_fail('daemon did not preserve the filename')

data = (SCRATCHDIR / 'rsyncd.log').read_bytes()
if b'] entry:turkish-I-\\#304\\#261\n' not in data or name in data:
    test_fail(f'daemon logfile did not escape the filename bytes: {data!r}')
