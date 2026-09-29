#!/usr/bin/env python3
"""Reporting: -i itemised output, --out-format and the info/debug switches.

The upstream itemize test pins the exact eleven-character change string for
every kind of update.  Most of those columns describe attributes this build
does not carry -- ownership, group, ACLs, symlink times -- so the ones that
are meaningful here are checked individually rather than by diffing a whole
canonical listing that could only be Unix's.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SCRATCH, check, ok, rewrite, rsync, write


def itemised(proc, name):
    """The change string rsync printed for `name`, or None."""
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() == name:
            return parts[0]
    return None


src = SCRATCH / 'from'
dst = SCRATCH / 'to'
write(src / 'first.txt', 'one\n')
write(src / 'sub' / 'nested.txt', 'nested\n')

# --- a new file and a new directory ---------------------------------------
proc = rsync('-rt', '-i', f'{src}/', f'{dst}/')
item = itemised(proc, 'first.txt')
check(item is not None, f'no itemised line for first.txt:\n{proc.stdout}')
check(item.startswith('>f+++++++'),
      f'a newly sent file should be >f+++++++++, got {item!r}')
item = itemised(proc, 'sub/')
check(item is not None and item.startswith('cd+++++++'),
      f'a newly created directory should be cd+++++++++, got {item!r}')

# --- an unchanged run reports nothing -------------------------------------
proc = rsync('-rt', '-i', f'{src}/', f'{dst}/')
check(itemised(proc, 'first.txt') is None,
      f'an unchanged file should not be itemised:\n{proc.stdout}')

# --- content change: the checksum and time columns ------------------------
rewrite(src / 'first.txt', 'one changed\n')
proc = rsync('-rt', '-i', f'{src}/', f'{dst}/')
item = itemised(proc, 'first.txt')
check(item is not None and item.startswith('>f'),
      f'a changed file should start >f, got {item!r}')
check(item[3] == 's' or item[4] == 't' or item[4] == 'T',
      f'a changed file should report a size or time difference, got {item!r}')

# --- a time-only change ----------------------------------------------------
# The quick check is size plus mtime, so a file whose mtime alone moved is
# re-sent rather than merely restamped: the update column stays '>', the size
# column reports no difference, and the time column does.
import os
os.utime(src / 'first.txt', (1_650_000_000, 1_650_000_000))
proc = rsync('-rt', '-i', f'{src}/', f'{dst}/')
item = itemised(proc, 'first.txt')
check(item is not None, f'no itemised line for an mtime-only change')
check(item[0] == '>' and item[3] == '.' and item[4] == 't',
      f'an mtime-only change should be >f..t......, got {item!r}')

# With --checksum the content is compared, so the same file needs only its
# time corrected -- and now the update column really does go to '.'.
os.utime(src / 'first.txt', (1_660_000_000, 1_660_000_000))
proc = rsync('-rt', '-i', '--checksum', f'{src}/', f'{dst}/')
item = itemised(proc, 'first.txt')
check(item is not None and item[0] == '.' and item[4] == 't',
      f'--checksum on an mtime-only change should be .f...t....., got {item!r}')

# --- --out-format ----------------------------------------------------------
# %n is the name, %l the length, %i the same change string as -i.
write(src / 'formatted.txt', 'x' * 1234)
proc = rsync('-rt', '--out-format=FMT|%i|%n|%l', f'{src}/', f'{dst}/')
lines = [ln for ln in proc.stdout.splitlines() if ln.startswith('FMT|')]
check(lines, f'--out-format produced no output:\n{proc.stdout}')
match = [ln for ln in lines if '|formatted.txt|' in ln]
check(match, f'--out-format did not report formatted.txt:\n{proc.stdout}')
check(match[0].endswith('|1234'),
      f'%l should have been the 1234-byte length, got {match[0]!r}')

# --- --itemize-changes is the long spelling of -i -------------------------
write(src / 'another.txt', 'another\n')
proc_i = rsync('-rt', '-i', '--dry-run', f'{src}/', f'{dst}/')
proc_long = rsync('-rt', '--itemize-changes', '--dry-run', f'{src}/', f'{dst}/')
check(proc_i.stdout == proc_long.stdout,
      '-i and --itemize-changes disagreed:\n'
      f'{proc_i.stdout}\n--- vs ---\n{proc_long.stdout}')

# --- --stats reports a summary --------------------------------------------
proc = rsync('-rt', '--stats', f'{src}/', f'{dst}/')
check('Number of files' in proc.stdout,
      f'--stats printed no file count:\n{proc.stdout}')
check('Total file size' in proc.stdout,
      f'--stats printed no total size:\n{proc.stdout}')

# --- --info=NAME is the modern spelling of -v -----------------------------
write(src / 'named.txt', 'named\n')
proc = rsync('-rt', '--info=NAME', f'{src}/', f'{dst}/')
check(any(ln.strip() == 'named.txt' for ln in proc.stdout.splitlines()),
      f'--info=NAME did not name the transferred file:\n{proc.stdout}')

# --- --quiet says nothing --------------------------------------------------
write(src / 'silent.txt', 'silent\n')
proc = rsync('-rt', '-q', f'{src}/', f'{dst}/')
check(proc.stdout.strip() == '',
      f'-q still produced output:\n{proc.stdout}')

ok('-i change strings, --out-format, --stats, --info and -q all correct')
