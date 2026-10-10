#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import (
    SCRATCHDIR,
    rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
    write_daemon_conf,
)

DAEMON_PORT = 12911
LINKVAL = 'realfile'

base = SCRATCHDIR / 'chroot-munge'
rmtree(base)
base.mkdir(parents=True)

srcup = base / 'srcup'
srcup.mkdir()
os.symlink(LINKVAL, srcup / 'sl')

is_root = os.geteuid() == 0

mod_a_dir = base / 'a_root'
mod_b_dir = base / 'b_root'
mod_c_base = base / 'c_base'
mod_c_sub = mod_c_base / 'sub'
for d in (mod_a_dir, mod_b_dir, mod_c_base, mod_c_sub):
    d.mkdir(parents=True)

modules = [('a_nochroot', {'path': str(mod_a_dir), 'use chroot': 'no',
                           'read only': 'no'})]
if is_root:
    modules += [
        ('b_chroot_root', {'path': str(mod_b_dir), 'use chroot': 'yes',
                           'read only': 'no'}),
        ('c_chroot_sub', {'path': f'{mod_c_base}/./sub', 'use chroot': 'yes',
                          'read only': 'no'}),
    ]

conf = write_daemon_conf(modules, name='chroot-munge.conf')
url = start_test_daemon(conf, DAEMON_PORT)

def stored_link(module, landing_dir):
    subprocess.run(rsync_argv('-al', f'{srcup}/', f'{url}{module}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sl = landing_dir / 'sl'
    if not sl.is_symlink():
        test_fail(f"{module}: uploaded symlink did not land at {sl}")
    return os.readlink(sl)

MUNGED = '/rsyncd-munged/' + LINKVAL

got = stored_link('a_nochroot', mod_a_dir)
if got != MUNGED:
    test_fail(f"use chroot=no: expected default munge ({MUNGED!r}), stored {got!r}")

if is_root:
    got = stored_link('b_chroot_root', mod_b_dir)
    if got != LINKVAL:
        test_fail(f"use chroot=yes serving '/': expected NO munge ({LINKVAL!r}), "
                  f"stored {got!r}")
    got = stored_link('c_chroot_sub', mod_c_sub)
    if got != MUNGED:
        test_fail(f"use chroot=yes with inside-path 'sub': expected default munge "
                  f"({MUNGED!r}), stored {got!r}")
    print("daemon-chroot-munge-default: default munge OFF when serving '/', ON "
          "for a non-chroot module and for a chroot module with an inside subdir "
          "-- the use-chroot doc regime split is accurate")
else:
    test_skipped("chroot regimes (B/C) need root; verified the no-chroot regime "
                 "munges by default", capability='root')
