#!/usr/bin/env python3
"""Pin WHEN the daemon applies default symlink munging, by config regime.

The rsyncd.conf(5) `use chroot` entry says:

  "If the daemon is serving the / dir (either directly or due to being chrooted
   to the module's path), rsync does not do any path sanitizing or (default)
   munging.  When it has to limit access to a particular subdir (either due to
   chroot being disabled or having an inside-chroot path set), rsync will munge
   symlinks (by default) and sanitize paths."

This was flagged as a suspected code mismatch during the doc audit.  It is NOT a
mismatch: clientserver.c derives `module_dirlen` (0 when the inside-chroot path
is "/", >0 for a real subdir) and sets
    munge_symlinks default = !use_chroot || module_dirlen
so the doc's regime split is exactly right.  This test confirms it by observing
the default (no explicit `munge symlinks`) across three regimes:

  A. use chroot = no            -> module_dirlen>0 -> munge ON
  B. use chroot = yes, path=DIR -> chrooted to DIR, inside path "/", len 0 -> munge OFF
  C. use chroot = yes, path=BASE/./SUB -> inside path "SUB", len>0 -> munge ON

Regimes B and C need root (chroot).  A runs anywhere.  The observable is whether
an uploaded symlink is stored verbatim or with the /rsyncd-munged/ prefix.
(Path sanitizing -- the other half of the same sentence -- is exercised by the
operator-path-* and daemon-scan-dir-escape tests.)
"""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
    write_daemon_conf,
)

DAEMON_PORT = 12911
LINKVAL = 'realfile'        # a plain rel-within target

base = SCRATCHDIR / 'chroot-munge'
rmtree(base)
base.mkdir(parents=True)

# Upload source: one symlink we can inspect after it lands.
srcup = base / 'srcup'
srcup.mkdir()
os.symlink(LINKVAL, srcup / 'sl')

is_root = os.geteuid() == 0

# Module A: no chroot.  Modules B/C: chroot (root only).
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
    """Upload the symlink into `module`; return the value as stored on disk."""
    subprocess.run(rsync_argv('-al', f'{srcup}/', f'{url}{module}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sl = landing_dir / 'sl'
    if not sl.is_symlink():
        test_fail(f"{module}: uploaded symlink did not land at {sl}")
    return os.readlink(sl)


MUNGED = '/rsyncd-munged/' + LINKVAL

# A: no chroot -> default munge ON.
got = stored_link('a_nochroot', mod_a_dir)
if got != MUNGED:
    test_fail(f"use chroot=no: expected default munge ({MUNGED!r}), stored {got!r}")

if is_root:
    # B: chrooted to the module path, inside path "/" -> default munge OFF.
    got = stored_link('b_chroot_root', mod_b_dir)
    if got != LINKVAL:
        test_fail(f"use chroot=yes serving '/': expected NO munge ({LINKVAL!r}), "
                  f"stored {got!r}")
    # C: chroot with a /./ inner subdir -> default munge ON.
    got = stored_link('c_chroot_sub', mod_c_sub)
    if got != MUNGED:
        test_fail(f"use chroot=yes with inside-path 'sub': expected default munge "
                  f"({MUNGED!r}), stored {got!r}")
    print("daemon-chroot-munge-default: default munge OFF when serving '/', ON "
          "for a non-chroot module and for a chroot module with an inside subdir "
          "-- the use-chroot doc regime split is accurate")
else:
    test_skipped("chroot regimes (B/C) need root; verified the no-chroot regime "
                 "munges by default")
