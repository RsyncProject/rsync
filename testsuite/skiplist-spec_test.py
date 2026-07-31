#!/usr/bin/env python3
# runtests.py's RSYNC_EXPECT_SKIPPED parser.  The per-platform expected-skip
# lists live in testsuite/skiplist/*.txt and are referenced from the workflows
# as @FILE, so a bug here silently weakens the skip oracle on every CI job (an
# unreadable or truncated list must never read as "expect no skips").  Also
# checks that the committed lists themselves parse and are referenced.

import importlib.util
import os
from pathlib import Path

from rsyncfns import SCRATCHDIR, SRCDIR, test_fail

# srcdir arrives relative under `make installcheck` (--srcdir=../src).  Resolve
# it once: paths built from it are handed back to a srcdir-relative API below,
# which would otherwise re-prefix them.
SRC = Path(SRCDIR).resolve()

spec = importlib.util.spec_from_file_location('runtests', SRC / 'runtests.py')
runtests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtests)

SUITE = str(SRC / 'testsuite')
ERROR = f'exit:{runtests.Exit.ERROR}'


def expand(text_spec, srcdir=None):
    """expand_skip_spec, but reporting a SystemExit as a string."""
    try:
        return runtests.expand_skip_spec(text_spec, srcdir or str(SRC), SUITE)
    except SystemExit as e:
        return f'exit:{e.code}'


def write(name, body):
    p = SCRATCHDIR / name
    p.write_text(body)
    return '@' + str(p)


# --- a bad spec must be a hard error, never a silently smaller expectation --
#
# The sorted/duplicate cases use real test names in descending order, so that
# dropping the sort check cannot be masked by the stale-name check.

for what, arg in (
    ('missing file', '@testsuite/skiplist/does-not-exist.txt'),
    ('unknown test name', 'no-such-test-here'),
    ('unsorted list', write('unsorted.txt', 'sparse\nacls\n')),
    ('duplicate entry', write('dup.txt', 'acls\nacls\n')),
    ('two names on a line', write('twonames.txt', 'acls sparse\n')),
    ('stale name in a list', write('stale.txt', 'gone-away-test\n')),
    ('empty file', write('empty.txt', '')),
    ('comments only', write('comments.txt', '# nothing here\n\n')),
    ('empty entry', 'acls,,sparse'),
    ('trailing comma', 'acls,'),
    ('leading comma', ',acls'),
    ('path in a name', '../testsuite/acls'),
):
    got = expand(arg)
    if got != ERROR:
        test_fail(f'{what}: expected a hard error, got {got!r}')

# --- good specs ------------------------------------------------------------

one = write('one.txt', '# comment\n\nacls  # trailing comment\nsparse\n')
two = write('two.txt', 'devices\nsparse\n')

for what, arg, want in (
    ('@FILE expansion', one, 'acls,sparse'),
    ('composing two lists', f'{one},{two}', 'acls,devices,sparse'),
    ('a bare name with @FILE', f'{one},crtimes', 'acls,crtimes,sparse'),
    ('an empty spec is "no skips"', '', ''),
):
    got = expand(arg)
    if got != want:
        test_fail(f'{what}: expected {want!r}, got {got!r}')

# A relative @FILE resolves against srcdir, not the cwd: `make check` in an
# out-of-tree build directory would otherwise not find the lists.  Passing a
# relative srcdir too is what `make installcheck` does.
for label, srcdir in (('absolute srcdir', str(SRC)),
                      ('relative srcdir', os.path.relpath(SRC))):
    got = expand('@testsuite/skiplist/common.txt', srcdir)
    if got.startswith('exit:') or not got:
        test_fail(f'relative @FILE did not resolve against {label}: {got!r}')

# --- the committed lists ---------------------------------------------------

lists = sorted((SRC / 'testsuite' / 'skiplist').glob('*.txt'))
if len(lists) < 5:
    test_fail(f'expected the per-platform skip lists, found {lists}')
for path in lists:
    got = expand('@' + str(path))
    if got.startswith('exit:') or not got:
        test_fail(f'{path.name} is empty or does not parse: {got!r} '
                  '(run runtests.py to see the diagnostic)')

# Every workflow reference must expand, and every committed list must be
# referenced by some workflow -- a list nothing points at silently stops
# being enforced.
wf = sorted((SRC / '.github' / 'workflows').glob('*.yml'))
refs, referenced = 0, set()
for path in wf:
    for line in path.read_text().splitlines():
        if 'RSYNC_EXPECT_SKIPPED=' not in line:
            continue
        arg = line.split('RSYNC_EXPECT_SKIPPED=', 1)[1].split()[0]
        got = expand(arg)
        if got.startswith('exit:') or not got:
            test_fail(f'{path.name}: unusable RSYNC_EXPECT_SKIPPED spec {arg!r}')
        refs += 1
        for tok in arg.split(','):
            if tok.startswith('@'):
                referenced.add(Path(tok[1:]).name)
if wf:
    if refs == 0:
        test_fail('no workflow references RSYNC_EXPECT_SKIPPED any more')
    orphans = sorted({p.name for p in lists} - referenced)
    if orphans:
        test_fail(f'skip lists no workflow references: {", ".join(orphans)}')
print(f'ok: {len(lists)} skip lists, {refs} workflow references')
