#!/usr/bin/env python3
# Finding [26]: support/rrsync leaves -D / --specials enabled in a restricted
# (non-"/") directory, so a restricted SSH user can have the spawned rsync
# create special files (FIFOs, sockets; device nodes too if the receiver is
# privileged) inside the served tree -- objects the forced-command policy did
# not intend to permit, and that the user cannot otherwise create through the
# wrapper (it only runs rsync, not a shell).
#
# Part 1 shows --specials is the toggle that enables special-file creation (a
# FIFO is created with it, skipped without it).  Part 2 is the fix: a restricted
# rrsync no longer creates special files even when -D/--specials is requested.
#
# Rejecting -D outright broke plain `rsync -a` (which bundles -rlptgoD) for every
# restricted rrsync (see rrsync-archive-mode), so rather than reject the transfer
# the wrapper forces --no-D, which strips the device/special semantics (they are
# skipped, not created -- exactly the part-1 "without --specials" behaviour)
# while the rest of the transfer proceeds.  Runs at any uid.

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, patched_rrsync, rmtree, rsync_argv, test_fail,
)

base = SCRATCHDIR / 'rrsync-specials'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
os.mkfifo(src / 'evil_fifo')

# --- Part 1: --specials enables special-file creation in the destination.
withd = base / 'with'
withd.mkdir()
subprocess.run(rsync_argv('-rlptgo', '--specials', f'{src}/', f'{withd}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not (withd / 'evil_fifo').is_fifo():
    test_fail("premise check failed: --specials should create the FIFO in the dest")
without = base / 'without'
without.mkdir()
subprocess.run(rsync_argv('-rlptgo', f'{src}/', f'{without}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if (without / 'evil_fifo').exists():
    test_fail("premise check failed: without --specials the FIFO should be skipped")

# --- Part 2: a restricted rrsync accepts -D/--specials but forces --no-D, so
# the special-file creation shown in part 1 cannot be requested through the
# wrapper.  Use an argv-recording stub rsync to confirm both that the option is
# accepted (the `rsync -a` regression is gone) and that --no-D is forced.
record = base / 'argv'
stub = base / 'rsync-record'
stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >' + str(record) + '\n')
stub.chmod(0o755)
rrsync = patched_rrsync(base, rsync_path=str(stub))
restricted = base / 'restricted'
restricted.mkdir(exist_ok=True)

for opt in ('-D', '--specials'):
    if record.exists():
        record.unlink()
    cmd = f'rsync --server --sender {opt} . .'
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': cmd}
    r = subprocess.run([str(rrsync), str(restricted)], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"restricted rrsync rejected `{opt}` instead of stripping it "
                  f"(exit {r.returncode}): {r.stderr.strip()}")
    forwarded = record.read_text().split('\n') if record.exists() else []
    if '--no-D' not in forwarded:
        test_fail(f"restricted rrsync did not force --no-D for `{opt}`: {forwarded}")

print("rrsync-specials: --specials creates special files in the served tree; "
      "restricted rrsync strips them by forcing --no-D (accepts the transfer, "
      "denies creation)")
