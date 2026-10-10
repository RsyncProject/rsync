#!/usr/bin/env python3

import os
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail

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

proc = subprocess.run(
    rsync_argv('-r', '--copy-links', f'{url}m/sub/', str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:400]!r}'

for path in sorted(p for p in dest.rglob('*') if p.is_file()):
    body = path.read_text(errors='replace')
    if body in (SECRET, DIRSECRET):
        test_fail(f'--copy-links followed a symlink out of the module: {path} '
                  f'holds out-of-module content ({ctx})')

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
