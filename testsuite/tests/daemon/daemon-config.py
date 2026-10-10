#!/usr/bin/env python3

from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, run_rsync, start_test_daemon, test_fail,
)
from harness import metadata

metadata(features={'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'configuration', 'daemon', 'version-mix'})

DAEMON_PORT = 12893

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

def module(name):
    return f'[{name}]\n\tpath = {src}\n\tread only = yes\n'

included = SCRATCHDIR / 'included.conf'
included.write_text(module('inc-file'))
directory = SCRATCHDIR / 'rsyncd.conf.d'
makepath(directory)
(directory / '20-b.conf').write_text(module('inc-b'))
(directory / '10-a.conf').write_text(module('inc-a'))
(directory / 'README').write_text('ignored\n')
merged = SCRATCHDIR / 'merged.inc'
merged.write_text(module('merged'))

conf = SCRATCHDIR / 'daemon-config.conf'
conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    "hosts allow = localhost 127.0.0.0/8\n"
    f"log file = {SCRATCHDIR}/rsyncd.log\n"
    f"&include {included}\n"
    f"&include = {directory}\n"
    f"&merge = {merged}\n"
    f"\n[badpath]\n\tpath = {SCRATCHDIR}/no-such-dir\n\tread only = yes\n"
)
url = start_test_daemon(conf, DAEMON_PORT)

listing = run_rsync(url, capture_output=True).stdout
for name in ('inc-file', 'inc-a', 'inc-b', 'merged'):
    if name not in listing:
        test_fail(f'{name}: included module is absent from the listing')
    run_rsync('-r', f'{url}{name}/')
if run_rsync('-r', f'{url}badpath/', check=False, capture_output=True).returncode == 0:
    test_fail('a module with a missing path accepted a connection')

print('daemon includes, merges and missing paths verified')
