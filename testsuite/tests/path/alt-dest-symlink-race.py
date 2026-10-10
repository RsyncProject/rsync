#!/usr/bin/env python3

import os

from harness import metadata
from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, get_rootgid, get_rootuid, get_testuid, makepath, rmtree, run_rsync,
    start_test_daemon, test_fail,
)

metadata(features={'alt-dest', 'daemon', 'symlink'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', cost='expensive', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'security', 'version-mix'})

base = SCRATCHDIR / 'alt-dest-symlink'
rmtree(base)
modules = []
cases = []
timestamp = 1_234_567_890

for option in ('link', 'copy'):
    root = base / option
    outside = base / f'{option}-outside'
    source = base / f'{option}-source'
    makepath(root, outside, source)
    source_file = source / 'target'
    outside_file = outside / 'target'
    source_file.write_text('source00\n' if option == 'copy' else 'matching\n')
    outside_file.write_text('outside0\n' if option == 'copy' else 'matching\n')
    os.utime(source_file, (timestamp, timestamp))
    os.utime(outside_file, (timestamp, timestamp))
    os.chmod(source_file, 0o644)
    os.chmod(outside_file, 0o644)
    (root / 'cd').symlink_to(outside)
    modules.append((option, {'path': str(root), 'read only': 'no'}))
    cases.append((option, root, outside_file, source, source_file))

global_options = {'use chroot': 'no', 'log file': str(base / 'rsyncd.log')}
if get_testuid() == get_rootuid():
    global_options.update({'uid': str(get_rootuid()), 'gid': str(get_rootgid())})
url = start_test_daemon(write_daemon_conf(modules, global_options=global_options,
                                          name='alt-dest-symlink.conf'), 12882)

for option, root, outside, source, expected in cases:
    run_rsync('-rtp', f'--{option}-dest=cd', f'{source}/', f'{url}{option}/', check=False)
    result = root / 'target'
    if not result.is_file():
        test_fail(f'{option}-dest: destination was not created')
    if option == 'link' and result.stat().st_ino == outside.stat().st_ino:
        test_fail('link-dest followed an escaping basis symlink')
    if option == 'copy' and result.read_bytes() != expected.read_bytes():
        test_fail('copy-dest read through an escaping basis symlink')
