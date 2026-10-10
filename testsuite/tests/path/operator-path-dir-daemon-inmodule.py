#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

base = SCRATCHDIR / 'inmodule'
rmtree(base)
base.mkdir()
mod = base / 'mod'
mod.mkdir()
(mod / 'pdir').mkdir()
(mod / 'f0').write_text("OLD\n")
src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW-LONGER-CONTENT-SO-IT-TRANSFERS\n")

conf = write_daemon_conf([('mod', {'path': str(mod), 'read only': 'no'})])
url = start_test_daemon(conf, 12908)

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
