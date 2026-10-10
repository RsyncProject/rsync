#!/usr/bin/env python3

import os

from harness.daemon_config import build_rsyncd_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR, check_perms, checkit, makepath, rmtree, run_rsync,
    start_test_daemon, test_fail,
)
from harness import metadata

metadata(features={'chmod', 'daemon'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'metadata', 'version-mix'})

DAEMON_PORT = 12875

checkdir = SCRATCHDIR / 'check'

FROMDIR.mkdir(parents=True, exist_ok=True)
(FROMDIR / 'name1').write_text("This is the file\n")
(FROMDIR / 'name2').write_text("This is the other file\n")
(FROMDIR / 'dir1').mkdir()
(FROMDIR / 'dir2').mkdir()

os.chmod(FROMDIR / 'name1', 0o4700)
os.chmod(FROMDIR / 'dir1', 0o700)
os.chmod(FROMDIR / 'dir2', 0o770)

checkit(['-avv', f'{FROMDIR}/', f'{checkdir}/'], FROMDIR, checkdir)

os.umask(0o002)

for entry in checkdir.iterdir():
    st = entry.stat()
    mode = st.st_mode & ~0o6000
    mode |= 0o444
    if entry.is_dir() or (st.st_mode & 0o111):
        mode |= 0o111
    os.chmod(entry, mode)
plus_w = 0o222 & ~0o002
for d in (checkdir, checkdir / 'dir1', checkdir / 'dir2'):
    st = d.stat()
    os.chmod(d, st.st_mode | plus_w)

checkit(['-avv', '--chmod', 'ug-s,a+rX,D+w', f'{FROMDIR}/', f'{TODIR}/'],
        checkdir, TODIR)

def check_permcopy(chmod_arg, start_mode, expected, is_dir=False):
    rmtree(FROMDIR)
    rmtree(TODIR)
    makepath(FROMDIR)
    permcopy = FROMDIR / 'permcopy'
    if is_dir:
        permcopy.mkdir()
    else:
        permcopy.write_text('permcopy\n')
    os.chmod(permcopy, start_mode)
    run_rsync('-avv', f'--chmod={chmod_arg}', f'{FROMDIR}/', f'{TODIR}/')
    check_perms(TODIR / 'permcopy', expected)

check_permcopy('g=o,o=', 0o647, 'rw-rwx---')
check_permcopy('g=u', 0o741, 'rwxrwx--x')
check_permcopy('g-o', 0o775, 'rwx-w-r-x')
check_permcopy('u=g', 0o4755, 'r-xr-xr-x')
check_permcopy('g=u', 0o2755, 'rwxrwxr-x')
check_permcopy('o=u', 0o1750, 'rwxr-xrwx', is_dir=True)

rmtree(FROMDIR)
rmtree(TODIR)
makepath(FROMDIR)
(FROMDIR / 'permcopy').write_text('permcopy\n')
proc = run_rsync('-avv', '--chmod=g=ur', f'{FROMDIR}/', f'{TODIR}/',
                 check=False, capture_output=True)
if proc.returncode == 0:
    test_fail('--chmod=g=ur was not rejected')

rmtree(FROMDIR)
rmtree(checkdir)
rmtree(TODIR)
makepath(TODIR, FROMDIR / 'foo')
(FROMDIR / 'bar').touch()

checkit(['-avv', f'{FROMDIR}/', f'{checkdir}/'], FROMDIR, checkdir)
os.chmod(FROMDIR / 'bar', (FROMDIR / 'bar').stat().st_mode | 0o001)

checkit(['-avv', '--chmod=Fo-x', f'{FROMDIR}/', f'{TODIR}/'], checkdir, TODIR)

conf = build_rsyncd_conf()
with open(conf, 'a') as f:
    f.write(f"""
[test-incoming-chmod]
\tpath = {TODIR}
\tread only = no
\tincoming chmod = Fo-x
""")

url = start_test_daemon(conf, DAEMON_PORT)

rmtree(TODIR)
makepath(TODIR)

checkit(['-avv', '--no-perms', f'{FROMDIR}/',
         f'{url}test-incoming-chmod/'],
        checkdir, TODIR, allowed_codes=(0, 23))
