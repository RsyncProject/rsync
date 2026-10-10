#!/usr/bin/env python3

import re
from pathlib import Path

from harness import runner
from harness import metadata, test_name
from harness.rsync import SRCDIR, test_fail

metadata(transports={'pipe'}, tags={'harness'})

src = Path(SRCDIR).resolve()

assignment = re.compile(
    r'RSYNC_TEST_PROFILES=(\S*\$\{\{\s*matrix\.profiles\s*\}\}\S*|\S+)')
matrix_ref = re.compile(r'\$\{\{\s*matrix\.profiles\s*\}\}')
matrix_value = re.compile(r'^\s+profiles:\s+(\S+)\s*$')
common_profiles = {'non-asan', 'root', 'self-peer', 'no-idn', 'no-xxhash', 'no-zstd-threads'}
required_profiles = {
    'freebsd-build.yml': common_profiles | {'freebsd'},
    'netbsd-build.yml': common_profiles | {'netbsd'},
    'openbsd-build.yml': common_profiles | {'openbsd'},
    'solaris-build.yml': common_profiles | {'solaris'},
    'asan-build.yml': {'nonroot', 'self-peer', 'linux'},
}
valgrind_matrix = {
    frozenset({'pipe', 'non-asan', 'nonroot', 'self-peer', 'linux', 'valgrind'}),
    frozenset({'non-asan', 'nonroot', 'self-peer', 'linux', 'valgrind'}),
    frozenset({'pipe', 'non-asan', 'root', 'self-peer', 'linux', 'valgrind'}),
    frozenset({'non-asan', 'root', 'self-peer', 'linux', 'valgrind'}),
}

def expand_profiles(line, matrix):
    match = assignment.search(line)
    if not match:
        test_fail('invalid RSYNC_TEST_PROFILES assignment')
    value = match.group(1)
    if not matrix_ref.search(value):
        return [value]
    if not matrix:
        test_fail('matrix profile expression has no values')
    return [matrix_ref.sub(profile, value) for profile in matrix]

test_paths = runner.collect_tests(str(src / 'testsuite'), [])
known_tests = {test_name(path) for path in test_paths}

references = 0
workflows = sorted((src / '.github' / 'workflows').glob('*.yml'))
for path in workflows:
    lines = path.read_text().splitlines()
    workflow = '\n'.join(lines)
    if (path.name == 'ubuntu-version-mix.yml'
            and 'tcp=(--use-tcp --daemon-tests-only)' not in workflow):
        test_fail('ubuntu-version-mix.yml: TCP pass repeats transport-independent tests')
    matrix = sorted(match.group(1) for line in lines if (match := matrix_value.match(line)))
    profile_lines = [line for line in lines if 'RSYNC_TEST_PROFILES=' in line]
    specs = [value for line in profile_lines for value in expand_profiles(line, matrix)]
    names = {name for value in specs for name in value.split(',')}
    for value in specs:
        for name in value.split(','):
            runner.load_profile(src / 'testsuite' / 'profiles' / f'{name}.json', known_tests)
        references += 1

    required = required_profiles.get(path.name)
    if required:
        missing = required - names
        if missing:
            test_fail(f'{path.name}: missing profiles: {", ".join(sorted(missing))}')
        if not any('test-results/*.json' in line for line in lines):
            test_fail(f'{path.name}: profile receipts are not retained')
        if not any('if: always()' in line for line in lines):
            test_fail(f'{path.name}: failed profile receipts are not retained')

    if path.name == 'valgrind.yml':
        actual = {frozenset(value.split(',')) for value in matrix}
        if actual != valgrind_matrix:
            test_fail('valgrind.yml: incomplete profile matrix')
        if ('VALGRIND_SCRATCH: /tmp/' not in workflow
                or 'scratchbase="$VALGRIND_SCRATCH"' not in workflow):
            test_fail('valgrind.yml: scratch is not accessible after dropping privileges')
        if 'TCP=(--use-tcp --daemon-tests-only)' not in workflow:
            test_fail('valgrind.yml: TCP pass repeats transport-independent tests')
        if 'find testtmp' in workflow or 'testtmp/**/' in workflow:
            test_fail('valgrind.yml: evidence collection traverses test fixtures')
        if 'test-results/valgrind-logs/*.log' not in workflow:
            test_fail('valgrind.yml: logs are not staged for artefact upload')
        if 'mkdir -p test-results/valgrind-logs' not in workflow:
            test_fail('valgrind.yml: root jobs own the evidence directory')
        if not any('--receipt=' in line for line in lines):
            test_fail('valgrind.yml: profile receipts are not written')
        if not any('if: always()' in line for line in lines):
            test_fail('valgrind.yml: diagnostic evidence is not retained')
        continue

    pipe = next((line for line in profile_lines
                 if '--use-tcp' not in line and ('runtests.py' in line
                                                  or line.rstrip().endswith('make check')
                                                  or line.rstrip().endswith("make check'"))), None)
    if not pipe:
        continue
    tcp = next((line for line in profile_lines if '--use-tcp' in line), None)
    if not tcp:
        test_fail(f'{path.name}: profiled pipe pass has no profiled TCP pass')
    if '--daemon-tests-only' not in tcp:
        test_fail(f'{path.name}: TCP pass repeats transport-independent tests')
    expected = {
        tuple(name for name in value.split(',')
              if name != 'pipe' and not name.startswith('protocol-'))
        for value in expand_profiles(pipe, matrix)
    }
    actual = {tuple(value.split(',')) for value in expand_profiles(tcp, matrix)}
    if actual != expected:
        test_fail(f'{path.name}: TCP profiles differ from pipe profiles')
    if required and ('--receipt=' not in pipe or '--receipt=' not in tcp):
        test_fail(f'{path.name}: profiled transport has no receipt')

if workflows and not references:
    test_fail('no workflow profile references found')

print(f'ok: {references} workflow profile references')
