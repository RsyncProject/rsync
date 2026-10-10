#!/usr/bin/env python3

import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    verify_dirs,
)
from harness import metadata

metadata(features={'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'configuration', 'daemon', 'version-mix'})

DAEMON_PORT = 12891

src = FROMDIR
rmtree(src)
make_tree(src, depth=3)

deldir = SCRATCHDIR / 'deldest'
makepath(deldir)

conf = write_daemon_conf([
    ('refuse-delete', {'path': deldir, 'read only': 'no',
                       'refuse options': 'delete'}),
    ('refuse-wild',   {'path': src, 'read only': 'yes',
                       'refuse options': 'checksum*'}),
    ('only-av',       {'path': src, 'read only': 'yes',
                       'refuse options': '* !a !v'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def refused(args, what):
    proc = subprocess.run(rsync_argv(*args),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode == 0:
        test_fail(f"{what} was not refused")
    return proc.stderr

def allowed(args, what, expected=None, actual=None):
    proc = subprocess.run(rsync_argv(*args),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode not in (0, 23):
        test_fail(f"{what} was unexpectedly refused: {proc.stderr}")
    if expected is not None:
        verify_dirs(expected, actual, label=what)

refused(['-a', '--delete', f'{src}/', f'{url}refuse-delete/'],
        "--delete on a refuse=delete module")
allowed(['-a', f'{src}/', f'{url}refuse-delete/'],
        "plain push to a refuse=delete module", src, deldir)

dest = SCRATCHDIR / 'wilddest'
makepath(dest)
refused(['-a', '--checksum', f'{url}refuse-wild/', f'{dest}/'],
        "--checksum on a refuse=checksum* module")

rmtree(dest)
makepath(dest)
allowed(['-av', f'{url}only-av/', f'{dest}/'], "-av on an allow-list module",
        src, dest)
refused(['-avz', f'{url}only-av/', f'{dest}/'],
        "-z on an allow-list module")

print("daemon-refuse: named / wildcard / allow-list refuse options verified")
