#!/usr/bin/env python3
"""Push and pull over ssh.

Needs a reachable host with rsync installed and key-based login, named in
RSYNC_WIN_TEST_HOST (e.g. "user@192.168.0.10"); skipped otherwise, since
there is nothing sensible to fall back to.  Set RSYNC_WIN_TEST_DIR to
choose the remote scratch path (default /tmp/rsync-win-test).

This is the path that matters most in practice: the receiving side splits
into a generator and a receiver, which on Windows is a thread rather than a
forked process (win32/win32fork.c).

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import hashlib
import os
import shutil
import subprocess

from wintest import (FROM, SCRATCH, TO, check, compare_trees, make_test_tree,
                     ok, pseudo_random, rewrite, rsync, skip, write)

host = os.environ.get('RSYNC_WIN_TEST_HOST')
if not host:
    skip('set RSYNC_WIN_TEST_HOST=user@host to run the ssh transfer tests')

remote_dir = os.environ.get('RSYNC_WIN_TEST_DIR', '/tmp/rsync-win-test')

if not shutil.which('ssh'):
    skip('no ssh client on PATH')


def ssh(command, expect=0):
    # The peer is a UTF-8 Unix host; decode its output as such rather than
    # as the Windows console code page, or non-ASCII names come back as
    # mojibake and every comparison against them fails.
    proc = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         host, command],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if expect is not None and proc.returncode != expect:
        skip(f'ssh to {host} failed ({proc.returncode}): {proc.stderr.strip()}')
    return proc.stdout


ssh('true')                                    # proves login works
ssh(f'rm -rf {remote_dir} && mkdir -p {remote_dir}')

make_test_tree(FROM)
write(FROM / 'unicode-日本.txt', 'unicode over the wire\n')

# --- push ------------------------------------------------------------------
rsync('-rt', '--delete', f'{FROM}/', f'{host}:{remote_dir}/')

listing = ssh(f'cd {remote_dir} && find . -type f | sort')
for name in ('./plain.txt', './sub/nested.txt', './unicode-日本.txt'):
    check(name in listing.splitlines(),
          f'{name} is missing from the remote after the push:\n{listing}')

remote_sums = ssh(
    f'cd {remote_dir} && find . -type f -printf "%P\\n" | sort '
    f'| xargs -d "\\n" md5sum')
remote = {}
for line in remote_sums.splitlines():
    digest, _, path = line.partition('  ')
    remote[path.replace('\\', '/')] = digest

local = {}
for dirpath, _dirs, files in os.walk(FROM):
    for name in files:
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, FROM).replace(os.sep, '/')
        local[rel] = hashlib.md5(open(full, 'rb').read()).hexdigest()

check(local == remote,
      'push mismatch:\n'
      f'  only local:  {sorted(set(local) - set(remote))[:10]}\n'
      f'  only remote: {sorted(set(remote) - set(local))[:10]}\n'
      f'  differing:   {[k for k in set(local) & set(remote) if local[k] != remote[k]][:10]}')

# --- pull ------------------------------------------------------------------
rsync('-rt', '--delete', f'{host}:{remote_dir}/', f'{TO}/')
compare_trees(FROM, TO, 'pull of what we just pushed')

# --- delta over the wire ---------------------------------------------------
blob = FROM / 'blob.bin'
write(blob, pseudo_random(300000, seed=5))
rsync('-rt', f'{FROM}/', f'{host}:{remote_dir}/')

edited = bytearray(pseudo_random(300000, seed=5))
for i in range(150000, 150100):
    edited[i] ^= 0x5A
rewrite(blob, bytes(edited))

proc = rsync('-rt', '--stats', f'{FROM}/', f'{host}:{remote_dir}/')
for line in proc.stdout.splitlines():
    if line.startswith('Matched data:'):
        matched = int(line.split()[2].replace(',', ''))
        check(matched > 150000,
              f'delta matched only {matched} bytes; block matching looks broken')
        print(f'delta matched {matched} bytes over the wire')

rsync('-rt', '--delete', f'{host}:{remote_dir}/', f'{TO}/')
compare_trees(FROM, TO, 'pull after the delta push')

ssh(f'rm -rf {remote_dir}')
ok(f'push, pull and delta over ssh to {host} all correct')
