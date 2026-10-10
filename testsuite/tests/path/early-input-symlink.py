#!/usr/bin/env python3

import os
import shlex
import pwd
import subprocess
import time

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, rmtree, rsync_argv, start_test_daemon, test_fail,
    test_skipped,
)

DAEMON_PORT = 12904
SANITY_MARKER = "EARLY_INPUT_SANITY_DELIVERED"
LEAK_MARKER = "EARLY_INPUT_LEAK_MARKER_DO_NOT_DISCLOSE"

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)", capability='cross_uid')

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
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

base = SCRATCHDIR / 'earlyinput'
rmtree(base)
base.mkdir()
moddir = base / 'mod'
moddir.mkdir()

capture = base / 'early_capture'
hook = base / 'earlyhook.sh'
hook.write_text(f'#!/bin/sh\ncat > {shlex.quote(str(capture))} 2>/dev/null\nexit 0\n')
hook.chmod(0o755)

conf = write_daemon_conf([
    ('mod', {'path': moddir, 'read only': 'yes', 'early exec': str(hook)}),
])
url = start_test_daemon(conf, DAEMON_PORT)

def list_with_early_input(path):
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

real = base / 'real_early'
real.write_text(SANITY_MARKER + '\n')
real.chmod(0o600)
list_with_early_input(real)
if not captured_contains(SANITY_MARKER):
    test_fail(
        "sanity check failed: a non-symlinked --early-input file was not "
        "delivered to the daemon's early-exec hook; the test machinery is "
        f"broken. stderr/capture: {capture.read_text(errors='replace') if capture.exists() else '(none)'!r}")

victim = base / 'victim_early'
victim.write_text(LEAK_MARKER + '\n')
os.chmod(victim, 0o600)

leaf_plant = base / 'early_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)
list_with_early_input(leaf_plant)
if captured_contains(LEAK_MARKER, secs=2):
    test_fail(
        f'--early-input leaked {LEAK_MARKER!r} from {victim} through '
        f'untrusted uid {NOBODY_UID} symlink')

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
        f'--early-input leaked {LEAK_MARKER!r} through untrusted uid '
        f'{NOBODY_UID} parent symlink {parent_plant} -> {parent_real}')

print("early-input-symlink: leaf + parent plants refused")
