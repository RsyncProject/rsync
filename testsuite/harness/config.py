import argparse
import fnmatch
import math
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path
from .metadata import applies_to_peer, discover_tests, read_requirements, test_name
from .process import file_digest
from .profile import load_binaries, load_profile, merge_profiles, parse_peer_banner
from .results import Exit

_SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _race_seconds(text):
    try:
        secs = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not a number: {text!r}')
    if not math.isfinite(secs) or secs <= 0:
        raise argparse.ArgumentTypeError(
            f'must be a finite positive number of seconds, got {text!r}')
    return secs

def _positive_int(text):
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f'not an integer: {text!r}') from error
    if value <= 0:
        raise argparse.ArgumentTypeError(f'must be positive: {text!r}')
    return value

def parse_args():
    parser = argparse.ArgumentParser(description='Run rsync test suite')
    parser.add_argument('tests', nargs='*', metavar='TEST',
                   help='Test names or patterns to run (default: all)')
    parser.add_argument('--exclude', default=None, metavar='LIST',
                   help='Exclude comma-separated test names or patterns '
                        '(default: RSYNC_EXCLUDE)')
    parser.add_argument('-j', '--parallel', type=_positive_int, default=1, metavar='N',
                   help='Run up to N tests in parallel (default: 1)')
    parser.add_argument('--valgrind', action='store_true',
                   help='Run rsync under valgrind (logs to per-process files)')
    parser.add_argument('--valgrind-opts', default='', metavar='OPTS',
                   help='Extra valgrind options such as "--leak-check=full"')
    parser.add_argument('--preserve-scratch', action='store_true',
                   help='Keep scratch directories after tests complete')
    parser.add_argument('--log-level', type=int, choices=range(1, 11), default=1, metavar='N',
                   help='Verbosity level 1 to 10 (default: 1)')
    parser.add_argument('--always-log', action='store_true',
                   help='Show test logs even for passing tests')
    parser.add_argument('--stop-on-fail', action='store_true',
                   help='Stop after first test failure')
    parser.add_argument('--timing', action='store_true',
                   help='Report test durations, slowest first')
    parser.add_argument('--receipt', metavar='FILE', help='Write a JSON run receipt')
    parser.add_argument('--check-receipt', metavar='FILE', help='Check a run receipt and return its exit status')
    parser.add_argument('--timeout', type=_positive_int, default=300, metavar='SECS',
                   help='Per-test timeout in seconds (default: 300)')
    parser.add_argument('--race-timeout', type=_race_seconds, default=None, metavar='SECS',
                   help='Override per-test race budgets in seconds; '
                        'lower values weaken race coverage')
    parser.add_argument('--rsync-bin', default=None, metavar='PATH',
                   help='Path to rsync binary (default: ./rsync)')
    parser.add_argument('--rsync-bin2', default=None, metavar='PATH',
                   help='Secondary binary for daemon and remote-shell peers '
                        '(default: --rsync-bin)')
    parser.add_argument('--archived-role', choices=('primary', 'secondary'),
                   help='Role occupied by the archived release in a version-mix run')
    parser.add_argument('--tooldir', default=None, metavar='DIR',
                   help='Tool/build directory (default: cwd)')
    parser.add_argument('--srcdir', default=None, metavar='DIR',
                   help='Source directory (default: script directory)')
    parser.add_argument('--protocol', type=int, default=None, metavar='VER',
                   help='Force protocol version (adds --protocol=VER to rsync)')
    parser.add_argument('--profiles', default=None, metavar='LIST',
                   help='Comma-separated test profiles')
    parser.add_argument('--daemon-tests-only', action='store_true',
                   help='Select tests which exercise daemon transports')
    parser.add_argument('--use-tcp', action='store_true',
                   help='Use loopback TCP daemons, accessible to other local users '
                        '(default: pipes)')
    parser.add_argument('--describe-tests', action='store_true',
                   help='Print test metadata as JSON and exit')
    return parser.parse_args()

def find_setfacl_nodef(scratchbase):
    for cmd in [
        ['setacl', '-k', 'u::7,g::5,o:5', scratchbase],
        ['setfacl', '-k', scratchbase],
        ['setfacl', '-s', 'u::7,g::5,o:5', scratchbase],
    ]:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=5)
            if result.returncode == 0:
                return cmd[:2]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    if shutil.which('setfacl') is None:
        return None
    try:
        r = subprocess.run(['setfacl', '--help'], capture_output=True, text=True, timeout=5)
        if '-k,' in r.stdout or '-k,' in r.stderr:
            return ['setfacl', '-k']
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None

def get_tls_args(config_h):
    args = ''
    try:
        with open(config_h) as f:
            text = f.read()
        if '#define HAVE_LUTIMES 1' in text:
            args += ' -l'
        if '#undef CHOWN_MODIFIES_SYMLINK' in text:
            args += ' -L'
    except FileNotFoundError:
        pass
    return args.strip()

def read_shconfig(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or line.startswith('export') or not line:
                    continue
                if '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return env

def get_testuser():
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())

def collect_tests(suitedir, patterns):
    legacy = sorted(Path(suitedir).glob('*_test.py'))
    if legacy:
        raise ValueError('tests outside tests/: ' + ', '.join(path.name for path in legacy))
    testdir = os.path.join(suitedir, 'tests')
    candidates = [str(path) for path in discover_tests(testdir)]
    if not candidates:
        raise ValueError(f'no tests found in {testdir}')
    if not patterns:
        tests = candidates
    else:
        seen = set()
        tests = []
        for pat in patterns:
            pattern = pat if pat.endswith('.py') else pat + '.py'
            matched = False
            for path in candidates:
                if not fnmatch.fnmatch(os.path.basename(path), pattern):
                    continue
                matched = True
                if path not in seen:
                    seen.add(path)
                    tests.append(path)
            if not matched:
                raise ValueError(f'no tests match {pat!r}')
    return tests

def select_applicable_tests(tests, not_applicable, requirements_by_path=None):
    selected = []
    excluded = {}
    capabilities_to_exclude = set(not_applicable)
    for path in tests:
        requirements = (requirements_by_path.get(os.fspath(path))
                        if requirements_by_path is not None else read_requirements(path)) or {}
        capabilities = sorted(set(requirements.get('features', ())) & capabilities_to_exclude)
        if not capabilities:
            selected.append(path)
            continue
        for capability in capabilities:
            excluded.setdefault(capability, []).append(test_name(path))
    details = [
        {'capability': capability, 'reasons': list(not_applicable[capability]),
         'tests': excluded[capability]}
        for capability in sorted(excluded)
    ]
    return selected, details

def select_transport_tests(tests, transport, requirements_by_path=None):
    selected, excluded = [], []
    for path in tests:
        requirements = (requirements_by_path.get(os.fspath(path))
                        if requirements_by_path is not None else read_requirements(path))
        if requirements and not requirements['transports']:
            raise ValueError(f'{path}: no transports declared')
        if not requirements or transport in requirements['transports']:
            selected.append(path)
        else:
            excluded.append(test_name(path))
    details = [] if not excluded else [{
        'capability': 'transport',
        'reasons': [f'not applicable to {transport} transport'],
        'tests': excluded,
    }]
    return selected, details

def describe_tests(tests):
    descriptions = []
    for path in tests:
        metadata = read_requirements(path)
        descriptions.append({'name': test_name(path), **(metadata or {})})
    return descriptions

def binary_identity(path):
    try:
        result = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'cannot determine rsync version from {path}') from error
    if result.returncode:
        raise ValueError(f'cannot determine rsync version from {path}')
    try:
        return parse_peer_banner(result.stdout)
    except ValueError as error:
        raise ValueError(f'cannot determine rsync version from {path}') from error

def apply_environment_defaults(args):
    if args.preserve_scratch or os.environ.get('preserve_scratch') == 'yes':
        args.preserve_scratch = True
    if args.log_level == 1:
        args.log_level = int(os.environ.get('loglevel', '1'))
    if args.exclude is None:
        args.exclude = os.environ.get('RSYNC_EXCLUDE', '')
    if args.profiles is None:
        args.profiles = os.environ.get('RSYNC_TEST_PROFILES', '')
    if os.environ.get('whichtests'):
        args.tests = [os.environ['whichtests']]

def resolve_run_paths(args):
    tooldir = args.tooldir or os.environ.get('TOOLDIR') or os.getcwd()
    srcdir = args.srcdir or os.path.dirname(_SUITE_DIR)
    if not srcdir or srcdir == '.':
        srcdir = tooldir
    rsync_bin = args.rsync_bin or os.environ.get('rsync_bin') or os.path.join(tooldir, 'rsync')
    rsync_bin2 = args.rsync_bin2 or os.environ.get('rsync_bin2') or rsync_bin
    if not os.path.isabs(rsync_bin):
        rsync_bin = os.path.abspath(rsync_bin)
    if not os.path.isabs(rsync_bin2):
        rsync_bin2 = os.path.abspath(rsync_bin2)
    return tooldir, srcdir, os.path.join(srcdir, 'testsuite'), rsync_bin, rsync_bin2

def validate_run_paths(tooldir, srcdir, rsync_bin, rsync_bin2):
    if not os.path.isfile(rsync_bin):
        sys.stderr.write(f'rsync_bin {rsync_bin} is not a file\n')
        sys.exit(Exit.ERROR)
    if not os.path.isfile(rsync_bin2):
        sys.stderr.write(f'rsync_bin2 {rsync_bin2} is not a file\n')
        sys.exit(Exit.ERROR)
    if not os.path.isdir(srcdir):
        sys.stderr.write(f'srcdir {srcdir} is not a directory\n')
        sys.exit(Exit.ERROR)

    helpers = (
        'tls', 'trimslash', 't_unsafe', 't_chmod_secure', 't_secure_relpath',
        'wildtest', 'getgroups', 'getfsdev',
    )
    missing = [name for name in helpers if not os.path.isfile(os.path.join(tooldir, name))]
    if missing:
        names = ' '.join(missing)
        sys.stderr.write(
            f'runtests.py: missing test helper program(s) in {tooldir}: {", ".join(missing)}\n'
            f'Build them with: make {names}\n'
            'or run the full test target: make check\n'
        )
        sys.exit(Exit.ERROR)

    archive = (Path(srcdir) / 'old_versions').resolve()
    for binary in {rsync_bin, rsync_bin2}:
        path = Path(binary).absolute()
        if path.parent != archive:
            path = path.resolve()
        if path.parent != archive:
            continue
        records = load_binaries(archive)
        if path.name not in records:
            raise ValueError(f'not an archived peer: {path.name}')
        record = records[path.name]
        version = path.name[len('rsync_'):]
        command = ['git', '--no-optional-locks', '--git-dir=' + str(Path(srcdir).resolve() / '.git'),
                   '--work-tree=' + str(Path(srcdir).resolve()),
                   '-C', srcdir, 'rev-parse', '--verify', f'refs/tags/v{version}^{{commit}}']
        try:
            source = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(f'cannot verify source of archived peer {version}') from error
        if source != record['source']:
            raise ValueError(f'archived peer {version} differs from its release tag')
        actual_version, protocol = binary_identity(binary)
        if actual_version != version or protocol != record['protocol']:
            raise ValueError(f'archived peer {version} differs from its declared identity')

def configure_profiles(args, suitedir, srcdir, all_tests, tests,
                       rsync_bin, rsync_bin2):
    archived_role = getattr(args, 'archived_role', None)
    requirements_by_path = {
        os.fspath(path): read_requirements(path) for path in tests
    }
    profiles = []
    for item in (value.strip() for value in args.profiles.split(',')):
        if not item:
            continue
        if os.path.sep not in item and (not os.path.altsep or os.path.altsep not in item):
            filename = item if item.endswith('.json') else f'{item}.json'
            path = os.path.join(suitedir, 'profiles', filename)
        else:
            path = item if os.path.isabs(item) else os.path.join(srcdir, item)
        profiles.append(load_profile(path))

    if args.profiles and not profiles:
        raise ValueError('no profiles selected')
    roles = {profile.name for profile in profiles} & {'root', 'nonroot'}
    if len(roles) > 1:
        raise ValueError('root and nonroot profiles conflict')
    for role in roles:
        if (role == 'root') != (os.geteuid() == 0):
            raise ValueError(f'{role} profile does not match effective UID {os.geteuid()}')
    profile_version, profile_protocol, not_applicable, unsupported = merge_profiles(profiles)
    if bool(profile_version) != bool(archived_role):
        raise ValueError('version profiles require exactly one archived role')
    if any(profile.name == 'self-peer' for profile in profiles):
        if profile_version or archived_role or file_digest(rsync_bin) != file_digest(rsync_bin2):
            raise ValueError('self-peer profile requires the same executable in both roles')
    transport = 'tcp' if args.use_tcp else 'pipe'
    tests, excluded = select_applicable_tests(tests, not_applicable, requirements_by_path)
    tests, transport_excluded = select_transport_tests(tests, transport, requirements_by_path)
    excluded.extend(transport_excluded)
    effective_protocol = args.protocol
    if profile_protocol and not profile_version:
        if args.protocol != profile_protocol:
            raise ValueError(
                f'profile protocol {profile_protocol} does not match '
                f'{args.protocol or "the default protocol"}')
        effective_protocol = profile_protocol

    if not profile_version:
        return tests, profiles, profile_version, effective_protocol, unsupported, transport, excluded

    primary_version, primary_protocol = binary_identity(rsync_bin)
    secondary_version, secondary_protocol = binary_identity(rsync_bin2)
    archived_version = primary_version if archived_role == 'primary' else secondary_version
    archived_protocol = primary_protocol if archived_role == 'primary' else secondary_protocol
    if archived_version != profile_version:
        raise ValueError(f'profile version {profile_version} does not match {archived_version}')
    if archived_protocol != profile_protocol:
        raise ValueError(
            f'profile protocol {profile_protocol} does not match {archived_protocol}')
    effective_protocol = min(primary_protocol, secondary_protocol,
                             args.protocol if args.protocol is not None else primary_protocol)
    for test in all_tests:
        key = os.fspath(test)
        if key not in requirements_by_path:
            requirements_by_path[key] = read_requirements(test)
    requirements = {
        test_name(test): requirements_by_path[os.fspath(test)] for test in all_tests
    }
    missing = [name for name, values in requirements.items() if not values or not values['transports']]
    if missing:
        raise ValueError(f'tests without transport metadata: {", ".join(sorted(missing))}')
    tests = [test for test in tests
             if applies_to_peer(requirements[test_name(test)], profile_version,
                                effective_protocol, transport, archived_role)]
    if not tests:
        raise ValueError(f'no tests apply to archived release {profile_version}')
    return tests, profiles, profile_version, effective_protocol, unsupported, transport, excluded
