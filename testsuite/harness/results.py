import enum
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

class Exit(enum.IntEnum):
    PASS = 0
    FAIL = 1
    ERROR = 2
    SKIP = 77

def unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate field: {key}')
        data[key] = value
    return data

def verdict_of(outcome, expected):
    if expected and expected.startswith('unsupported'):
        return 'unsupported' if outcome == 'unsupported' else 'profile_error'
    if expected == 'pass' and outcome in ('skip', 'unsupported'):
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
        if self.exit_code == Exit.FAIL:
            return 'fail'
        if self.exit_code == Exit.SKIP:
            return 'unsupported' if self.unsupported else 'skip'
        return 'error'

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

def read_receipt(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 3:
        raise ValueError('unsupported receipt schema')
    run, records, summary = data.get('run'), data.get('tests'), data.get('summary')
    if not isinstance(run, dict) or not isinstance(records, list) or not isinstance(summary, dict):
        raise ValueError('invalid receipt sections')
    selected, profiles = run.get('selected'), run.get('profiles')
    if not isinstance(selected, list) or not selected or any(not isinstance(name, str) or not name for name in selected):
        raise ValueError('invalid selected tests')
    if len(set(selected)) != len(selected) or not isinstance(profiles, list) \
            or any(not isinstance(name, str) or not name for name in profiles):
        raise ValueError('invalid test selection or profiles')
    for field in ('primary_sha256', 'secondary_sha256'):
        if not isinstance(run.get(field), str) or not re.fullmatch('[0-9a-f]{64}', run[field]):
            raise ValueError('invalid executable identity')
    for field in ('primary', 'secondary'):
        if not isinstance(run.get(field), str) or not run[field]:
            raise ValueError('invalid executable command')
    for field in ('primary_version', 'secondary_version'):
        if not isinstance(run.get(field), str) or not re.fullmatch(r'\d+(?:\.\d+)+', run[field]):
            raise ValueError('invalid executable version')
    archived_role, archived_version = run.get('archived_role'), run.get('archived_version')
    if archived_role not in (None, 'primary', 'secondary') or bool(archived_role) != bool(archived_version):
        raise ValueError('invalid archived binary identity')
    if archived_role and (not isinstance(archived_version, str)
                          or archived_version != run[f'{archived_role}_version']):
        raise ValueError('archived version does not match its role')
    version_profiles = []
    for name in profiles:
        match = re.fullmatch(r'peer-(\d+(?:\.\d+)+)', name)
        if match:
            version_profiles.append(match.group(1))
    if (len(version_profiles) > 1 or bool(version_profiles) != bool(archived_role)
            or (version_profiles and version_profiles[0] != archived_version)):
        raise ValueError('archived version does not match its profile')
    if 'self-peer' in profiles and (run['primary_sha256'] != run['secondary_sha256']
            or run['primary_version'] != run['secondary_version'] or archived_role):
        raise ValueError('self-peer receipt identifies different executables')
    groups = run.get('not_applicable')
    if not isinstance(groups, list) or any(not isinstance(group, dict)
            or not isinstance(group.get('tests'), list) or not isinstance(group.get('capability'), str)
            or any(not isinstance(name, str) or not name for name in group['tests']) for group in groups):
        raise ValueError('invalid N/A selection')
    excluded = {name for group in groups for name in group['tests']}
    reasons = {group['capability']: len(group['tests']) for group in groups}
    if set(selected) & excluded or summary.get('not_applicable') != len(excluded) \
            or len(reasons) != len(groups) or summary.get('not_applicable_reasons') != reasons:
        raise ValueError('N/A selection counts disagree')
    if type(summary.get('valgrind_errors')) not in (bool, int) or summary['valgrind_errors'] < 0 \
            or not isinstance(summary.get('mismatches'), list):
        raise ValueError('invalid run failure summary')
    names, verdicts = [], Counter()
    for record in records:
        if not isinstance(record, dict) or type(record.get('exit_code')) is not int:
            raise ValueError('invalid test result')
        name, expected = record.get('name'), record.get('expected')
        if not isinstance(name, str) or name not in selected or bool(profiles) != ('expected' in record):
            raise ValueError('test result does not match the selected run')
        if expected is not None and (not isinstance(expected, str) or not (
                expected == 'pass' or expected.startswith('unsupported:')
                and expected.split(':', 1)[1])):
            raise ValueError('invalid expected result')
        unsupported = record.get('unsupported', '')
        if not isinstance(unsupported, str):
            raise ValueError('invalid unsupported capability')
        result = TestResult(name, record['exit_code'], unsupported=unsupported)
        verdict = verdict_of(result.outcome, expected)
        if record.get('outcome') != result.outcome or record.get('verdict') != verdict:
            raise ValueError('test outcome and verdict disagree')
        if expected and expected.startswith('unsupported:'):
            requirements = record.get('requirements', {})
            features = requirements.get('features') if isinstance(requirements, dict) else None
            if not isinstance(features, list) or any(not isinstance(feature, str) for feature in features) \
                    or unsupported != expected.split(':', 1)[1] or unsupported not in features:
                raise ValueError('unsupported result has no declared capability')
        names.append(name)
        verdicts[verdict] += 1
    if len(set(names)) != len(names) or summary.get('verdicts') != dict(verdicts):
        raise ValueError('test result counts disagree')
    counts = {'passed': verdicts['pass'], 'skipped': verdicts['skip'],
              'unsupported': verdicts['unsupported']}
    counts['failed'] = len(records) - sum(counts.values())
    if any(type(summary.get(key)) is not int or summary[key] != value for key, value in counts.items()):
        raise ValueError('receipt summary disagrees with its results')
    accepted = {'pass', 'unsupported'} | ({'skip'} if not profiles else set())
    failed = (set(names) != set(selected) or bool(set(verdicts) - accepted)
              or summary.get('valgrind_errors') or summary.get('mismatches'))
    if type(summary.get('exit_code')) is not int or summary['exit_code'] != int(bool(failed)):
        raise ValueError('receipt exit status disagrees with its results')
    return data

def test_fail(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.FAIL)

def test_error(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    sys.exit(Exit.ERROR)

def test_skipped(msg: str, capability: str = None) -> None:
    sys.stderr.write(msg.rstrip() + '\n')
    scratch = Path(os.environ['scratchdir'])
    if capability:
        (scratch / 'unsupported').write_text(capability + '\n', encoding='utf-8')
    (scratch / 'whyskipped').write_text(msg.rstrip() + '\n', encoding='utf-8')
    sys.exit(Exit.SKIP)
