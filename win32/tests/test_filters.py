#!/usr/bin/env python3
"""Filter rules: --filter, merge files, --cvs-exclude and the size filters.

The upstream exclude/merge/filter-depth/cvs-exclude/size-filter/
prune-empty-dirs tests are all platform-neutral -- they are about rule
parsing and matching, not about anything Unix does -- so the same ground is
covered here.  What differs on Windows is that the rules are matched against
names that arrived through a path layer which rewrites separators, so a rule
anchored with '/' has to keep working against a source given as C:\\dir.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SCRATCH, check, ok, rsync, write


def tree(name):
    """A fresh source tree with a predictable shape."""
    root = SCRATCH / name
    write(root / 'keep.txt', 'keep\n')
    write(root / 'drop.dat', 'drop\n')
    write(root / 'sub' / 'keep.txt', 'nested keep\n')
    write(root / 'sub' / 'drop.dat', 'nested drop\n')
    write(root / 'sub' / 'deeper' / 'keep.txt', 'deep keep\n')
    write(root / 'sub' / 'deeper' / 'drop.dat', 'deep drop\n')
    return root


def present(root, *rel):
    for r in rel:
        check((root / r).exists(), f'{r} should be present under {root}')


def absent(root, *rel):
    for r in rel:
        check(not (root / r).exists(), f'{r} should NOT be present under {root}')


# --- --filter with an anchored rule ----------------------------------------
# A leading '/' anchors to the transfer root, so only the top-level drop.dat
# goes; the nested ones stay.  This is the case most at risk on Windows,
# where the source is given with a drive letter and backslashes.
src = tree('anchored')
dst = SCRATCH / 'anchored-to'
rsync('-r', '--filter=- /drop.dat', f'{src}\\', f'{dst}\\')
absent(dst, 'drop.dat')
present(dst, 'keep.txt', 'sub/drop.dat', 'sub/deeper/drop.dat')

# --- --exclude-from --------------------------------------------------------
src = tree('excl-from')
dst = SCRATCH / 'excl-from-to'
rules = write(SCRATCH / 'rules.txt', '*.dat\n')
rsync('-r', f'--exclude-from={rules}', f'{src}/', f'{dst}/')
present(dst, 'keep.txt', 'sub/keep.txt', 'sub/deeper/keep.txt')
absent(dst, 'drop.dat', 'sub/drop.dat', 'sub/deeper/drop.dat')

# --- a per-directory merge file (dir-merge) --------------------------------
# '.rsync-filter' in a directory applies to that directory and below.  Here
# the rule lives in sub/, so sub/drop.dat and sub/deeper/drop.dat go but the
# top-level drop.dat stays.
src = tree('dirmerge')
write(src / 'sub' / '.rsync-filter', '- drop.dat\n')
dst = SCRATCH / 'dirmerge-to'
rsync('-r', '--filter=dir-merge /.rsync-filter', f'{src}/', f'{dst}/')
present(dst, 'drop.dat', 'keep.txt', 'sub/keep.txt')
absent(dst, 'sub/drop.dat', 'sub/deeper/drop.dat')
# The filter file itself still travels: hiding it takes a second -F.
check((dst / 'sub' / '.rsync-filter').exists(),
      'dir-merge without "-" should still transfer the filter file')

# --- -F, and the second -F that hides the merge file -----------------------
# One -F is shorthand for the dir-merge rule above; repeating it adds
# "- .rsync-filter", so the rule file stops travelling with the tree.
src = tree('dashF')
write(src / 'sub' / '.rsync-filter', '- drop.dat\n')

dst = SCRATCH / 'dashF-to'
rsync('-r', '-F', f'{src}/', f'{dst}/')
absent(dst, 'sub/drop.dat')
present(dst, 'drop.dat', 'sub/keep.txt', 'sub/.rsync-filter')

dst = SCRATCH / 'dashFF-to'
rsync('-r', '-F', '-F', f'{src}/', f'{dst}/')
absent(dst, 'sub/drop.dat', 'sub/.rsync-filter')
present(dst, 'drop.dat', 'sub/keep.txt')

# --- a merged rule file (. modifier) ---------------------------------------
src = tree('merge')
merged = write(SCRATCH / 'merged-rules', '- *.dat\n')
dst = SCRATCH / 'merge-to'
rsync('-r', f'--filter=. {merged}', f'{src}/', f'{dst}/')
present(dst, 'keep.txt', 'sub/keep.txt')
absent(dst, 'drop.dat', 'sub/drop.dat')

# --- --cvs-exclude ---------------------------------------------------------
# -C brings in a built-in list (core, *.o, .git/, ...) and honours .cvsignore.
src = SCRATCH / 'cvs'
write(src / 'source.c', 'int main(void){return 0;}\n')
write(src / 'source.o', 'object\n')
write(src / 'core', 'core dump\n')
write(src / 'notes.txt', 'notes\n')
write(src / 'sub' / '.cvsignore', 'ignored.txt\n')
write(src / 'sub' / 'ignored.txt', 'should not travel\n')
write(src / 'sub' / 'kept.txt', 'should travel\n')
dst = SCRATCH / 'cvs-to'
rsync('-r', '-C', f'{src}/', f'{dst}/')
present(dst, 'source.c', 'notes.txt', 'sub/kept.txt')
absent(dst, 'source.o', 'core', 'sub/ignored.txt')

# --- filter depth: a rule applies below where it is defined ----------------
# An unanchored '- drop.dat' from the command line matches at every depth,
# while 'sub/deeper/drop.dat' anchored at the root matches only that one.
src = tree('depth')
dst = SCRATCH / 'depth-to'
rsync('-r', '--filter=- /sub/deeper/drop.dat', f'{src}/', f'{dst}/')
present(dst, 'drop.dat', 'sub/drop.dat')
absent(dst, 'sub/deeper/drop.dat')

# --- --max-size / --min-size ----------------------------------------------
src = SCRATCH / 'sizes'
write(src / 'tiny.txt', 'x' * 10)
write(src / 'medium.txt', 'x' * 5000)
write(src / 'large.txt', 'x' * 200000)

dst = SCRATCH / 'maxsize-to'
rsync('-r', '--max-size=100K', f'{src}/', f'{dst}/')
present(dst, 'tiny.txt', 'medium.txt')
absent(dst, 'large.txt')

dst = SCRATCH / 'minsize-to'
rsync('-r', '--min-size=1K', f'{src}/', f'{dst}/')
present(dst, 'medium.txt', 'large.txt')
absent(dst, 'tiny.txt')

# --- --prune-empty-dirs ----------------------------------------------------
# Directories left empty by the filters must not be created at all.
src = SCRATCH / 'prune'
write(src / 'has' / 'keep.txt', 'keep\n')
write(src / 'hasnot' / 'drop.dat', 'drop\n')
write(src / 'empty' / '.placeholder', 'x\n')
(src / 'truly-empty').mkdir(parents=True, exist_ok=True)

dst = SCRATCH / 'prune-to'
rsync('-r', '-m', '--exclude=*.dat', '--exclude=.placeholder',
      f'{src}/', f'{dst}/')
present(dst, 'has/keep.txt')
absent(dst, 'hasnot', 'empty', 'truly-empty')

# Without -m the same run keeps the now-empty directories, which is what
# makes the assertions above mean something.
dst = SCRATCH / 'noprune-to'
rsync('-r', '--exclude=*.dat', '--exclude=.placeholder', f'{src}/', f'{dst}/')
present(dst, 'has/keep.txt', 'hasnot', 'empty', 'truly-empty')

ok('filter rules, merge files, --cvs-exclude, size filters and -m all correct')
