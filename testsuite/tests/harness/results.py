#!/usr/bin/env python3

from harness import Exit, TestContext, TestResult, requires, run, test_fail, verdict_of

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    cases = (
        (Exit.PASS, '', 'pass'), (Exit.FAIL, '', 'fail'),
        (Exit.ERROR, '', 'error'), (Exit.SKIP, '', 'skip'),
        (Exit.SKIP, 'acl', 'unsupported'), (Exit.XFAIL, '', 'xfail'),
    )
    for exit_code, unsupported, expected in cases:
        result = TestResult('case', exit_code, unsupported=unsupported)
        if result.outcome != expected:
            test_fail(f'{exit_code} normalised as {result.outcome}')

    verdicts = (
        ('pass', 'xfail:issue-1', 'xpass'),
        ('fail', 'xfail:issue-1', 'xfail'),
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
