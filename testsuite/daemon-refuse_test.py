#!/usr/bin/env python3
"""Daemon coverage: refuse options (a named option, a wildcard, and the
allow-list negation form).

daemon-refuse-compress_test.py covers the basic case; this widens it to a
different named option, a wildcard pattern, and the "* !a !v" allow-list idiom
documented in rsyncd.conf.5.
"""

import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    make_tree, makepath, rmtree, rsync_argv, run_rsync, start_test_daemon,
    test_fail, verify_dirs, write_daemon_conf,
)

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


def allowed(args, what):
    proc = subprocess.run(rsync_argv(*args),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode not in (0, 23):
        test_fail(f"{what} was unexpectedly refused: {proc.stderr}")


# --- a named refused option (delete) ----------------------------------------
refused(['-a', '--delete', f'{src}/', f'{url}refuse-delete/'],
        "--delete on a refuse=delete module")
allowed(['-a', f'{src}/', f'{url}refuse-delete/'],
        "plain push to a refuse=delete module")

# --- a wildcard refused option (checksum*) ----------------------------------
dest = SCRATCHDIR / 'wilddest'
makepath(dest)
refused(['-a', '--checksum', f'{url}refuse-wild/', f'{dest}/'],
        "--checksum on a refuse=checksum* module")

# --- the "* !a !v" allow-list: -av allowed, -z refused ----------------------
rmtree(dest)
makepath(dest)
allowed(['-av', f'{url}only-av/', f'{dest}/'], "-av on an allow-list module")
refused(['-avz', f'{url}only-av/', f'{dest}/'],
        "-z on an allow-list module")

print("daemon-refuse: named / wildcard / allow-list refuse options verified")
