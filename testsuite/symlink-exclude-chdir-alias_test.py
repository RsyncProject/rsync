#!/usr/bin/env python3
# Daemon module-exclude under-block via an ALLOWED symlinked chdir whose logical
# path then mislabels descendants.  (Probe for codex residual #2.)
#
# The module excludes a *nested* subtree: exclude = /public/secret/.  public/ is
# NOT excluded, so an in-module symlink  blink2 -> public/  is followed when used
# as the destination.  After that chdir the daemon's logical cwd is "blink2"
# while the physical cwd is "public"; a file then pushed to "secret/x" lands in
# public/secret/x -- the excluded subtree -- because the exclude check sees the
# logical "blink2/secret", not the physical "public/secret".
#
# RED = under-block reproduces (the alias defeats the nested exclude).  Runs
# unprivileged.
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12982
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'symlink-exclude-chdir-alias'
rmtree(base)
base.mkdir()

mod = base / 'mod'
mod.mkdir()
public = mod / 'public'                        # NOT excluded
public.mkdir()
secret = public / 'secret'                     # excluded: /public/secret/
secret.mkdir()
victim = secret / 'x'
victim.write_text(SECRET)

os.symlink('public/', mod / 'blink2')          # allowed symlink -> public/

src = base / 'src'
(src / 'secret').mkdir(parents=True)
(src / 'secret' / 'x').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no', 'exclude': '/public/secret/'})])
url = start_test_daemon(conf, DAEMON_PORT)

# Destination is the allowed symlink blink2 -> public; the pushed secret/x then
# resolves to public/secret/x (excluded).
subprocess.run(
    rsync_argv('-a', '--keep-dirlinks', f'{src}/', f'{url}mod/blink2/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after == SECRET:
    test_fail(
        "the daemon refused a symlinked-chdir push that stock rsync (3.2.7) "
        f"follows: blink2/secret/x did not reach public/secret/ ({victim} is still "
        f"{after!r}).  The exclude is name-based (the logical alias path), not the "
        "physical target; it must not block this.")
print("daemon exclude is name-based: a push via an allowed symlinked chdir reaches "
      "the nested excluded subtree (3.2.7-equivalent)")
