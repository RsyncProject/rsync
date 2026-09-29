#!/usr/bin/env python3
"""--files-from, including the implied --relative and the -0 variant.

--files-from turns on --relative unless it is explicitly countermanded, so
the listed names reproduce their whole path at the destination.  That makes
this as much a path test as a selection test, which is why it is worth
having here: the names in the list use '/' while the source root arrives as
a Windows path.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SCRATCH, check, ok, rsync, write

src = SCRATCH / 'from'
write(src / 'top.txt', 'top\n')
write(src / 'skipme.txt', 'not listed\n')
write(src / 'a' / 'one.txt', 'one\n')
write(src / 'a' / 'two.txt', 'two\n')
write(src / 'a' / 'b' / 'deep.txt', 'deep\n')
write(src / 'c' / 'other.txt', 'other\n')

# --- the basic form --------------------------------------------------------
listing = write(SCRATCH / 'list.txt', 'top.txt\na/one.txt\na/b/deep.txt\n')
dst = SCRATCH / 'to'
rsync('-r', f'--files-from={listing}', f'{src}/', f'{dst}/')

for rel in ('top.txt', 'a/one.txt', 'a/b/deep.txt'):
    check((dst / rel).is_file(), f'--files-from did not deliver {rel}')
# The implied --relative reproduces the intermediate directories...
check((dst / 'a' / 'b').is_dir(),
      '--files-from should imply --relative and create a/b/')
# ...but nothing that was not asked for.
check(not (dst / 'skipme.txt').exists(),
      '--files-from delivered a file that was not in the list')
check(not (dst / 'a' / 'two.txt').exists(),
      '--files-from delivered a sibling that was not in the list')
check(not (dst / 'c').exists(),
      '--files-from created a directory it was never asked for')

# --- a listed directory is transferred without recursing -------------------
# Naming a directory in the list sends the directory itself; its contents
# come along only because -r was given.  Without -r it stays empty.
listing = write(SCRATCH / 'dirlist.txt', 'c\n')
dst = SCRATCH / 'to-dir-norec'
rsync(f'--files-from={listing}', f'{src}/', f'{dst}/')
check((dst / 'c').is_dir(), '--files-from did not create the listed directory')
check(not (dst / 'c' / 'other.txt').exists(),
      'a listed directory should not recurse without -r')

dst = SCRATCH / 'to-dir-rec'
rsync('-r', f'--files-from={listing}', f'{src}/', f'{dst}/')
check((dst / 'c' / 'other.txt').is_file(),
      '-r with --files-from did not recurse into the listed directory')

# --- --no-relative flattens ------------------------------------------------
listing = write(SCRATCH / 'flat.txt', 'a/one.txt\na/b/deep.txt\n')
dst = SCRATCH / 'to-flat'
rsync('-r', '--no-relative', f'--files-from={listing}', f'{src}/', f'{dst}/')
check((dst / 'one.txt').is_file(),
      '--no-relative should have put one.txt at the top')
check((dst / 'deep.txt').is_file(),
      '--no-relative should have put deep.txt at the top')
check(not (dst / 'a').exists(),
      '--no-relative should not have reproduced the a/ path')

# --- --files-from=- reads the list from stdin ------------------------------
# rsync must not confuse the list on stdin with the protocol stream.
dst = SCRATCH / 'to-stdin'
proc = rsync('-r', '--files-from=-', f'{src}/', f'{dst}/',
             stdin_text='top.txt\na/two.txt\n')
check((dst / 'top.txt').is_file(), '--files-from=- did not deliver top.txt')
check((dst / 'a' / 'two.txt').is_file(),
      '--files-from=- did not deliver a/two.txt')

# --- -0 (NUL-separated) ----------------------------------------------------
# The point of -0 is names that contain a newline; Windows will not allow one
# in a filename, so this checks the parsing rather than such a name.
listing = SCRATCH / 'list0.bin'
write(listing, b'top.txt\x00a/one.txt\x00')
dst = SCRATCH / 'to-null'
rsync('-r', '-0', f'--files-from={listing}', f'{src}/', f'{dst}/')
check((dst / 'top.txt').is_file(), '-0 --files-from did not deliver top.txt')
check((dst / 'a' / 'one.txt').is_file(),
      '-0 --files-from did not deliver a/one.txt')

# Read without -0 the same file yields one line, and the NUL terminates it as
# a C string, so only the first name survives.  Asserting that the second one
# is lost is what shows -0 changes the parsing rather than being a no-op.
dst = SCRATCH / 'to-null-wrong'
rsync('-r', f'--files-from={listing}', f'{src}/', f'{dst}/', expect=(0, 23))
check(not (dst / 'a' / 'one.txt').exists(),
      'a NUL-separated list was parsed as multiple names without -0')

# --- --files-from with --delete -------------------------------------------
# Only the listed names are candidates for deletion, so an unlisted file
# already at the destination survives.
dst = SCRATCH / 'to-delete'
rsync('-r', f'{src}/', f'{dst}/')
write(dst / 'bystander.txt', 'not from the source\n')
listing = write(SCRATCH / 'del.txt', 'top.txt\n')
rsync('-r', '--delete', f'--files-from={listing}', f'{src}/', f'{dst}/')
check((dst / 'bystander.txt').is_file(),
      '--delete with --files-from removed a file outside the list')
check((dst / 'a' / 'one.txt').is_file(),
      '--delete with --files-from removed an unlisted source file')

ok('--files-from covers relative, stdin, -0, recursion and --delete')
