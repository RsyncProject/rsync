import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, forced_protocol, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12961
SECRET = "PROTECTED-OUTSIDE-MODULE\n"
PUSHED = "NEW\n"
OLD_DEST = "OLD-DESTINATION-FILE-CONTENT\n"

base = SCRATCHDIR / 'partialdirdaemon'
rmtree(base)
base.mkdir()

mod = base / 'mod'
secret = base / 'secret'
if secret.exists():
    rmtree(secret)
secret.mkdir(parents=True)

victim = secret / 'f0'
victim.write_text(SECRET)                    # a file the partial must not clobber
mod.mkdir()
dest = mod / 'f0'
dest.write_text(OLD_DEST)

blink = mod / 'blink'                        # euid-owned symlink -> outside module
os.symlink(f'{secret}', blink)

src = base / 'src'
src.mkdir()
(src / 'f0').write_text(PUSHED)

conf = write_daemon_conf(
    [('mod', {'path': str(mod), 'read only': 'no'})])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-a', '--delay-updates', '--partial-dir=/blink',
               f'{src}/', f'{url}mod/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# The security oracle -- the outside-module victim must never be clobbered --
# holds at every protocol and is checked unconditionally below.  The stronger
# "daemon actively rejects the forced --partial-dir operand" behavior only
# applies at protocol 30+; at protocol 29 the operand is handled differently and
# the transfer completes normally (dest replaced, victim still untouched), so
# gate just that secondary check.
proto = forced_protocol()
dest_after = dest.read_text() if dest.exists() else None
if proto is None or proto >= 30:
    if proc.returncode == 0 or dest_after != OLD_DEST:
        test_fail(
            "the daemon did not reject the forced --partial-dir staging path: "
            f"rsync exited {proc.returncode} and {dest} is now {dest_after!r}. "
            "If the operand were ignored, the upload would replace the destination "
            f"with {PUSHED!r}.")
else:
    # At protocol 29 the operand is handled differently: the transfer must still
    # complete normally (no escape) rather than fail for an unrelated reason, so
    # require the known-good outcome instead of skipping the check entirely.
    if proc.returncode != 0 or dest_after != PUSHED:
        test_fail(
            "protocol-29 --partial-dir run did not complete normally: rsync exited "
            f"{proc.returncode} and {dest} is {dest_after!r} (expected a clean "
            f"transfer replacing it with {PUSHED!r})")

after = victim.read_text() if victim.exists() else None
if after != SECRET:
    test_fail(
        "the daemon followed a --partial-dir symlink outside the module: "
        f"{victim} is now {after!r} (a peer backed up over a file the module "
        "does not serve).")
print("daemon confines a peer-supplied --partial-dir symlink to the served set")
