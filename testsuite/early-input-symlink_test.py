#!/usr/bin/env python3
# Client --early-input=PATH must not follow planted symlinks at attacker-
# controlled path components.
#
# --early-input is an operator-supplied input file on the CLIENT: rsync reads it
# during the in-band daemon handshake (clientserver.c start_inband_exchange) and
# forwards its bytes to the daemon, which feeds them to the module's "early exec"
# hook on stdin. A symlink planted at the path (or a parent component) by another
# uid redirects the read to an attacker-chosen file, whose contents are then sent
# on the wire to (a possibly malicious) daemon -- a content-disclosure primitive,
# the same class as --password-file (item-1 companion).
#
# Leak signal: the daemon's "early exec" hook captures its stdin to a file. The
# planted symlink points at a victim file containing a unique marker. If the
# client follows the symlink, the marker reaches the daemon and lands in the
# capture -> leak observed. With the fix the open is refused ("failed to open"),
# the handshake aborts before the module request, and the marker never appears.
#
# Both leaf and parent-component variants are exercised.
#
# Requires root (to lchown the planted symlink to a non-self uid -- the attacker
# simulation); skips otherwise.

import os
import shlex
import pwd
import subprocess
import time

from rsyncfns import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12904
SANITY_MARKER = "EARLY_INPUT_SANITY_DELIVERED"
LEAK_MARKER = "EARLY_INPUT_LEAK_MARKER_DO_NOT_DISCLOSE"


if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)")

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            NOBODY_UID = u
            break
    except KeyError:
        continue
if NOBODY_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

base = SCRATCHDIR / 'earlyinput'
rmtree(base)
base.mkdir()
moddir = base / 'mod'
moddir.mkdir()

# The "early exec" hook records its stdin (which carries the forwarded early
# input after the NUL-terminated arg list) to a capture file we can inspect.
capture = base / 'early_capture'
hook = base / 'earlyhook.sh'
hook.write_text(f'#!/bin/sh\ncat > {shlex.quote(str(capture))} 2>/dev/null\nexit 0\n')
hook.chmod(0o755)

conf = write_daemon_conf([
    ('mod', {'path': moddir, 'read only': 'yes', 'early exec': str(hook)}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def list_with_early_input(path):
    """List the module with --early-input=path; return the CompletedProcess."""
    if capture.exists():
        capture.unlink()
    return subprocess.run(
        rsync_argv('--early-input=' + str(path), f'{url}mod/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def captured_contains(marker, secs=5):
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if capture.is_file():
            try:
                if marker in capture.read_text(errors='replace'):
                    return True
            except OSError:
                pass
        time.sleep(0.05)
    return False


# Sanity: a real (non-symlinked) early-input file is delivered to the hook.
real = base / 'real_early'
real.write_text(SANITY_MARKER + '\n')
real.chmod(0o600)
list_with_early_input(real)
if not captured_contains(SANITY_MARKER):
    test_fail(
        "sanity check failed: a non-symlinked --early-input file was not "
        "delivered to the daemon's early-exec hook; the test machinery is "
        f"broken. stderr/capture: {capture.read_text(errors='replace') if capture.exists() else '(none)'!r}")

# The victim file the attacker wants the client to read and forward.
victim = base / 'victim_early'
victim.write_text(LEAK_MARKER + '\n')
os.chmod(victim, 0o600)

# Leaf plant.
leaf_plant = base / 'early_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)
list_with_early_input(leaf_plant)
if captured_contains(LEAK_MARKER, secs=2):
    test_fail(
        f"--early-input followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): the client read {victim} via the symlink and forwarded "
        f"its contents to the daemon, where the marker {LEAK_MARKER!r} landed in "
        f"the early-exec hook's stdin. This is a content-disclosure primitive. "
        f"Fix: refuse planted symlinks at the --early-input open.")

# Parent-component plant.
parent_real = base / 'parent_real'
parent_real.mkdir()
(parent_real / 'victim_in_target').write_text(LEAK_MARKER + '\n')
os.chmod(parent_real / 'victim_in_target', 0o600)
parent_plant = base / 'parent_link'
os.symlink(parent_real, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)
list_with_early_input(parent_plant / 'victim_in_target')
if captured_contains(LEAK_MARKER, secs=2):
    test_fail(
        f"--early-input followed a planted PARENT-component symlink (owned by "
        f"uid {NOBODY_UID}): {parent_plant} -> {parent_real} was traversed and "
        f"victim_in_target forwarded to the daemon ({LEAK_MARKER!r} in the "
        f"early-exec capture). This is the /tmp/somedir/-class parent-flip case. "
        f"Fix: per-component path walk refusing parent symlinks owned by "
        f"untrusted uids.")

print("early-input-symlink: leaf + parent plants refused")
