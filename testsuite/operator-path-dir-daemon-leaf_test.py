#!/usr/bin/env python3
# A leaf-level module exclude (e.g. exclude = /public/f0, where the dir /public
# is itself served) must not be bypassable by a peer-supplied --partial-dir/
# --backup-dir symlink.  (Codex-review follow-up to Omar Elsayed's findings.)
#
# The parent dir is served (it has other, non-excluded files), so resolution is
# allowed to descend into it; only the single leaf f0 is hidden.  An in-module
# euid-owned symlink -> that served dir, used as the operator-path, targets the
# hidden leaf -- which the parent-only ownership walk did not filter-check.  The
# leaf must be filter-checked too, so the peer cannot delete/overwrite it.
#
# Runs unprivileged: the boundary is the module exclude, not a uid.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

SECRET = "LEAF-EXCLUDED-IN-A-SERVED-DIR\n"


def run_case(label, opt, extra, port):
    base = SCRATCHDIR / f'leaf-{label}'
    rmtree(base)
    base.mkdir()
    mod = base / 'mod'
    pub = mod / 'public'                 # served dir; only /public/f0 is excluded
    pub.mkdir(parents=True)
    (pub / 'f0').write_text(SECRET)
    (pub / 'served.txt').write_text("served\n")   # sibling => 'public' is served
    (mod / 'f0').write_text("OLD-DEST-CONTENT\n")
    os.symlink('public', mod / 'blink')
    src = base / 'src'
    src.mkdir()
    (src / 'f0').write_text("NEW\n")

    conf = write_daemon_conf(
        [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/public/f0'})],
        {'pid file': str(SCRATCHDIR / f'rsyncd-leaf-{label}.pid')},
        name=f'leaf-{label}.conf')
    url = start_test_daemon(conf, port)
    subprocess.run(
        rsync_argv('-a', *extra, f'{opt}=/blink', f'{src}/', f'{url}mod/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    after = (pub / 'f0').read_text() if (pub / 'f0').exists() else None
    if after == SECRET:
        test_fail(
            f"{label}: the daemon refused a {opt} symlink to a leaf-excluded file "
            f"that stock rsync (3.2.7) reaches: public/f0 is unchanged ({after!r}). "
            "The daemon exclude filter is name-based ('blink' is not excluded), not "
            "a symlink boundary; it must not block this.")


run_case('backup', '--backup-dir', ['--backup'], 12906)
run_case('partial', '--partial-dir', [], 12907)
print("daemon exclude is name-based: an operator-path symlink reaches a "
      "leaf-excluded file in a served dir (3.2.7-equivalent)")
