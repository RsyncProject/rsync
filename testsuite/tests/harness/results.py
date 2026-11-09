#!/usr/bin/env python3

from harness import Exit, TestContext, TestResult, requires, run, test_fail, verdict_of

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

if __name__ == '__main__':
    run(test)
