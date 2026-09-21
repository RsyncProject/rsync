#!/usr/bin/env python3
# Regression guard for the module-confinement / exclude-aware operator-path
# resolver: a LEGITIMATE absolute --partial-dir that lives inside the served
# module (a real directory, no symlink, not excluded) must still work.  The
# out-of-module / exclude refusals must not over-block an absolute in-module
# path by tripping on the module root's own ancestors (/home, /srv, ...) while
# the ownership walk descends to it.

import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

base = SCRATCHDIR / 'inmodule'
rmtree(base)
base.mkdir()
mod = base / 'mod'
mod.mkdir()
(mod / 'pdir').mkdir()                   # a real in-module partial dir
(mod / 'f0').write_text("OLD\n")         # existing dest, smaller than src
src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW-LONGER-CONTENT-SO-IT-TRANSFERS\n")

conf = write_daemon_conf([('mod', {'path': str(mod), 'read only': 'no'})])
url = start_test_daemon(conf, 12908)

# Absolute in-module --partial-dir: must be accepted and the file delivered.
proc = subprocess.run(
    rsync_argv('-a', f'--partial-dir={mod}/pdir', f'{src}/', f'{url}mod/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
got = (mod / 'f0').read_text() if (mod / 'f0').exists() else None
if got != "NEW-LONGER-CONTENT-SO-IT-TRANSFERS\n":
    test_fail(
        "a legitimate absolute in-module --partial-dir was over-blocked: "
        f"dest f0 is {got!r} (rc={proc.returncode}, err={proc.stderr.strip()[:200]}). "
        "The module-confine/exclude refusal must allow the module root's ancestors.")
print("legitimate absolute in-module --partial-dir is accepted")
