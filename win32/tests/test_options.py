#!/usr/bin/env python3
"""Assorted options that the port has to get right.

--remove-source-files exercises the MSG_SUCCESS wire format, which differs
between a forked local server and a separate-process one; the rest are
everyday flags worth a regression check.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import (FROM, SCRATCH, TO, check, compare_trees, ok, rewrite,
                     rsync, write)

# --- --remove-source-files -------------------------------------------------
write(FROM / 'move1.txt', 'first\n')
write(FROM / 'move2.txt', 'second\n')
write(FROM / 'sub' / 'move3.txt', 'third\n')

rsync('-rt', '--remove-source-files', f'{FROM}/', f'{TO}/')

for name in ('move1.txt', 'move2.txt', 'sub/move3.txt'):
    check((TO / name).is_file(), f'{name} was not delivered')
    check(not (FROM / name).exists(),
          f'--remove-source-files left {name} behind')
# Directories are not removed, only files.
check((FROM / 'sub').is_dir(), '--remove-source-files removed a directory')

# --- --dry-run changes nothing --------------------------------------------
dry = SCRATCH / 'dry'
write(FROM / 'newfile.txt', 'created after the move\n')
rsync('-rt', '--dry-run', f'{FROM}/', f'{dry}/')
check(not dry.exists() or not any(dry.iterdir()),
      '--dry-run created files at the destination')

# --- --include / --exclude ordering ---------------------------------------
src = SCRATCH / 'filtered'
write(src / 'keep.txt', 'keep\n')
write(src / 'drop.dat', 'drop\n')
write(src / 'nested' / 'keep.txt', 'keep nested\n')
write(src / 'nested' / 'drop.dat', 'drop nested\n')

dst = SCRATCH / 'filtered-out'
rsync('-rt', '--include=*/', '--include=*.txt', '--exclude=*',
      f'{src}/', f'{dst}/')
check((dst / 'keep.txt').is_file(), '--include=*.txt did not keep keep.txt')
check((dst / 'nested' / 'keep.txt').is_file(),
      '--include did not descend into nested/')
check(not (dst / 'drop.dat').exists(), '--exclude=* did not drop drop.dat')

# --- --compress round-trips -----------------------------------------------
zsrc = SCRATCH / 'zsrc'
zdst = SCRATCH / 'zdst'
write(zsrc / 'text.txt', 'highly compressible\n' * 2000)
rsync('-rt', '-z', f'{zsrc}/', f'{zdst}/')
compare_trees(zsrc, zdst, '-z transfer')

# --- --backup keeps the old copy ------------------------------------------
bsrc = SCRATCH / 'bsrc'
bdst = SCRATCH / 'bdst'
write(bsrc / 'file.txt', 'version one\n')
rsync('-rt', f'{bsrc}/', f'{bdst}/')
rewrite(bsrc / 'file.txt', 'version two\n')
rsync('-rt', '--backup', f'{bsrc}/', f'{bdst}/')
check((bdst / 'file.txt').read_text() == 'version two\n',
      '--backup did not install the new version')
check((bdst / 'file.txt~').read_text() == 'version one\n',
      '--backup did not preserve the old version at file.txt~')

ok('--remove-source-files, --dry-run, filters, -z and --backup all correct')
