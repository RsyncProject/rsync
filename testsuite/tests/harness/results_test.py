#!/usr/bin/env python3
"""Result normalisation and receipt writing"""

import json

from harness import (Exit, Outcome, TestContext, TestResult, requires, run, test_fail, test_skipped,
                     test_xfail, verdict_of, write_receipt)
from rsyncfns import test_fail as legacy_test_fail
from rsyncfns import test_skipped as legacy_test_skipped
from rsyncfns import test_xfail as legacy_test_xfail


@requires(protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe'}, min_peer='2.6.0', tags={'harness'})
def test(context: TestContext):
    if (legacy_test_fail, legacy_test_skipped, legacy_test_xfail) != (test_fail, test_skipped, test_xfail):
        test_fail('legacy result helpers are not harness exports')

    cases = (
        (Exit.PASS, '', Outcome.PASS), (Exit.FAIL, '', Outcome.FAIL),
        (Exit.ERROR, '', Outcome.ERROR), (Exit.SKIP, '', Outcome.SKIP),
        (Exit.SKIP, 'acl', Outcome.UNSUPPORTED), (Exit.XFAIL, '', Outcome.XFAIL),
    )
    for exit_code, unsupported, expected in cases:
        result = TestResult('case', exit_code, unsupported=unsupported)
        if result.outcome != expected:
            test_fail(f'{exit_code} normalised as {result.outcome}')

    verdicts = (
        (Outcome.PASS, 'xfail:issue-1', Outcome.XPASS),
        (Outcome.FAIL, 'xfail:issue-1', Outcome.XFAIL),
        (Outcome.UNSUPPORTED, 'pass', Outcome.PROFILE_ERROR),
        (Outcome.UNSUPPORTED, 'unsupported:acl', Outcome.UNSUPPORTED),
    )
    for outcome, expected, verdict in verdicts:
        if verdict_of(outcome, expected) != verdict:
            test_fail(f'{outcome} against {expected} produced the wrong verdict')
    if TestResult('case', Exit.SKIP, unsupported='acl').record()['verdict'] != 'unsupported':
        test_fail('unprofiled unsupported result was reclassified')

    path = context.scratch / 'receipt.json'
    write_receipt(path, {'schema': 1, 'tests': [TestResult('case', Exit.PASS).record('pass')]})
    data = json.loads(path.read_text())
    if data['tests'][0]['outcome'] != 'pass' or list(context.scratch.glob('.*.tmp')):
        test_fail('receipt write was incomplete')


if __name__ == '__main__':
    run(test)
