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


def expand(text_spec, srcdir=None, suitedir=None):
    """expand_skip_spec, but reporting a SystemExit as a string."""
    try:
        return runtests.expand_skip_spec(text_spec, srcdir or str(SRC),
                                         suitedir or SUITE)
    except SystemExit as e:
        return f'exit:{e.code}'


def write(name, body):
    p = SCRATCHDIR / name
    if isinstance(body, bytes):
        p.write_bytes(body)
    else:
        p.write_text(body)
    return '@' + str(p)


# A stand-in suite whose entries make each malformed name *look* real, so that
# removing the guard for it cannot be masked by the "no such test" check.
FAKE = SCRATCHDIR / 'fakesuite'
FAKE.mkdir(exist_ok=True)
(FAKE / 'acls sparse_test.py').write_text('')   # if the one-name-per-line
(FAKE / 'foo,bar_test.py').write_text('')       # ... comma and
(FAKE / 'adir_test.py').mkdir(exist_ok=True)    # ... regular-file guards went

# --- a bad spec must be a hard error, never a silently smaller expectation --
#
# The sorted/duplicate cases use real test names in descending order, so that
# dropping the sort check cannot be masked by the stale-name check.

for what, arg, suite in (
    ('missing file', '@testsuite/skiplist/does-not-exist.txt', None),
    ('unknown test name', 'no-such-test-here', None),
    ('unsorted list', write('unsorted.txt', 'sparse\nacls\n'), None),
    ('duplicate entry', write('dup.txt', 'acls\nacls\n'), None),
    ('stale name in a list', write('stale.txt', 'gone-away-test\n'), None),
    ('empty file', write('empty.txt', ''), None),
    ('comments only', write('comments.txt', '# nothing here\n\n'), None),
    ('not valid text', write('binary.txt', b'acls\n\xff\xfe not utf-8\n'), None),
    ('empty entry', 'acls,,sparse', None),
    ('trailing comma', 'acls,', None),
    ('leading comma', ',acls', None),
    ('path in a name', '../testsuite/acls', None),
    ('two names on a line', write('twonames.txt', 'acls sparse\n'), str(FAKE)),
    ('comma in a name', write('comma.txt', 'foo,bar\n'), str(FAKE)),
    ('a directory, not a test', 'adir', str(FAKE)),
):
    got = expand(arg, suitedir=suite)
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
# out-of-tree build directory would otherwise not find the lists.  srcdir is
# itself relative under `make installcheck` (--srcdir=../src), so check a
# non-trivial one too -- in-tree, os.path.relpath() would just be ".".
got = expand('@testsuite/skiplist/common.txt', str(SRC))
if got.startswith('exit:') or not got:
    test_fail(f'relative @FILE did not resolve against srcdir: {got!r}')

cwd = os.getcwd()
try:
    os.chdir(SRC.parent)
    got = expand('@testsuite/skiplist/common.txt', SRC.name)
finally:
    os.chdir(cwd)
if got.startswith('exit:') or not got:
    test_fail(f'@FILE did not resolve against a relative srcdir: {got!r}')

# --- the committed lists ---------------------------------------------------

# backport.txt is not an expected-skip list: it is an EXCLUDE list that a
# backport branch ships to say which of this suite's tests it cannot run, and
# fleettest reads it from the BUILT tree rather than from any workflow.  It only
# appears here when a backport tree has been overlaid with this suite, so it is
# exempt from the checks below that assume a workflow points at the file.
BACKPORT_LIST = 'backport.txt'

lists = sorted(p for p in (SRC / 'testsuite' / 'skiplist').glob('*.txt')
               if p.name != BACKPORT_LIST)
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
# '-name' removals.  fleettest emits these for a host that can genuinely run a
# test its platform list expects to skip; the name being dropped lives inside an
# @FILE, so no composer can subtract it before runtests expands the spec.
common = SRC / 'testsuite' / 'skiplist' / 'common.txt'
if common.is_file():
    full = expand('@' + str(common))
    victim = full.split(',')[0]
    got = expand(f'@{common},-{victim}')
    if got == full or victim in got.split(','):
        test_fail(f'-{victim} did not remove it from the expanded list')
    if len(got.split(',')) != len(full.split(',')) - 1:
        test_fail(f'-{victim} removed more than the one name')
    # Order must not matter: removals apply after every addition.
    if expand(f'-{victim},@{common}') != got:
        test_fail('a removal before the list it removes from behaved differently')
    # A removal that removes nothing is stale, and shrinking the expected set
    # quietly is the whole failure mode this parser exists to prevent.
    for stale in (f'@{common},-no-such-test-here', f'@{common},-{victim},-{victim}'):
        if expand(stale) != ERROR:
            test_fail(f'a stale removal was accepted: {stale!r}')

print(f'ok: {len(lists)} skip lists, {refs} workflow references')
