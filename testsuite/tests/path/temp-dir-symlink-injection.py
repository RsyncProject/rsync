#!/usr/bin/env python3

import os
import subprocess
import time

import filecmp
import sys

from harness.rsync import (
    SCRATCHDIR, race_budget, find_attacker_uid,
    rmtree, rsync_argv, test_fail, test_skipped,
)

if os.geteuid() != 0:
    test_skipped("requires root to plant a temp-dir symlink owned by a non-self uid",
                 capability='cross_uid')
ATT_UID = find_attacker_uid()
if ATT_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

NFILES = 40
PINNED = 1000000000

base = SCRATCHDIR / 'tmpdir-inject'
src = base / 'src'
dest = base / 'dest'
tmproot = base / 'tmproot'
td = tmproot / 'td'
tdlink = tmproot / '.tdlink'
outside = base / 'outside'

def build():
    rmtree(base)
    for d in (src, dest, tmproot, outside):
        d.mkdir(parents=True)
    for i in range(NFILES):
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
