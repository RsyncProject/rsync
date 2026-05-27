#!/usr/bin/env python3
"""Daemon coverage: module path, read only, write only, list.

Drives a loopback daemon (secure stdio-pipe transport by default) and checks
the access-control parameters, transferring a >=3-deep tree through each module
and pulling a deep sub-path to exercise in-module path resolution.
"""

import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_same, make_tree, makepath, rmtree, rsync_argv, run_rsync,
    start_test_daemon, test_fail, verify_dirs, walk_files, write_daemon_conf,
)

DAEMON_PORT = 12886

src = FROMDIR
rwdir = SCRATCHDIR / 'rwdest'
wodir = SCRATCHDIR / 'wodest'
pulld = SCRATCHDIR / 'pulled'
for d in (rwdir, wodir, pulld, TODIR):
    rmtree(d)
makepath(rwdir, wodir)
rmtree(src)
make_tree(src, depth=3)
rels = [p.relative_to(src) for p in walk_files(src)]

conf = write_daemon_conf([
    ('ro',     {'path': src,   'read only': 'yes', 'comment': 'r/o'}),
    ('rw',     {'path': rwdir, 'read only': 'no',  'comment': 'r/w'}),
    ('wo',     {'path': wodir, 'read only': 'no',  'write only': 'yes'}),
    ('hidden', {'path': src,   'list': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def fails(args, what):
    proc = subprocess.run(rsync_argv(*args),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode == 0:
        test_fail(f"{what} unexpectedly succeeded")
    return proc.stderr


# --- read only module: pull works (deep), push refused ----------------------
run_rsync('-a', f'{url}ro/', f'{pulld}/', check=False)   # codes 0/23 ok
for rel in rels:
    assert_same(pulld / rel, src / rel, label=f'pull ro {rel}')
# deep sub-path pull
rmtree(pulld)
makepath(pulld)
run_rsync('-a', f'{url}ro/d1/d2/', f'{pulld}/', check=False)
assert_same(pulld / 'f2', src / 'd1' / 'd2' / 'f2', label='deep sub-path pull')
fails(['-a', f'{src}/', f'{url}ro/'], "push to a read-only module")

# --- read/write module: push works at depth ---------------------------------
run_rsync('-a', f'{src}/', f'{url}rw/', check=False)
verify_dirs(src, rwdir, label="push to rw module")

# --- write only module: push works, pull refused ----------------------------
run_rsync('-a', f'{src}/', f'{url}wo/', check=False)
verify_dirs(src, wodir, label="push to wo module")
fails(['-a', f'{url}wo/', f'{pulld}/'], "pull from a write-only module")

# --- list: hidden module absent from the listing, ro/rw/wo present ----------
proc = subprocess.run(rsync_argv(url), capture_output=True, text=True)
listing = proc.stdout
for m in ('ro', 'rw', 'wo'):
    if m not in listing:
        test_fail(f"module {m} missing from the daemon listing:\n{listing}")
if 'hidden' in listing:
    test_fail(f"list=no module leaked into the listing:\n{listing}")
# ...but the hidden module is still usable by name.
rmtree(pulld)
makepath(pulld)
run_rsync('-a', f'{url}hidden/f0', f'{pulld}/', check=False)
assert_same(pulld / 'f0', src / 'f0', label='hidden module usable by name')

print("daemon-access: read only / write only / list / deep paths verified")
