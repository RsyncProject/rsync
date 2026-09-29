#!/usr/bin/env python3
"""Path semantics: -R, --no-implied-dirs, -d, --mkpath, duplicates, missing.

This is the group most worth having on Windows, because every one of these
options is about rsync constructing destination paths from source paths --
and here the source arrives as C:\\dir\\sub while the wire protocol and the
destination both speak '/'.  The upstream relative, relative-implied, dirs,
mkpath, duplicates and missing tests all cover this ground; what they add
that cannot run here is symlink handling, which is left out rather than
worked around.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import os

from wintest import SCRATCH, check, ok, rsync, write

os.chdir(SCRATCH)

src = SCRATCH / 'from'
write(src / 'top.txt', 'top\n')
write(src / 'a' / 'one.txt', 'one\n')
write(src / 'a' / 'b' / 'deep.txt', 'deep\n')
write(src / 'a' / 'b' / 'c' / 'deeper.txt', 'deeper\n')

# --- -R reproduces the whole source path ----------------------------------
# Driven with a relative source so the reproduced path is predictable.
dst = SCRATCH / 'to-R'
rsync('-r', '-R', 'from/a/b', f'{dst}/')
check((dst / 'from' / 'a' / 'b' / 'deep.txt').is_file(),
      '-R did not reproduce from/a/b/ at the destination')
check(not (dst / 'from' / 'a' / 'one.txt').exists(),
      '-R brought along a sibling outside the named path')

# --- the ./ cut point ------------------------------------------------------
# Everything left of ./ is the root and is not reproduced.
dst = SCRATCH / 'to-dot'
rsync('-r', '-R', 'from/./a/b', f'{dst}/')
check((dst / 'a' / 'b' / 'deep.txt').is_file(),
      './ in an -R path did not anchor the reproduction at a/')
check(not (dst / 'from').exists(),
      './ should have cut everything to its left')

# --- --no-implied-dirs -----------------------------------------------------
# The implied parents are still created, but their attributes are not taken
# from the source; the transferred leaf is unaffected either way.
dst = SCRATCH / 'to-noimplied'
rsync('-r', '-R', '--no-implied-dirs', 'from/a/b', f'{dst}/')
check((dst / 'from' / 'a' / 'b' / 'deep.txt').is_file(),
      '--no-implied-dirs lost the transferred file')

# --- -d transfers a directory without recursing ---------------------------
dst = SCRATCH / 'to-dirs'
rsync('-d', f'{src}/', f'{dst}/')
check((dst / 'top.txt').is_file(), '-d did not transfer a top-level file')
check((dst / 'a').is_dir(), '-d did not create the top-level directory')
check(not (dst / 'a' / 'one.txt').exists(), '-d should not have recursed')

# --- --mkpath creates missing destination components ----------------------
# Negative control first, otherwise the success below proves nothing.
deep_dst = SCRATCH / 'to-mk' / 'x' / 'y' / 'z'
proc = rsync('-r', f'{src}/', f'{deep_dst}/', expect=None)
check(proc.returncode != 0,
      'a missing multi-level destination path should fail without --mkpath')
check(not deep_dst.exists(),
      'the failed run created the destination path anyway')

rsync('-r', '--mkpath', f'{src}/', f'{deep_dst}/')
check((deep_dst / 'a' / 'b' / 'deep.txt').is_file(),
      '--mkpath did not create the destination path and transfer into it')

# --mkpath naming a file destination rather than a directory.
file_dst = SCRATCH / 'to-mkfile' / 'p' / 'q' / 'named.txt'
rsync('--mkpath', str(src / 'top.txt'), str(file_dst))
check(file_dst.is_file(), '--mkpath did not create the path for a file target')
check(file_dst.read_text() == 'top\n', '--mkpath delivered the wrong content')

# --- the same source named repeatedly is copied once ----------------------
# clean_flist() dedupes, so ten identical arguments must not produce ten
# entries -- nor a "duplicate" complaint.
dst = SCRATCH / 'to-dup'
args = ['-r', '-R'] + ['from/a'] * 10 + [f'{dst}/']
proc = rsync(*args)
check((dst / 'from' / 'a' / 'b' / 'deep.txt').is_file(),
      'the deduplicated transfer lost a file')
check('duplicate' not in (proc.stdout + proc.stderr).lower(),
      f'rsync complained about duplicates:\n{proc.stdout}\n{proc.stderr}')

# Counted the honest way: one line per file, however many times it was named.
proc = rsync('-r', '-R', '-i', *(['from/a'] * 10), f'{SCRATCH}/to-dup2/')
sent = [ln for ln in proc.stdout.splitlines() if ln.startswith('>f')]
check(len(sent) == 3,
      f'expected 3 files sent once each, got {len(sent)}:\n{proc.stdout}')

# --- a missing source is reported, and does not stop the rest -------------
dst = SCRATCH / 'to-missing'
proc = rsync('-r', str(src / 'top.txt'), str(src / 'nonexistent.txt'),
             f'{dst}/', expect=23)
check((dst / 'top.txt').is_file(),
      'a missing source argument stopped the arguments that were present')
combined = proc.stdout + proc.stderr
check('nonexistent.txt' in combined,
      f'the missing source was not named in the error:\n{combined}')

ok('-R, ./ cut points, --no-implied-dirs, -d, --mkpath, dedupe and missing args')
