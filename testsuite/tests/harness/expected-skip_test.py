#!/usr/bin/env python3
"""Expected-skip parser compatibility"""

import importlib.util
import os
from pathlib import Path

from rsyncfns import SCRATCHDIR, SRCDIR, test_fail

SRC = Path(SRCDIR).resolve()
SUITE = str(SRC / 'testsuite')
spec = importlib.util.spec_from_file_location('runtests', SRC / 'testsuite' / 'runtests.py')
runtests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtests)
ERROR = f'exit:{runtests.Exit.ERROR}'


def expand(value, srcdir=None, suitedir=None):
    try:
        return runtests.expand_skip_spec(value, srcdir or str(SRC), suitedir or SUITE)
    except SystemExit as error:
        return f'exit:{error.code}'


def write(name, body):
    path = SCRATCHDIR / name
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
        path.write_text(body)
    return '@' + str(path)


fake = SCRATCHDIR / 'fakesuite'
tests = fake / 'tests'
tests.mkdir(parents=True, exist_ok=True)
(tests / 'nested').mkdir()
(tests / 'nested' / 'nested_test.py').write_text('')
(tests / 'acls sparse_test.py').write_text('')
(tests / 'foo,bar_test.py').write_text('')
(tests / 'adir_test.py').mkdir(exist_ok=True)

for what, value, suite in (
    ('missing file', '@does-not-exist.txt', None),
    ('unknown test name', 'no-such-test-here', None),
    ('unsorted list', write('unsorted.txt', 'sparse\nacls\n'), None),
    ('duplicate entry', write('duplicate.txt', 'acls\nacls\n'), None),
    ('stale name', write('stale.txt', 'gone-away-test\n'), None),
    ('empty file', write('empty.txt', ''), None),
    ('comments only', write('comments.txt', '# none\n'), None),
    ('invalid text', write('binary.txt', b'acls\n\xff\xfe\n'), None),
    ('empty entry', 'acls,,sparse', None),
    ('trailing comma', 'acls,', None),
    ('leading comma', ',acls', None),
    ('path in name', '../testsuite/acls', None),
    ('two names', write('two-names.txt', 'acls sparse\n'), str(fake)),
    ('comma in name', write('comma.txt', 'foo,bar\n'), str(fake)),
    ('directory', 'adir', str(fake)),
):
    if expand(value, suitedir=suite) != ERROR:
        test_fail(f'{what} was accepted')

one = write('one.txt', '# comment\nacls # trailing comment\nsparse\n')
two = write('two.txt', 'devices\nsparse\n')
for what, value, want in (
    ('file expansion', one, 'acls,sparse'),
    ('composed files', f'{one},{two}', 'acls,devices,sparse'),
    ('file and name', f'{one},crtimes', 'acls,crtimes,sparse'),
    ('empty spec', '', ''),
):
    got = expand(value)
    if got != want:
        test_fail(f'{what}: expected {want!r}, got {got!r}')
if expand('nested', suitedir=str(fake)) != 'nested':
    test_fail('nested test name was not accepted')

backport = SCRATCHDIR / 'backport'
(backport / 'testsuite' / 'skiplist').mkdir(parents=True, exist_ok=True)
(backport / 'testsuite' / 'skiplist' / 'backport.txt').write_text('nested\n')
if runtests.read_backport_exclude(str(backport), str(fake)) != {'nested'}:
    test_fail('backport exclusions were not read from the build tree')

relative = SCRATCHDIR / 'relative'
relative.mkdir(exist_ok=True)
(relative / 'expected.txt').write_text('acls\n')
cwd = os.getcwd()
try:
    os.chdir(relative.parent)
    if expand('@expected.txt', srcdir=relative.name) != 'acls':
        test_fail('relative file did not resolve against srcdir')
finally:
    os.chdir(cwd)

full = expand(one)
victim = full.split(',')[0]
without = expand(f'{one},-{victim}')
if victim in without.split(',') or len(without.split(',')) != len(full.split(',')) - 1:
    test_fail('name removal failed')
if expand(f'-{victim},{one}') != without:
    test_fail('name removal depends on order')
for stale in (f'{one},-no-such-test-here', f'{one},-{victim},-{victim}'):
    if expand(stale) != ERROR:
        test_fail('stale name removal was accepted')

print('ok: expected-skip parser')
