#!/usr/bin/env python3

from copy import deepcopy

from harness import Exit, TestContext, requires, run
from harness.results import TestResult, test_fail, verdict_of
from harness.results import read_receipt, write_receipt

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    cases = (
        (Exit.PASS, '', 'pass'), (Exit.FAIL, '', 'fail'),
        (Exit.ERROR, '', 'error'), (Exit.SKIP, '', 'skip'),
        (Exit.SKIP, 'acl', 'unsupported'), (Exit.XFAIL, '', 'xfail'),
        (3, '', 'error'), (-15, '', 'error'),
    )
    for exit_code, unsupported, expected in cases:
        result = TestResult('case', exit_code, unsupported=unsupported)
        if result.outcome != expected:
            test_fail(f'{exit_code} normalised as {result.outcome}')

    verdicts = (
        ('pass', 'xfail:issue-1', 'unexpected_pass'),
        ('fail', 'xfail:issue-1', 'xfail'),
        ('xfail', 'xfail:issue-1', 'xfail'),
        ('xfail', 'pass', 'profile_error'),
        ('xfail', None, 'profile_error'),
        ('error', 'xfail:issue-1', 'profile_error'),
        ('unsupported', 'xfail:issue-1', 'profile_error'),
        ('unsupported', 'pass', 'profile_error'),
        ('unsupported', 'unsupported:acl', 'unsupported'),
    )
    for outcome, expected, verdict in verdicts:
        if verdict_of(outcome, expected) != verdict:
            test_fail(f'{outcome} against {expected} produced the wrong verdict')
    if TestResult('case', Exit.SKIP, unsupported='acl').record()['verdict'] != 'unsupported':
        test_fail('unprofiled unsupported result was reclassified')

    receipt = context.scratch / 'receipt.json'
    data = {
        'schema': 2, 'run': {'selected': ['case'], 'profiles': [], 'not_applicable': [],
                            'binary_sha256': 'a' * 64, 'peer_sha256': 'a' * 64},
        'tests': [TestResult('case', Exit.PASS).record()],
        'summary': {'passed': 1, 'failed': 0, 'skipped': 0, 'unsupported': 0, 'xfailed': 0,
                    'not_applicable': 0, 'not_applicable_reasons': {}, 'verdicts': {'pass': 1},
                    'exit_code': 0, 'valgrind_errors': 0, 'mismatches': []},
    }
    write_receipt(receipt, data)
    if read_receipt(receipt) != data:
        test_fail('a valid receipt changed while loading')
    cases = (
        ('schema', 1), ('schema', 2.0), ('tests', []), ('tests', data['tests'] * 2),
        ('run', {**data['run'], 'selected': []}),
        ('run', {**data['run'], 'selected': ['case', 'missing']}),
        ('run', {**data['run'], 'profiles': ['fixture']}),
        ('run', {**data['run'], 'profiles': ['self-peer'], 'peer_sha256': 'b' * 64}),
        ('summary', {**data['summary'], 'exit_code': 1}),
        ('summary', {**data['summary'], 'passed': 2}),
        ('summary', {**data['summary'], 'verdicts': {}}),
    )
    for field, value in cases:
        changed = deepcopy(data)
        changed[field] = value
        write_receipt(receipt, changed)
        try:
            read_receipt(receipt)
        except ValueError:
            continue
        test_fail(f'an invalid receipt was accepted: {field}={value}')
    data['tests'] = [TestResult('case', Exit.FAIL).record()]
    data['summary'].update(passed=0, failed=1, verdicts={'fail': 1}, exit_code=1)
    write_receipt(receipt, data)
    if read_receipt(receipt)['summary']['exit_code'] != 1:
        test_fail('a failed run lost its exit status')

    data['run']['profiles'] = ['fixture']
    data['tests'] = [TestResult('case', Exit.SKIP, unsupported='acl').record('unsupported:acl')]
    data['summary'].update(failed=0, unsupported=1, verdicts={'unsupported': 1}, exit_code=0)
    for features in ('acl', None, {'acl': True}, ['acl', 1]):
        data['tests'][0]['requirements'] = {'features': features}
        write_receipt(receipt, data)
        try:
            read_receipt(receipt)
        except ValueError:
            continue
        test_fail('an unsupported result with invalid metadata was accepted')
    data['tests'][0]['requirements'] = {'features': ['acl']}
    write_receipt(receipt, data)
    read_receipt(receipt)

if __name__ == '__main__':
    run(test)
