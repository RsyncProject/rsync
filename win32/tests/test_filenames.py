#!/usr/bin/env python3
"""File names Windows has to carry through unchanged.

The embedded manifest selects UTF-8 as the active code page, so names
outside the legacy ANSI code page survive the narrow Win32 calls in
win32/*.c.  Spaces and shell metacharacters matter because the local-server
path builds a command line for CreateProcess.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import FROM, TO, check, compare_trees, ok, rsync, write

NAMES = [
    'plain.txt',
    'a file with spaces.txt',
    "single'quote.txt",
    # No double quote: NTFS forbids it in a name, along with * ? : < > | \ /
    'semi;colon.txt',
    'amper&sand.txt',
    'dollar$sign.txt',
    'paren(these)s.txt',
    'per%cent.txt',
    'caret^hat.txt',
    'equals=sign.txt',
    'comma,comma.txt',
    'hash#mark.txt',
    'at@sign.txt',
    'plus+plus.txt',
    'bracket[]s.txt',
    'naïve-café.txt',
    'ελληνικά.txt',
    '日本語のファイル.txt',
    'emoji-🙂.txt',
    'mixed 名前 with spaces.txt',
]

for i, name in enumerate(NAMES):
    write(FROM / name, f'contents of entry {i}\n')
write(FROM / 'sub dir with spaces' / '日本語.txt', 'nested unicode\n')

rsync('-rt', f'{FROM}/', f'{TO}/')
compare_trees(FROM, TO, 'awkward file names')

for name in NAMES:
    check((TO / name).is_file(), f'{name!r} did not arrive')

# The names must survive a second, incremental pass unchanged -- a mangled
# name would show up as a spurious re-transfer or a duplicate.
proc = rsync('-rt', '--stats', f'{FROM}/', f'{TO}/')
for line in proc.stdout.splitlines():
    if 'regular files transferred' in line:
        count = line.rsplit(':', 1)[1].strip().replace(',', '')
        check(count == '0',
              f'incremental pass re-sent {count} files; a name is not stable')

compare_trees(FROM, TO, 'after the incremental pass')
ok(f'{len(NAMES) + 1} awkward names round-tripped')
