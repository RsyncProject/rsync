#!/usr/bin/env python3
"""Lock down daemon in-module-symlink resolution for a writable, non-chroot
module, across every combination of:

    insecure links {no (default secure), yes (admin opt-out)}
  x munge symlinks {no, yes}
  x link origin    {pre-existing on disk, uploaded via rsync}
  x access VECTOR  (how the client reaches the in-module link "evil"):
        read         : --copy-dirlinks pull of the module        (send_directory)
        write        : --keep-dirlinks push into the module       (send_directory)
        read-plain   : plain pull of an explicit sub-path "evil/" (change_dir)
        write-plain   : plain push into an explicit sub-path "evil/" (change_dir)
        compare-dest : push with --compare-dest=/evil             (basis_link_stat)
  x symlink TYPE   (the target the in-module link "evil" points at):
        rel-within   : relative, stays inside the module           (legit)
        rel-outside  : relative, climbs OUT of the module           (escape)
        rel-transits : relative, climbs above the module root with ".."
                       then back IN -- net target inside, path transits out
        abs-outside  : absolute, lands OUTSIDE the module           (escape)
        abs-inside   : absolute, lands INSIDE the module            (legit)

We measure whether the operation FOLLOWED the link to its target ("followed").
For an *outside* target, followed == an out-of-module escape.

Contract pinned here for THIS branch:
  - insecure links = no (default): NO out-of-module escape via ANY vector --
    every site (send_directory, change_dir, basis_link_stat, secure_basis_open)
    refuses a link whose target lands outside the module root.
  - insecure links = yes (admin opt-out): restores stock-3.2.7 follow behaviour
    UNIFORMLY at every site (the whole point of the option). We assert this
    against a REAL 3.2.7 daemon oracle when old_versions/rsync_3.2.7 is present:
    for every insecure=yes cell the current build must follow iff 3.2.7 follows.
    Without the oracle we fall back to a static contract that still catches a
    regression (the broken build returns follow=0 where 3.2.7/the contract
    want 1).

Why the earlier version of this test missed the change_dir / basis_link_stat
gap: its only vectors were `read`/`write` (--copy-dirlinks/--keep-dirlinks),
which route 100%% through send_directory/secure_opendir -- the one daemon
descent site that already honoured the opt-out -- so insecure=yes followed there
and the test passed. The explicit-path (read-plain/write-plain -> change_dir) and
alt-dest-basis (compare-dest -> basis_link_stat) vectors below exercise the sites
that did NOT honour the opt-out.

Needs root (foreign-owned plant; served as root) + a real TCP peer.
"""

import os
import subprocess
from pathlib import Path

from rsyncfns import (
    RSYNC, RSYNC_PEER, SCRATCHDIR,
    find_attacker_uid, require_tcp, rmtree, rsync_argv, rsync_supports,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

PORT_CUR = 12909
PORT_ORACLE = 12910
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

# The 3.2.7 oracle: prefer a --rsync-bin2 peer if it differs from the build under
# test, else the in-tree static binary. None -> degrade to the static contract.
_repo = Path(__file__).resolve().parent.parent
ORACLE_BIN = None
if RSYNC_PEER != RSYNC:
    ORACLE_BIN = RSYNC_PEER
elif (_repo / 'old_versions' / 'rsync_3.2.7').is_file():
    ORACLE_BIN = str(_repo / 'old_versions' / 'rsync_3.2.7')

# The in-tree oracle is a Linux x86-64 static binary, so on a non-Linux runner it
# is present but not usable: a BSD/macOS host refuses to exec it (ENOEXEC), while
# Solaris execs it but it crashes (SIGSEGV).  Probe it with `--version` and degrade
# to the static contract unless it ran cleanly (rc == 0) -- catching can't-exec
# (OSError), a hang (TimeoutExpired), and a non-zero/signal exit alike -- rather
# than later crashing the oracle daemon launch.
if ORACLE_BIN:
    try:
        _probe = subprocess.run([ORACLE_BIN, '--version'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if _probe.returncode != 0:
            ORACLE_BIN = None
    except (OSError, subprocess.TimeoutExpired):
        ORACLE_BIN = None

TYPES = ('rel-within', 'rel-outside', 'rel-transits', 'abs-outside', 'abs-inside')
OUTSIDE = {'rel-outside', 'abs-outside'}
VECTORS = ('read', 'write', 'read-plain', 'write-plain', 'compare-dest')

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
# Daemon UNDER TEST is always the current build; the oracle (if any) is a second
# daemon on its own port serving the identical modules (3.2.7 ignores the unknown
# "insecure links" param and just follows -- exactly the legacy oracle we want).
# The oracle needs its OWN pid/log file so the two daemons don't fight one lock.
url_cur = start_test_daemon(conf, PORT_CUR, rsync_cmd=RSYNC)
url_oracle = None
if ORACLE_BIN:
    conf_oracle = write_daemon_conf(
        mod_conf,
        {'pid file': str(SCRATCHDIR / 'rsyncd-oracle.pid'),
         'log file': str(SCRATCHDIR / 'rsyncd-oracle.log')},
        name='symlink-escape-matrix-oracle.conf')
    url_oracle = start_test_daemon(conf_oracle, PORT_ORACLE, rsync_cmd=ORACLE_BIN)


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


def attempt(url, insecure, munge, origin, vector, sltype):
    """Build the cell fresh, run `vector` against daemon `url`, return
    (followed, target_outside)."""
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

    def run(*args):
        subprocess.run(rsync_argv(*args),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if vector == 'read':                         # --copy-dirlinks pull (send_directory)
        dest = base / 'dest'
        rmtree(dest); dest.mkdir()
        run('-r', '--copy-dirlinks', f'{url}{name}/', f'{dest}/')
        through = dest / 'evil' / 'tgtfile'
        return (through.is_file() and through.read_text() == SECRET), t_out

    if vector == 'write':                        # --keep-dirlinks push (send_directory)
        sw = base / 'srcw'
        rmtree(sw); sw.mkdir()
        (sw / 'evil').mkdir()
        (sw / 'evil' / 'pwned').write_text(PWNED)
        run('-r', '--keep-dirlinks', f'{sw}/', f'{url}{name}/')
        landed = resolved / 'pwned'
        return (landed.is_file() and landed.read_text() == PWNED), t_out

    if vector == 'read-plain':                   # plain pull of "evil/" (change_dir)
        dest = base / 'dest'
        rmtree(dest); dest.mkdir()
        run('-r', f'{url}{name}/evil/', f'{dest}/')
        through = dest / 'tgtfile'
        return (through.is_file() and through.read_text() == SECRET), t_out

    if vector == 'write-plain':                  # plain push into "evil/" (change_dir)
        sw = base / 'srcw'
        rmtree(sw); sw.mkdir()
        (sw / 'pwned').write_text(PWNED)
        run('-r', f'{sw}/', f'{url}{name}/evil/')
        landed = resolved / 'pwned'
        return (landed.is_file() and landed.read_text() == PWNED), t_out

    # compare-dest: push a file whose content matches the through-the-link basis;
    # if the daemon follows /evil it finds the match and SKIPS the transfer, so
    # the file never lands in the module.  followed == the file was skipped.
    sc = base / 'srcc'
    rmtree(sc); sc.mkdir()
    (sc / 'tgtfile').write_text(SECRET)
    run('-r', '--checksum', '--compare-dest=/evil', f'{sc}/', f'{url}{name}/')
    pushed = moddir / 'tgtfile'
    return (not pushed.is_file()), t_out


def static_followed(insecure, munge, origin, sltype):
    """Fallback contract when no 3.2.7 oracle is present (insecure=yes only).
    The opt-out follows every pre-existing link; an uploaded link is sanitised
    (munge -> none follow; munge-off -> only a rel-within value survives)."""
    if origin == 'uploaded':
        return sltype == 'rel-within' and not munge
    return True


_REPORT = os.environ.get('REPORT_MATRIX')
grid, mismatches, escapes = [], [], []
for insecure in (False, True):
    for munge in (False, True):
        for origin in ('preexist', 'uploaded'):
            for vector in VECTORS:
                for sltype in TYPES:
                    got, t_out = attempt(url_cur, insecure, munge, origin, vector, sltype)
                    esc = got and t_out

                    if not insecure:
                        # Secure default: the ONLY hard contract is "no escape".
                        # (In-module follow differs per resolver family, so we
                        # don't over-pin it -- see module docstring.)
                        line = (f"ins=0 munge={int(munge)} {origin:8} {vector:12} "
                                f"{sltype:11}: followed={int(got)} "
                                f"{'ESCAPE' if esc else ''}")
                        if esc:
                            escapes.append(f"DEFAULT ESCAPE: munge={int(munge)} "
                                           f"{origin}/{vector}/{sltype}")
                    else:
                        # Opt-out: must match stock 3.2.7 (live oracle) or the
                        # static fallback contract.
                        if url_oracle is not None:
                            want, src = attempt(url_oracle, insecure, munge,
                                                origin, vector, sltype)[0], '327'
                        else:
                            want, src = static_followed(insecure, munge, origin, sltype), 'contract'
                        line = (f"ins=1 munge={int(munge)} {origin:8} {vector:12} "
                                f"{sltype:11}: followed={int(got)} want={int(want)}({src})")
                        if got != want:
                            mismatches.append(
                                f"insecure=yes munge={'yes' if munge else 'no'} "
                                f"{origin}/{vector}/{sltype}: followed={got}, "
                                f"{src} expected={want}")
                    grid.append(line)

if _REPORT:
    test_fail("REPORT-MATRIX grid:\n  " + "\n  ".join(grid))
problems = []
if escapes:
    problems.append("secure-default confinement FAILED (out-of-module access):\n  "
                    + "\n  ".join(escapes))
if mismatches:
    problems.append("insecure-links opt-out did NOT match stock 3.2.7:\n  "
                    + "\n  ".join(mismatches))
if problems:
    test_fail("\n".join(problems))

oracle_note = ("vs a real 3.2.7 daemon oracle" if url_oracle
               else "vs the static fallback contract (no 3.2.7 binary present)")
print("daemon-symlink-escape-matrix: 5 link types x preexist/uploaded x "
      f"5 vectors (read/write/read-plain/write-plain/compare-dest) x munge: "
      f"secure default never escapes the module, and insecure links=yes matches "
      f"stock-3.2.7 following uniformly ({oracle_note}).")
