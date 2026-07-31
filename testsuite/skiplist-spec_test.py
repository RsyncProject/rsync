#!/usr/bin/env python3
# runtests.py's RSYNC_EXPECT_SKIPPED parser.  The per-platform expected-skip
# lists live in testsuite/skiplist/*.txt and are referenced from the workflows
# as @FILE, so a bug here silently weakens the skip oracle on every CI job (an
# unreadable list must never read as "expect no skips").  Also checks that the
# committed lists themselves parse, are sorted, and name real tests.

import importlib.util

from rsyncfns import SCRATCHDIR, SRCDIR, test_fail

spec = importlib.util.spec_from_file_location('runtests', SRCDIR / 'runtests.py')
runtests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtests)

SUITE = str(SRCDIR / 'testsuite')


def expand(text_spec):
    """expand_skip_spec, but reporting a SystemExit as a string."""
    try:
        return runtests.expand_skip_spec(text_spec, str(SRCDIR), SUITE)
    except SystemExit as e:
        return f'exit:{e.code}'


def write(name, body):
    p = SCRATCHDIR / name
    p.write_text(body)
    return '@' + str(p)


# --- a bad spec must be a hard error, never a silently empty expectation ----

for what, arg in (
    ('missing file', '@testsuite/skiplist/does-not-exist.txt'),
    ('unknown test name', 'no-such-test-here'),
    ('unsorted list', write('unsorted.txt', 'zebra_z\nacls\n')),
    ('duplicate entry', write('dup.txt', 'acls\nacls\n')),
    ('two names on a line', write('twonames.txt', 'acls sparse\n')),
    ('stale name in a list', write('stale.txt', 'gone-away-test\n')),
):
    got = expand(arg)
    if got != f'exit:{runtests.Exit.ERROR}':
        test_fail(f'{what}: expected a hard error, got {got!r}')

# --- good specs ------------------------------------------------------------

one = write('one.txt', '# comment\n\nacls  # trailing comment\nsparse\n')
two = write('two.txt', 'devices\nsparse\n')

if expand(one) != 'acls,sparse':
    test_fail(f'@FILE expansion: got {expand(one)!r}')
if expand(f'{one},{two}') != 'acls,devices,sparse':
    test_fail(f'composing two lists: got {expand(f"{one},{two}")!r}')
if expand(f'{one},crtimes') != 'acls,crtimes,sparse':
    test_fail(f'mixing a bare name with @FILE: got {expand(f"{one},crtimes")!r}')
if expand('') != '':
    test_fail('an empty spec must expand to an empty set')

# A relative @FILE resolves against srcdir, not the cwd: `make check` in an
# out-of-tree build directory would otherwise not find the lists.
rel = expand('@testsuite/skiplist/common.txt')
if 'exit:' in rel or not rel:
    test_fail(f'relative @FILE did not resolve against srcdir: {rel!r}')

# --- the committed lists ---------------------------------------------------

lists = sorted((SRCDIR / 'testsuite' / 'skiplist').glob('*.txt'))
if len(lists) < 4:
    test_fail(f'expected the per-platform skip lists, found {lists}')
for path in lists:
    got = expand('@' + str(path))
    if got.startswith('exit:'):
        test_fail(f'{path.name} does not parse (rerun to see the diagnostic)')

# The workflows must reference lists that exist and parse.  Failing here means
# a workflow points at a list that was renamed or removed.
wf = sorted((SRCDIR / '.github' / 'workflows').glob('*.yml'))
refs = 0
for path in wf:
    for line in path.read_text().splitlines():
        if 'RSYNC_EXPECT_SKIPPED=' not in line:
            continue
        arg = line.split('RSYNC_EXPECT_SKIPPED=', 1)[1].split()[0]
        got = expand(arg)
        if got.startswith('exit:'):
            test_fail(f'{path.name}: unusable RSYNC_EXPECT_SKIPPED spec {arg!r}')
        refs += 1
if wf and refs == 0:
    test_fail('no workflow references RSYNC_EXPECT_SKIPPED any more')
print(f'ok: {len(lists)} skip lists, {refs} workflow references')
