import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail, write_daemon_conf,
)

DAEMON_PORT = 12960
SECRET = "PROTECTED-IN-EXCLUDED-SUBTREE\n"

base = SCRATCHDIR / 'backupdirdaemon'
rmtree(base)
base.mkdir()

mod = base / 'mod'
secret = base / 'secret'
if secret.exists():
    rmtree(secret)# excluded subtree (exclude = /secret/)
secret.mkdir(parents=True)

victim = secret / 'f0'
victim.write_text(SECRET)                    # a file the backup must not clobber
mod.mkdir()
(mod / 'f0').write_text("OLD-DESTINATION-FILE-CONTENT\n")

blink = mod / 'blink'                        # euid-owned symlink -> excluded subtree
os.symlink(f'{secret}', blink)

src = base / 'src'
src.mkdir()
(src / 'f0').write_text("NEW\n")

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT)

subprocess.run(
    rsync_argv('-a', '--backup', '--backup-dir=/blink', f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

after = victim.read_text() if victim.exists() else None
if after != SECRET:
    test_fail(
        "the daemon followed a --backup-dir symlink outside the module: "
        f"{victim} is now {after!r} (a peer backed up over a file the module "
        "does not serve).")
print("daemon confines a peer-supplied --backup-dir symlink to the served set")
