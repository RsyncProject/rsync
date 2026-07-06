#!/usr/bin/env python3
# Python rewrite of testsuite/chmod-option.test.
#
# Test --chmod and the daemon-side "incoming chmod = ..." setting.
# Covers a 2.6.8 bug where pushing a new directory with --no-perms to a
# daemon with an incoming chmod made the daemon mis-classify the dir as
# a file for the purposes of applying the incoming chmod.

import os
import shutil

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    build_rsyncd_conf, check_perms, checkit, makepath, rmtree,
    run_rsync, start_test_daemon, test_fail,
)


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

# Baseline copy of source.
checkit(['-avv', f'{FROMDIR}/', f'{checkdir}/'], FROMDIR, checkdir)

# Pin umask to 002 for the rest of the test and DO NOT restore it: rsync's
# --chmod `D+w` honours the process umask, so the expected tree (built just
# below) and the rsync run that follows must use the same umask -- exactly
# as the shell test did (it set `umask 002` and left it in effect). Without
# this the test fails under a different ambient umask (e.g. 077).
os.umask(0o002)

# Manually apply the mode transform that --chmod ug-s,a+rX,D+w should
# produce on the destination, then verify rsync's transform matches.
for entry in checkdir.iterdir():
    # ug-s,a+rX: clear setuid/setgid; add r everywhere; add x where
    # any existing x or the entry is a dir.
    st = entry.stat()
    mode = st.st_mode & ~0o6000
    mode |= 0o444  # a+r
    if entry.is_dir() or (st.st_mode & 0o111):
        mode |= 0o111  # a+X
    os.chmod(entry, mode)
# `chmod +w` with no explicit who: adds w for every category not masked by
# the current umask. Under umask 002 that's u+w AND g+w.
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


# Exercise chmod(1)-style permission copies.
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

# Now exercise the F-only chmod path.
rmtree(FROMDIR)
rmtree(checkdir)
rmtree(TODIR)
makepath(TODIR, FROMDIR / 'foo')
(FROMDIR / 'bar').touch()

checkit(['-avv', f'{FROMDIR}/', f'{checkdir}/'], FROMDIR, checkdir)
os.chmod(FROMDIR / 'bar', (FROMDIR / 'bar').stat().st_mode | 0o001)  # o+x

checkit(['-avv', '--chmod=Fo-x', f'{FROMDIR}/', f'{TODIR}/'], checkdir, TODIR)

# 2.6.8 regression: pushing a new directory via --no-perms to a daemon
# with an "incoming chmod" once mis-classified the directory as a file.
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
