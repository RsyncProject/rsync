#!/usr/bin/env python3
"""Workflow profile policy"""

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import fleettest
from rsyncfns import SCRATCHDIR, SRCDIR, test_fail

SRC = Path(SRCDIR).resolve()
fleettest.REPO = SRC
fleettest.TESTSUITE_REPO = SRC
fleettest.WORKFLOWS = SRC / '.github' / 'workflows'
fleettest.known_test_policy.cache_clear()
spec = importlib.util.spec_from_file_location('runtests', SRC / 'testsuite' / 'runtests.py')
runtests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtests)

PROFILE_ASSIGNMENT = re.compile(
    r'RSYNC_TEST_PROFILES=(\S*\$\{\{\s*matrix\.profiles\s*\}\}\S*|\S+)')
MATRIX_PROFILE = re.compile(r'\$\{\{\s*matrix\.profiles\s*\}\}')
MATRIX_PROFILE_VALUE = re.compile(r'^\s+profiles:\s+(\S+)\s*$')
PLATFORM_PROFILES = {'non-asan', 'root', 'self-peer', 'no-idn', 'no-xxhash', 'no-zstd-threads'}
PLATFORM_WORKFLOWS = {
    'freebsd-build.yml': PLATFORM_PROFILES | {'freebsd'},
    'netbsd-build.yml': PLATFORM_PROFILES | {'netbsd'},
    'openbsd-build.yml': PLATFORM_PROFILES | {'openbsd'},
    'solaris-build.yml': PLATFORM_PROFILES | {'solaris'},
}
REQUIRED_WORKFLOW_PROFILES = {
    **PLATFORM_WORKFLOWS,
    'asan-build.yml': {'nonroot', 'self-peer', 'linux'},
}
VALGRIND_PROFILES = {
    frozenset({'pipe', 'non-asan', 'nonroot', 'self-peer', 'linux', 'valgrind'}),
    frozenset({'non-asan', 'nonroot', 'self-peer', 'linux', 'valgrind'}),
    frozenset({'pipe', 'non-asan', 'root', 'self-peer', 'linux', 'valgrind'}),
    frozenset({'non-asan', 'root', 'self-peer', 'linux', 'valgrind'}),
}


def profile_specs(line, matrix_profiles):
    match = PROFILE_ASSIGNMENT.search(line)
    if not match:
        test_fail('invalid RSYNC_TEST_PROFILES assignment')
    value = match.group(1)
    if not MATRIX_PROFILE.search(value):
        return [value]
    if not matrix_profiles:
        test_fail('matrix profile expression has no values')
    return [MATRIX_PROFILE.sub(profile, value) for profile in matrix_profiles]


test_paths = runtests.collect_tests(str(SRC / 'testsuite'), [])
known_tests = {runtests._testbase(path) for path in test_paths}
references = 0
workflows = sorted((SRC / '.github' / 'workflows').glob('*.yml'))
legacy_lists = [path for path in (SRC / 'testsuite' / 'skiplist').glob('*.txt')
                if path.name != 'backport.txt']
if legacy_lists:
    test_fail(f'transitional skip lists remain: {", ".join(path.name for path in legacy_lists)}')

for path in workflows:
    lines = path.read_text().splitlines()
    if any('RSYNC_EXPECT_SKIPPED=' in line for line in lines):
        test_fail(f'{path.name}: transitional expected-skip policy remains')
    matrix_profiles = sorted(match.group(1) for line in lines
                             if (match := MATRIX_PROFILE_VALUE.match(line)))
    profile_lines = [line for line in lines if 'RSYNC_TEST_PROFILES=' in line]
    specs = [value for line in profile_lines for value in profile_specs(line, matrix_profiles)]
    names = {name for value in specs for name in value.split(',')}
    for value in specs:
        for name in value.split(','):
            runtests.load_profile(SRC / 'testsuite' / 'profiles' / f'{name}.json', known_tests)
        references += 1

    required = REQUIRED_WORKFLOW_PROFILES.get(path.name)
    if required:
        missing = required - names
        if missing:
            test_fail(f'{path.name}: missing profiles: {", ".join(sorted(missing))}')
        if not any('test-results/*.json' in line for line in lines):
            test_fail(f'{path.name}: profile receipts are not retained')
        if not any('if: always()' in line for line in lines):
            test_fail(f'{path.name}: failed profile receipts are not retained')

    if path.name == 'valgrind.yml':
        actual = {frozenset(value.split(',')) for value in matrix_profiles}
        if actual != VALGRIND_PROFILES:
            test_fail('valgrind.yml: incomplete profile matrix')
        workflow = '\n'.join(lines)
        if ('VALGRIND_SCRATCH: /tmp/' not in workflow
                or 'scratchbase="$VALGRIND_SCRATCH"' not in workflow):
            test_fail('valgrind.yml: scratch is not accessible after dropping privileges')
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
    if path.name == 'asan-build.yml' and '--daemon-tests-only' not in tcp:
        test_fail('asan-build.yml: TCP pass repeats transport-independent tests')
    expected = {
        tuple(name for name in value.split(',')
              if name != 'pipe' and not name.startswith('protocol-'))
        for value in profile_specs(pipe, matrix_profiles)
    }
    actual = {tuple(value.split(',')) for value in profile_specs(tcp, matrix_profiles)}
    if actual != expected:
        test_fail(f'{path.name}: TCP profiles differ from pipe profiles')
    if required and ('--receipt=' not in pipe or '--receipt=' not in tcp):
        test_fail(f'{path.name}: profiled transport has no receipt')

if workflows and not references:
    test_fail('no workflow profile references found')

valgrind_scratch = SCRATCHDIR / 'valgrind-command'
valgrind_args = SimpleNamespace(valgrind=True, valgrind_opts='', protocol=None)
valgrind_cmd = runtests.build_rsync_cmd('/tmp/rsync', valgrind_args, str(valgrind_scratch))
local_supp = valgrind_scratch / 'valgrind-logs' / 'valgrind.supp'
if not local_supp.is_file() or f'--suppressions={local_supp}' not in valgrind_cmd:
    test_fail('valgrind suppression file is not available to dropped processes')

receipt = {
    'schema': 1,
    'run': {'profiles': ['pipe'], 'transport': 'pipe'},
    'tests': [],
    'summary': {'exit_code': 0, 'mismatches': []},
}
raw = f'output\n{fleettest.RECEIPT_BEGIN}\n{json.dumps(receipt)}\n{fleettest.RECEIPT_END}\n'
parsed, error = fleettest.parse_receipt(raw)
if error or parsed != receipt:
    test_fail(f'valid fleet receipt was rejected: {error}')
if not fleettest.parse_receipt('output only')[1]:
    test_fail('missing fleet receipt was accepted')
result = fleettest.parse_transport('pipe', fleettest.CmdResult(0, raw), True)
if not result.ok:
    test_fail(f'valid profiled fleet result was rejected: {result.receipt_error}')

receipt['tests'] = [{'name': 'bad-skip', 'verdict': 'profile_error'}]
receipt['summary'].update({'exit_code': 1,
                           'mismatches': [{'name': 'bad-skip', 'verdict': 'profile_error'}]})
raw = (f'SKIP    bad-skip\n{fleettest.RECEIPT_BEGIN}\n{json.dumps(receipt)}\n'
       f'{fleettest.RECEIPT_END}\n')
result = fleettest.parse_transport('pipe', fleettest.CmdResult(1, raw), True)
if result.profile_errors != ['bad-skip'] or result.ok:
    test_fail('profile mismatch was not retained from the receipt')

receipt['tests'] = [{'name': 'known-failure', 'verdict': 'fail'}]
receipt['summary'].update({'exit_code': 1,
                           'mismatches': [{'name': 'known-failure', 'verdict': 'fail'}]})
raw = (f'FAIL    known-failure\n{fleettest.RECEIPT_BEGIN}\n{json.dumps(receipt)}\n'
       f'{fleettest.RECEIPT_END}\n')
result = fleettest.parse_transport('pipe', fleettest.CmdResult(1, raw), True,
                                   ['known-failure'])
if not result.ok or result.xfailed_req != ['known-failure']:
    test_fail('target xfail did not reconcile the profile receipt')
result = fleettest.parse_transport('pipe', fleettest.CmdResult(1, raw), True)
retry = fleettest.Target('retry', None, 'none.yml', [], max_retry=1)
fleettest.retry_failed(retry, 'pipe', result,
                       lambda _: fleettest.CmdResult(0, 'PASS    known-failure\n'))
if not result.ok or result.recovered != ['known-failure']:
    test_fail('recovered fleet failure retained its profile mismatch')

if workflows:
    missing = fleettest.Target('matrix', None, 'ubuntu-build.yml', [])
    if not fleettest.validate_target_capabilities(missing):
        test_fail('matrix workflow accepted no target profiles')
    matrix = fleettest.Target('matrix', None, 'ubuntu-build.yml', [],
                              profiles=['non-asan', 'root', 'self-peer', 'linux'])
    if fleettest.target_profiles(matrix, 'pipe', 29) != 'pipe,non-asan,root,self-peer,linux,protocol-29':
        test_fail('matrix target profiles were composed incorrectly')

    target = fleettest.Target('mac', None, 'macos-build.yml', [])
    profiles = fleettest.target_profiles(target, 'pipe')
    script = fleettest.test_script(target, 'pipe', profiles, 1)
    if 'RSYNC_EXPECT_SKIPPED' in script or '--receipt=.fleettest-pipe-receipt.json' not in script:
        test_fail('fleet script does not use a profile receipt')

    fleet = fleettest.load_fleet(SRC / 'testsuite' / 'fleettest.json.example')
    for target in fleet:
        if error := fleettest.validate_target_capabilities(target):
            test_fail(error)
        required = REQUIRED_WORKFLOW_PROFILES.get(target.workflow)
        if required:
            pipe_profiles = set(fleettest.target_profiles(target, 'pipe').split(','))
            tcp_profiles = set(fleettest.target_profiles(target, 'tcp').split(','))
            if pipe_profiles != required | {'pipe'} or tcp_profiles != required:
                test_fail(f'{target.name}: fleet profiles differ from workflow')

print(f'ok: {references} workflow profile references')
