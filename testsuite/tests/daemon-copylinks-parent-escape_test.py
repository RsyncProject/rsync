#!/usr/bin/env python3
"""--copy-links may follow ".." inside the module, but never out of it.

Allowing a parent-relative symlink target to resolve at all is a loosening:
the resolver used to refuse every literal ".." at the front door, which
protected the module boundary by accident while also breaking legitimate
in-module targets.  Now the fd-anchored walk decides, popping a held parent
descriptor per ".." and refusing to pop above the module root.

So both directions need holding down together, which is what this checks:

  in-module  ../target.txt   file symlink -> dereferenced (the loosening works)
  in-module  ../targetdir    dir  symlink -> dereferenced
  escaping   ../../outside/* file symlink -> refused, no content leaves
  escaping   ../../outside/* dir  symlink -> refused, no content leaves

A file and a directory symlink are both covered because they take different
paths through the sender -- the directory one already understood "..", the
file one did not, which is the asymmetry this fix removes.
"""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

PORT = 12945

IN_FILE = 'IN-MODULE-FILE\n'
IN_DIRFILE = 'IN-MODULE-DIRFILE\n'
SECRET = 'OUT-OF-MODULE-SECRET\n'
DIRSECRET = 'OUT-OF-MODULE-DIRSECRET\n'

base = SCRATCHDIR / 'copylinks-parent-escape'
rmtree(base)
module = base / 'module'
outside = base / 'outside'
dest = base / 'dest'
makepath(module / 'sub', module / 'targetdir', outside / 'outdir', dest)

(module / 'target.txt').write_text(IN_FILE)
(module / 'targetdir' / 'f.txt').write_text(IN_DIRFILE)
# Siblings of the module root: reachable only by climbing above it.
(outside / 'secret.txt').write_text(SECRET)
(outside / 'outdir' / 's.txt').write_text(DIRSECRET)

os.symlink('../target.txt', module / 'sub' / 'in_file')
os.symlink('../targetdir', module / 'sub' / 'in_dir')
os.symlink('../../outside/secret.txt', module / 'sub' / 'esc_file')
os.symlink('../../outside/outdir', module / 'sub' / 'esc_dir')

conf = write_daemon_conf([
    ('m', {'path': str(module), 'read only': 'yes', 'use chroot': 'no'}),
], name='copylinks-parent-escape.conf')
url = start_test_daemon(conf, PORT)

# A refused escape makes rsync exit 23; that is the expected outcome here, so
# the oracle is what landed on disk rather than the exit status.
proc = subprocess.run(
    rsync_argv('-r', '--copy-links', f'{url}m/sub/', str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:400]!r}'

# 1. Nothing from above the module root may appear anywhere in the destination.
for path in sorted(p for p in dest.rglob('*') if p.is_file()):
    body = path.read_text(errors='replace')
    if body in (SECRET, DIRSECRET):
        test_fail(f'--copy-links followed a symlink out of the module: {path} '
                  f'holds out-of-module content ({ctx})')

# 2. The in-module targets must still be dereferenced -- otherwise "no leak"
#    could be satisfied by refusing everything, which is the bug being fixed.
got_file = dest / 'in_file'
if not got_file.is_file() or got_file.is_symlink():
    test_fail(f'in-module ../target.txt was not dereferenced ({ctx})')
if got_file.read_text() != IN_FILE:
    test_fail(f'in-module file symlink gave wrong content: '
              f'{got_file.read_text()!r} ({ctx})')

got_dirfile = dest / 'in_dir' / 'f.txt'
if not got_dirfile.is_file() or got_dirfile.read_text() != IN_DIRFILE:
    test_fail(f'in-module ../targetdir was not dereferenced ({ctx})')

print('copy-links followed ".." inside the module and refused it at the root')
