import enum
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from exitcodes import Exit


class Outcome(str, enum.Enum):
    PASS = 'pass'
    FAIL = 'fail'
    ERROR = 'error'
    SKIP = 'skip'
    UNSUPPORTED = 'unsupported'
    XFAIL = 'xfail'
    XPASS = 'xpass'
    PROFILE_ERROR = 'profile_error'


def outcome_of(exit_code, unsupported=''):
    if exit_code == Exit.PASS:
        return Outcome.PASS
    if exit_code == Exit.ERROR:
        return Outcome.ERROR
    if exit_code == Exit.SKIP:
        return Outcome.UNSUPPORTED if unsupported else Outcome.SKIP
    if exit_code == Exit.XFAIL:
        return Outcome.XFAIL
    return Outcome.FAIL


def verdict_of(outcome, expected):
    if expected == 'fail' or (expected and expected.startswith('xfail')):
        if outcome in (Outcome.FAIL, Outcome.XFAIL):
            return Outcome.XFAIL
        return Outcome.XPASS if outcome == Outcome.PASS else Outcome.PROFILE_ERROR
    if expected and expected.startswith('unsupported'):
        return Outcome.UNSUPPORTED if outcome == Outcome.UNSUPPORTED else Outcome.PROFILE_ERROR
    if expected == 'skip':
        return Outcome.SKIP if outcome == Outcome.SKIP else Outcome.PROFILE_ERROR
    if expected == 'pass' and outcome in (Outcome.SKIP, Outcome.UNSUPPORTED, Outcome.XFAIL):
        return Outcome.PROFILE_ERROR
    return outcome


@dataclass
class TestResult:
    name: str
    exit_code: int
    output: str = ''
    skip_reason: str = ''
    unsupported: str = ''
    duration: float = 0.0

    @property
    def outcome(self):
        return outcome_of(self.exit_code, self.unsupported)

    def record(self, expected=None):
        verdict = verdict_of(self.outcome, expected)
        data = {
            'name': self.name,
            'outcome': self.outcome.value,
            'verdict': verdict.value,
            'exit_code': int(self.exit_code),
            'duration_seconds': round(self.duration, 6),
        }
        if self.skip_reason:
            data['reason'] = self.skip_reason
        if self.unsupported:
            data['unsupported'] = self.unsupported
        if expected:
            data['expected'] = expected
        return data


def test_fail(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.FAIL)


def test_skipped(msg: str, capability: str = None) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    scratch = Path(os.environ['scratchdir'])
    if capability:
        (scratch / 'unsupported').write_text(capability + '\n')
    (scratch / 'whyskipped').write_text(msg.rstrip() + '\n')
    sys.exit(Exit.SKIP)


def test_xfail(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.XFAIL)


def unsupported(capability: str) -> None:
    scratch = Path(os.environ['scratchdir'])
    (scratch / 'unsupported').write_text(capability + '\n', encoding='utf-8')
    (scratch / 'whyskipped').write_text(capability + '\n', encoding='utf-8')
    raise SystemExit(Exit.SKIP)
