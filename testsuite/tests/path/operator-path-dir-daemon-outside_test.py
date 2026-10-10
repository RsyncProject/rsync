#!/usr/bin/env python3
# A daemon must not let a peer-supplied --partial-dir/--backup-dir symlink resolve
# OUTSIDE the served module at all (not just into an excluded subtree).
# (Companion to the exclude-bypass tests; reported via Omar Elsayed's findings.)
#
# An in-module symlink owned by the daemon euid whose target is ABSOLUTE (or
# climbs out with ../) points to a sibling directory outside the module root.
# An unprivileged peer overwrites a dest file with --backup/--partial-dir aimed
# at that symlink, moving/staging the data OUTSIDE the served set -- modifying
# files the module never exposed.  The module-confined resolver must refuse a
# resolved path that leaves the module root.
#
# Runs unprivileged: the boundary under test is the module root, not a uid.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

def run_case(label, opt, extra, port):
    base = SCRATCHDIR / f'outside-{label}'
    rmtree(base)
    base.mkdir()
    mod = base / 'mod'
    mod.mkdir()
    outside = base / 'outside'           # sibling of the module -- NOT served
    outside.mkdir()
    (mod / 'f0').write_text("OLD-DEST-CONTENT\n")   # existing dest -> overwritten
    src = base / 'src'
    src.mkdir()
    (src / 'f0').write_text("NEW\n")
    # euid-owned symlink inside the module, absolute target outside the module.
    os.symlink(str(outside), mod / 'elink')

    conf = write_daemon_conf(
        [('mod', {'path': str(mod), 'read only': 'no'})],
        {'pid file': str(SCRATCHDIR / f'rsyncd-outside-{label}.pid')},
        name=f'outside-{label}.conf')
    url = start_test_daemon(conf, port)
    subprocess.run(
        rsync_argv('-a', *extra, f'{opt}=/elink', f'{src}/', f'{url}mod/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    leaked = [os.path.join(r, f) for r, _d, fs in os.walk(outside) for f in fs]
    if leaked:
        test_fail(
            f"{label}: the daemon followed an in-module symlink whose target is "
            f"OUTSIDE the module root and wrote there: {leaked} (a peer modified "
            "files the module does not serve). The resolver must confine an "
            "operator/peer path to the module root.")


run_case('backup', '--backup-dir', ['--backup'], 12905)
run_case('partial', '--partial-dir', [], 12909)
print("daemon confines peer --partial-dir/--backup-dir symlinks to the module root")
