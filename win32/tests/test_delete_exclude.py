#!/usr/bin/env python3
"""--delete must not delete what --exclude protects.

This is a regression test with teeth.  A local server is a separate process
on Windows, not a fork, so it inherits nothing: the filter list has to
travel over the pipe.  When it didn't, the receiver deleted exactly the
files --exclude was there to protect (see LOCAL_SERVER_SHARES_STATE in
rsync.h).

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import FROM, TO, check, ok, rsync, write

write(FROM / 'keep.txt', 'in the source\n')
write(FROM / 'sub' / 'also-keep.txt', 'also in the source\n')

# Files that exist only at the destination.  The .log ones are excluded, so
# --delete must leave them; the .tmp one is not, so it must go.
write(TO / 'keep.txt', 'in the source\n')
write(TO / 'precious.log', 'destination-only, protected by --exclude\n')
write(TO / 'sub' / 'nested.log', 'destination-only, protected too\n')
write(TO / 'stale.tmp', 'destination-only, not protected\n')

rsync('-rt', '--delete', '--exclude=*.log', f'{FROM}/', f'{TO}/')

check((TO / 'precious.log').is_file(),
      '--delete removed precious.log despite --exclude=*.log')
check((TO / 'sub' / 'nested.log').is_file(),
      '--delete removed sub/nested.log despite --exclude=*.log')
check(not (TO / 'stale.tmp').exists(),
      '--delete failed to remove stale.tmp, which nothing protected')
check((TO / 'sub' / 'also-keep.txt').is_file(),
      'the transfer did not deliver sub/also-keep.txt')

# --exclude-from should behave the same way.
write(TO / 'second.log', 'protected via --exclude-from\n')
rules = FROM.parent / 'rules.txt'
write(rules, '*.log\n')
rsync('-rt', '--delete', f'--exclude-from={rules}', f'{FROM}/', f'{TO}/')
check((TO / 'second.log').is_file(),
      '--delete removed second.log despite --exclude-from')

ok('--delete honours --exclude and --exclude-from')
