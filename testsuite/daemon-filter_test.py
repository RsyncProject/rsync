#!/usr/bin/env python3
"""Daemon coverage: exclude, incoming chmod, outgoing chmod (at depth).

A daemon-side exclude must hide matching files everywhere in the module tree;
incoming/outgoing chmod must rewrite the permissions of every transferred file,
including ones several levels deep.
"""

import os
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    assert_mode, assert_not_exists, assert_same, make_tree, makepath, rmtree,
    rsync_argv, start_test_daemon, test_fail, walk_files, write_daemon_conf,
)

DAEMON_PORT = 12887

src = FROMDIR
rmtree(src)
make_tree(src, depth=3)
(src / 'a.secret').write_text('s\n')
(src / 'd1' / 'd2' / 'b.secret').write_text('s\n')
rels = [p.relative_to(src) for p in walk_files(src)]

incdir = SCRATCHDIR / 'incdest'
for d in (incdir,):
    rmtree(d)
makepath(incdir)

conf = write_daemon_conf([
    ('filt', {'path': src, 'read only': 'yes', 'exclude': '*.secret'}),
    ('inc',  {'path': incdir, 'read only': 'no', 'incoming chmod': 'F600'}),
    ('out',  {'path': src, 'read only': 'yes', 'outgoing chmod': 'Fg-r,Fo-r'}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def pull(mod, dest):
    rmtree(dest)
    makepath(dest)
    subprocess.run(rsync_argv('-a', f'{url}{mod}/', f'{dest}/'),
                   stdout=subprocess.DEVNULL)


# --- daemon exclude hides *.secret everywhere in the module -----------------
fp = SCRATCHDIR / 'filtpull'
pull('filt', fp)
assert_not_exists(fp / 'a.secret', label='daemon exclude top')
assert_not_exists(fp / 'd1' / 'd2' / 'b.secret', label='daemon exclude deep')
assert_same(fp / 'd1' / 'd2' / 'f2', src / 'd1' / 'd2' / 'f2',
            label='daemon exclude kept others')

# --- incoming chmod rewrites pushed file modes at depth ---------------------
subprocess.run(rsync_argv('-a', f'{src}/', f'{url}inc/'),
               stdout=subprocess.DEVNULL)
checked = 0
for rel in rels:
    p = incdir / rel
    if p.is_file():
        assert_mode(p, 0o600, label=f'incoming chmod {rel}')
        checked += 1
if checked == 0:
    test_fail("incoming chmod test transferred no files")

# --- outgoing chmod rewrites pulled file modes at depth ---------------------
op = SCRATCHDIR / 'outpull'
pull('out', op)
for rel in rels:
    p = op / rel
    if p.is_file() and (os.stat(p).st_mode & 0o044):
        test_fail(f"outgoing chmod did not clear group/other read on {rel}: "
                  f"{oct(os.stat(p).st_mode & 0o777)}")

print("daemon-filter: exclude / incoming chmod / outgoing chmod verified at depth")
