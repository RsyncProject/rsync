#!/usr/bin/env python3

import os

from harness.rsync import (
    CHKDIR, TMPDIR, TODIR,
    checkit, cp_touch, run_rsync,
)

os.chdir(TMPDIR)

for d in ('from1', 'from2', 'from3', 'deep'):
    os.mkdir(d)
for d in ('from2/sub1', 'from3/sub1', 'from3/sub2', 'from1/dir-and-not-dir'):
    os.mkdir(d)

CHKDIR.mkdir(exist_ok=True)
for d in ('sub1', 'sub2', 'dir-and-not-dir'):
    (CHKDIR / d).mkdir()

with open('from1/one', 'w') as f:
    f.write("one\n")
cp_touch('from1/one', 'from2/one')
cp_touch('from1/one', 'from3/one')

with open('from1/two', 'w') as f:
    f.write("two\n")
with open('from2/three', 'w') as f:
    f.write("three\n")
with open('from3/four', 'w') as f:
    f.write("four\n")
with open('from1/five', 'w') as f:
    f.write("five\n")
with open('from3/six', 'w') as f:
    f.write("six\n")
with open('from2/sub1/uno', 'w') as f:
    f.write("sub1\n")
cp_touch('from2/sub1/uno', 'from3/sub1/uno')
with open('from3/sub1/dos', 'w') as f:
    f.write("sub2\n")
with open('from2/sub1/tres', 'w') as f:
    f.write("sub3\n")
with open('from3/sub2/subby', 'w') as f:
    f.write("subby\n")
with open('from1/dir-and-not-dir/inside', 'w') as f:
    f.write("extra\n")
with open('from3/dir-and-not-dir', 'w') as f:
    f.write("not-dir\n")
with open('deep/arg-test', 'w') as f:
    f.write("arg-test\n")
with open('shallow', 'w') as f:
    f.write("shallow\n")

for src in ('from1/one', 'from1/two', 'from2/three', 'from3/four',
            'from1/five', 'from3/six'):
    cp_touch(src, str(CHKDIR))
cp_touch('deep/arg-test', str(CHKDIR))
cp_touch('shallow', str(CHKDIR))
cp_touch('from1/dir-and-not-dir/inside', str(CHKDIR / 'dir-and-not-dir'))
for src in ('from2/sub1/uno', 'from3/sub1/dos', 'from2/sub1/tres'):
    cp_touch(src, str(CHKDIR / 'sub1'))
cp_touch('from3/sub2/subby', str(CHKDIR / 'sub2'))

def _flatten_dirs(src, dst):
    run_rsync('-av', '--existing', '-f', 'exclude,! */', f'{src}/', f'{dst}/')

_flatten_dirs('from1', 'from2')
_flatten_dirs('from2', 'from3')
_flatten_dirs('from1', str(CHKDIR))
_flatten_dirs('from3', str(CHKDIR))

checkit(['-avv', 'deep/arg-test', 'shallow', 'from1/', 'from2/', 'from3/', 'to/'],
        CHKDIR, TODIR)
