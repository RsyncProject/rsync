#!/usr/bin/env python3
"""Lock down daemon in-module-symlink resolution for a writable, non-chroot
module, across every combination of:

    insecure links {no (default secure), yes (admin opt-out)}
  x munge symlinks {no, yes}
  x link origin    {pre-existing on disk, uploaded via rsync}
  x operation      {READ via --copy-dirlinks pull, WRITE via --keep-dirlinks push}
  x symlink TYPE   (the target the in-module link "evil" points at):
        rel-within   : relative, stays inside the module           (legit)
        rel-outside  : relative, climbs OUT of the module           (escape)
        rel-transits : relative, climbs above the module root with ".."
                       then back IN -- net target inside, path transits out
        abs-outside  : absolute, lands OUTSIDE the module           (escape)
        abs-inside   : absolute, lands INSIDE the module            (legit)

We measure whether the operation FOLLOWED the link to its target ("followed").
For an *outside* target, followed == an out-of-module escape; for an *inside*
target, followed == legitimate in-module access.

Contract pinned here for THIS branch:
  - insecure links = no (default): the secure resolver follows only a
    rel-within link; it refuses an absolute target (even one landing inside) and
    any "../" that rises above the module root (even one landing back inside).
    So no escape -- and rel-transits / abs-inside are a deliberate functionality
    cost of confinement.
  - insecure links = yes (admin opt-out): restores the pre-3.4.3 legacy
    behaviour uniformly (sender AND receiver) -- every type is followed, so an
    outside type escapes, matching stock 3.2.7.
  - Uploaded links never escape regardless: munge stores them /rsyncd-munged/-
    prefixed, and munge-off still sanitises an incoming link (drops a leading
    "/", strips escaping "..").

Needs root (foreign-owned plant; served as root) + a real TCP peer.
"""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    find_attacker_uid, require_tcp, rmtree, rsync_argv, rsync_supports,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12909
SECRET = "TARGET-CONTENT\n"
PWNED = "WROTE-THROUGH-LINK\n"

require_tcp("the daemon symlink-resolution flow needs a real TCP peer")
if os.geteuid() != 0:
    test_skipped("requires root to plant a foreign-owned symlink and serve as root")
if not rsync_supports('--copy-dirlinks'):
    test_skipped("rsync lacks --copy-dirlinks")
ATT = find_attacker_uid()
if ATT is None:
    test_skipped("no untrusted-uid user available for the cross-uid plant")

TYPES = ('rel-within', 'rel-outside', 'rel-transits', 'abs-outside', 'abs-inside')
OUTSIDE = {'rel-outside', 'abs-outside'}

base = SCRATCHDIR / 'symlink-escape-matrix'
rmtree(base)
base.mkdir(parents=True)

MODS = {}   # (insecure, munge) -> (modname, moddir, outside)
mod_conf = []
for insecure in (False, True):
    for munge in (False, True):
        name = f"m_{'ins' if insecure else 'safe'}_{'munge' if munge else 'nomunge'}"
        moddir = base / (name + '_root')
        outside = base / (name + '_outside')
        moddir.mkdir(); outside.mkdir()
        MODS[(insecure, munge)] = (name, moddir, outside)
        mod_conf.append((name, {
            'path': str(moddir), 'use chroot': 'no', 'read only': 'no',
            'uid': '0', 'gid': '0',
            'insecure links': 'yes' if insecure else 'no',
            'munge symlinks': 'yes' if munge else 'no',
        }))

conf = write_daemon_conf(mod_conf, name='symlink-escape-matrix.conf')
url = start_test_daemon(conf, DAEMON_PORT)


def link_target(moddir, outside, sltype):
    """(symlink value, resolved target dir, target_is_outside) for a TYPE."""
    realdir = moddir / 'realdir'
    if sltype == 'rel-within':
        return 'realdir', realdir, False
    if sltype == 'rel-outside':
        return '../' + outside.name, outside, True
    if sltype == 'rel-transits':       # up to the parent, then back into the module
        return f'../{moddir.name}/realdir', realdir, False
    if sltype == 'abs-outside':
        return str(outside), outside, True
    if sltype == 'abs-inside':
        return str(realdir), realdir, False
    raise AssertionError(sltype)


def attempt(insecure, munge, origin, vector, sltype):
    """Set up the cell, run it, return (followed, target_outside)."""
    name, moddir, outside = MODS[(insecure, munge)]
    rmtree(moddir); moddir.mkdir()
    rmtree(outside); outside.mkdir()
    realdir = moddir / 'realdir'
    realdir.mkdir()
    (realdir / 'tgtfile').write_text(SECRET)     # in-module target content
    (outside / 'tgtfile').write_text(SECRET)     # out-of-module target content
    symval, resolved, t_out = link_target(moddir, outside, sltype)
    evil = moddir / 'evil'

    if origin == 'preexist':
        os.symlink(symval, evil)
        os.lchown(evil, ATT, ATT)
    else:  # uploaded: transfer the link in so munge/sanitise applies on the daemon
        up = base / 'srcup'
        rmtree(up); up.mkdir()
        os.symlink(symval, up / 'evil')
        subprocess.run(rsync_argv('-al', f'{up}/', f'{url}{name}/'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not evil.is_symlink():
            return False, t_out

    if vector == 'read':
        dest = base / 'dest'
        rmtree(dest); dest.mkdir()
        subprocess.run(
            rsync_argv('-r', '--copy-dirlinks', f'{url}{name}/', f'{dest}/'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        through = dest / 'evil' / 'tgtfile'      # content reached THROUGH the link
        return (through.is_file() and through.read_text() == SECRET), t_out

    # write: push "evil/pwned" with --keep-dirlinks -> writes through the link
    sw = base / 'srcw'
    rmtree(sw); sw.mkdir()
    (sw / 'evil').mkdir()
    (sw / 'evil' / 'pwned').write_text(PWNED)
    subprocess.run(rsync_argv('-r', '--keep-dirlinks', f'{sw}/', f'{url}{name}/'),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    landed = resolved / 'pwned'
    return (landed.is_file() and landed.read_text() == PWNED), t_out


def expected_followed(insecure, munge, origin, vector, sltype):
    """Contract: secure default follows only a rel-within link; the opt-out
    follows everything; an uploaded link is sanitised/munged so only rel-within
    (whose value survives both) is still followable."""
    if origin == 'uploaded':
        # munge prefixes EVERY incoming link with /rsyncd-munged/ (none follow);
        # munge-off sanitises -- only a rel-within value survives intact.
        return sltype == 'rel-within' and not munge
    if insecure:
        return True                        # opt-out: legacy follow of every type
    return sltype == 'rel-within'          # secure default: only the in-tree link


# Diagnostic: REPORT_MATRIX=1 surfaces the full observed grid (via test_fail) for
# eyeballing / cross-version comparison; unset, the test just asserts the contract.
_REPORT = os.environ.get('REPORT_MATRIX')
grid, mismatches, escapes = [], [], []
for insecure in (False, True):
    for munge in (False, True):
        for origin in ('preexist', 'uploaded'):
            for vector in ('read', 'write'):
                for sltype in TYPES:
                    got, t_out = attempt(insecure, munge, origin, vector, sltype)
                    want = expected_followed(insecure, munge, origin, vector, sltype)
                    esc = got and t_out
                    grid.append(f"ins={int(insecure)} munge={int(munge)} "
                                f"{origin:8} {vector:5} {sltype:11}: "
                                f"followed={int(got)} want={int(want)} "
                                f"{'ESCAPE' if esc else ''}")
                    # A confined-default escape is a hard failure, always.
                    if esc and not insecure:
                        escapes.append(f"DEFAULT ESCAPE: munge={int(munge)} "
                                       f"{origin}/{vector}/{sltype}")
                    if got != want:
                        mismatches.append(
                            f"insecure={'yes' if insecure else 'no'} "
                            f"munge={'yes' if munge else 'no'} "
                            f"{origin}/{vector}/{sltype}: "
                            f"followed={got}, expected={want}")

if _REPORT:
    test_fail("REPORT-MATRIX grid:\n  " + "\n  ".join(grid))
if escapes:
    test_fail("secure-default confinement FAILED (out-of-module access):\n  "
              + "\n  ".join(escapes))
if mismatches:
    test_fail("symlink-resolution matrix deviated from the pinned contract:\n  "
              + "\n  ".join(mismatches))

print("daemon-symlink-escape-matrix: 5 link types x preexist/uploaded x "
      "read/write x munge x insecure all match the pinned contract "
      "(secure default follows only in-tree links and never escapes; the "
      "insecure-links opt-out restores legacy following on sender AND receiver)")
