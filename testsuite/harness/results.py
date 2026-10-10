import enum
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

class Exit(enum.IntEnum):
    PASS = 0
    FAIL = 1
    ERROR = 2
    SKIP = 77
    XFAIL = 78

def verdict_of(outcome, expected):
    if expected and expected.startswith('xfail'):
        if outcome in ('fail', 'xfail'):
            return 'xfail'
        return 'xpass' if outcome == 'pass' else 'profile_error'
    if expected and expected.startswith('unsupported'):
        return 'unsupported' if outcome == 'unsupported' else 'profile_error'
    if expected == 'pass' and outcome in ('skip', 'unsupported', 'xfail'):
        return 'profile_error'
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
        if self.exit_code == Exit.PASS:
            return 'pass'
        if self.exit_code == Exit.ERROR:
            return 'error'
        if self.exit_code == Exit.SKIP:
            return 'unsupported' if self.unsupported else 'skip'
        if self.exit_code == Exit.XFAIL:
            return 'xfail'
        return 'fail'

    def record(self, expected=None):
        verdict = verdict_of(self.outcome, expected)
        data = {
            'name': self.name,
            'outcome': self.outcome,
            'verdict': verdict,
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

def write_receipt(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{os.getpid()}.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

def test_fail(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.FAIL)

def test_skipped(msg: str, capability: str = None) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    scratch = Path(os.environ['scratchdir'])
    if capability:
        (scratch / 'unsupported').write_text(capability + '\n', encoding='utf-8')
    (scratch / 'whyskipped').write_text(msg.rstrip() + '\n', encoding='utf-8')
    sys.exit(Exit.SKIP)

def test_xfail(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.XFAIL)
