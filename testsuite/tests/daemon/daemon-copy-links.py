#!/usr/bin/env python3

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, start_test_daemon, test_fail,
)

base = SCRATCHDIR / 'daemon-copy-links'
module = base / 'module'
destination = base / 'destination'
rmtree(base)
makepath(module / 'real-dir', destination)
(module / 'real-file').write_text('file target\n')
(module / 'real-dir' / 'nested').write_text('directory target\n')
(module / 'file-link').symlink_to('real-file')
(module / 'dir-link').symlink_to('real-dir')

config = write_daemon_conf([('module', {'path': str(module), 'read only': 'yes'})])
url = start_test_daemon(config, 12935)
run_rsync('-aL', f'{url}module/', f'{destination}/')

for link, target in (('file-link', 'real-file'), ('dir-link/nested', 'real-dir/nested')):
    result = destination / link
    if not result.is_file() or result.is_symlink():
        test_fail(f'{link} was not copied as a regular file')
    assert_same(result, module / target, label=link)

print('daemon --copy-links follows in-module file and directory symlinks')
