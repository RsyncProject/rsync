#!/usr/bin/env python3
"""Links are not reproduced, but they are reported.

Neither symlinks nor hard links are first-class on Windows -- creating a
symlink needs a privilege most accounts lack -- so this build leaves
SUPPORT_LINKS and SUPPORT_HARD_LINKS off and treats each as the ordinary
file it resolves to.  Silently flattening a tree would be a poor outcome,
so win32/win32links.c lists what it met as the run ends.

This checks both halves of that: the copy is a plain file, and the run says
so.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import os
import subprocess

from wintest import (FROM, SCRATCH, TO, check, have_capability, make_junction,
                     ok, rsync, skip, write)

# The point of this test is that the build reports links rather than
# reproducing them; if a build ever does support them, it needs a different
# test, not this one.
check(not have_capability('symlinks'),
      'this build reports symlink support; test_links_reported assumes it does not')
check(not have_capability('hardlinks'),
      'this build reports hard-link support; test_links_reported assumes it does not')


def hardlink(target, link):
    res = subprocess.run(['cmd', '/c', 'mklink', '/H', str(link), str(target)],
                         capture_output=True, text=True)
    return res.returncode == 0


write(FROM / 'plain.txt', 'an ordinary file\n')
write(FROM / 'linked.txt', 'shared by two names\n')
write(FROM / 'target' / 'inside.txt', 'behind the junction\n')

made_hardlink = hardlink(FROM / 'linked.txt', FROM / 'alias.txt')
made_junction = make_junction(FROM / 'junc', FROM / 'target')

if not made_hardlink and not made_junction:
    skip('this filesystem creates neither hard links nor junctions')

proc = rsync('-rt', f'{FROM}/', f'{TO}/')
report = proc.stderr

# Whatever it did with them, the transfer itself must succeed and deliver
# readable content.
check((TO / 'plain.txt').read_text() == 'an ordinary file\n',
      'the ordinary file did not arrive intact')

if made_hardlink:
    # Both names arrive, each as its own file rather than a shared inode.
    for name in ('linked.txt', 'alias.txt'):
        check((TO / name).is_file(), f'{name} did not arrive')
        check((TO / name).read_text() == 'shared by two names\n',
              f'{name} has the wrong content')
    check(os.stat(TO / 'linked.txt').st_nlink == 1,
          'the destination file is still hard-linked; this build should not '
          'reproduce links')
    check('hard-linked' in report,
          f'the run did not report the hard link it met:\n{report}')
    for name in ('linked.txt', 'alias.txt'):
        check(name in report, f'{name} is missing from the hard-link report')
    print('hard links: copied independently and reported')

if made_junction:
    # A junction is followed, so its contents arrive as a real directory.
    check((TO / 'junc' / 'inside.txt').read_text() == 'behind the junction\n',
          'the junction was not followed into a real directory')
    check(not os.path.islink(str(TO / 'junc')),
          'the destination still holds a reparse point')
    check('symlink' in report,
          f'the run did not report the symlink it met:\n{report}')
    check('junc' in report, f'junc is missing from the symlink report:\n{report}')
    print('symlink/junction: followed, copied and reported')

# The explanation must be there too, not just a bare list.
check('does not reproduce links' in report,
      f'the report did not explain itself:\n{report}')

# A tree with no links at all must stay quiet.
quiet_src = SCRATCH / 'quiet'
write(quiet_src / 'only.txt', 'nothing linked here\n')
proc = rsync('-rt', f'{quiet_src}/', f'{SCRATCH}/quiet-dst/')
check('does not reproduce links' not in proc.stderr,
      f'a link-free transfer still printed a link report:\n{proc.stderr}')

ok('links are followed, copied as plain files, and reported at the end')
