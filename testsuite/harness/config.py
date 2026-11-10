import argparse
import fnmatch
import math
import os
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
            f'must be a finite positive number of seconds, got {text!r}; '
            'a zero/negative/NaN budget would make every race test pass '
            'without running its race')
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
                   help='Comma-separated test names/globs to skip entirely: '
                        'they are not run and not reported as skipped. Useful '
                        'for tests that cannot work in a given build/CI '
                        'environment such as a restricted buildd chroot. '
                        'Falls back to the RSYNC_EXCLUDE environment variable.')
    parser.add_argument('-j', '--parallel', type=_positive_int, default=1, metavar='N',
                   help='Run up to N tests in parallel (default: 1)')
    parser.add_argument('--valgrind', action='store_true',
                   help='Run rsync under valgrind (logs to per-process files)')
    parser.add_argument('--valgrind-opts', default='', metavar='OPTS',
                   help='Extra valgrind options such as "--leak-check=full"')
    parser.add_argument('--preserve-scratch', action='store_true',
                   help='Keep scratch directories after tests complete')
    parser.add_argument('--log-level', type=int, choices=range(1, 11), default=1, metavar='N',
                   help='Verbosity level 1-10 (default: 1)')
    parser.add_argument('--always-log', action='store_true',
                   help='Show test logs even for passing tests')
    parser.add_argument('--stop-on-fail', action='store_true',
                   help='Stop after first test failure')
    parser.add_argument('--timing', action='store_true',
                   help='After the run, report each test\'s wall-clock time, '
                        'slowest first. With -j N the report also shows how '
                        'much of the run the slowest test alone accounts for.')
    parser.add_argument('--receipt', metavar='FILE', help='Write a JSON run receipt')
    parser.add_argument('--check-receipt', metavar='FILE', help='Check a run receipt and return its exit status')
    parser.add_argument('--timeout', type=_positive_int, default=300, metavar='SECS',
                   help='Per-test timeout in seconds (default: 300)')
    parser.add_argument('--race-timeout', type=_race_seconds, default=None, metavar='SECS',
                   help='Budget (seconds) a TOCTOU symlink-race test may spend '
                        'trying to win its race before concluding. Overrides '
                        'every such test\'s own default (5-15s, the suite\'s '
                        'slowest tests: a race test always spends its whole '
                        'budget). Lowering it speeds the suite up but weakens '
                        'the oracle. Unset: each test keeps its default.')
    parser.add_argument('--rsync-bin', default=None, metavar='PATH',
                   help='Path to rsync binary (default: ./rsync)')
    parser.add_argument('--rsync-bin2', default=None, metavar='PATH',
                   help='Path to a second ("peer") rsync binary used for the '
                        'daemon side and remote-shell --rsync-path. Lets the '
                        'suite mix two rsync versions over the wire. Default: '
                        'same as --rsync-bin (no version mixing).')
    parser.add_argument('--tooldir', default=None, metavar='DIR',
                   help='Tool/build directory (default: cwd)')
    parser.add_argument('--srcdir', default=None, metavar='DIR',
                   help='Source directory (default: script directory)')
    parser.add_argument('--protocol', type=int, default=None, metavar='VER',
                   help='Force protocol version (adds --protocol=VER to rsync)')
    parser.add_argument('--profiles', default=None, metavar='LIST',
                   help='Comma-separated test profiles')
    parser.add_argument('--daemon-tests-only', action='store_true',
                   help='Run only the tests that can reach the daemon '
                        'transport. Intended for a --use-tcp pass that follows '
                        'a full default-transport run: the tests this drops '
                        'cannot observe --use-tcp.')
    parser.add_argument('--use-tcp', action='store_true',
                   help='Run daemon tests against a real rsyncd bound to '
                        '127.0.0.1 (non-default). The default is the secure '
                        'stdio-pipe transport, which opens no listening '
                        'socket; --use-tcp exposes a loopback port for the '
                        'duration of each daemon test.')
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
    for cmd in ['/usr/bin/whoami', '/usr/ucb/whoami', '/bin/whoami']:
        if os.path.isfile(cmd):
            try:
                return subprocess.check_output([cmd], text=True).strip()
            except subprocess.CalledProcessError:
                pass
    try:
        return subprocess.check_output(['id', '-un'], text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return os.environ.get('LOGNAME', os.environ.get('USER', 'UNKNOWN'))

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

def select_daemon_tests(tests):
    keep, dropped = [], []
    for path in tests:
        requirements = read_requirements(path)
        applicable = not requirements or not requirements['transports'] or 'tcp' in requirements['transports']
        (keep if applicable else dropped).append(path)
    return keep, dropped

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
        command = ['git', '--no-optional-locks', '-c', f'safe.directory={Path(srcdir).resolve()}',
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
    requirements_by_path = {
        os.fspath(path): read_requirements(path) for path in tests
    }
    known_tests = {test_name(test) for test in all_tests}
    profiles = []
    for item in (value.strip() for value in args.profiles.split(',')):
        if not item:
            continue
        if os.path.sep not in item and (not os.path.altsep or os.path.altsep not in item):
            filename = item if item.endswith('.json') else f'{item}.json'
            path = os.path.join(suitedir, 'profiles', filename)
        else:
            path = item if os.path.isabs(item) else os.path.join(srcdir, item)
        profiles.append(load_profile(path, known_tests))

    if args.profiles and not profiles:
        raise ValueError('no profiles selected')
    roles = {profile.name for profile in profiles} & {'root', 'nonroot'}
    if len(roles) > 1:
        raise ValueError('root and nonroot profiles conflict')
    for role in roles:
        if (role == 'root') != (os.geteuid() == 0):
            raise ValueError(f'{role} profile does not match effective UID {os.geteuid()}')
    peer, profile_protocol, not_applicable, unsupported, xfail = merge_profiles(profiles)
    if any(profile.name == 'self-peer' for profile in profiles):
        if peer or file_digest(rsync_bin) != file_digest(rsync_bin2):
            raise ValueError('self-peer profile requires the same executable in both roles')
    transport = 'tcp' if args.use_tcp else 'pipe'
    tests, excluded = select_applicable_tests(tests, not_applicable, requirements_by_path)
    tests, transport_excluded = select_transport_tests(tests, transport, requirements_by_path)
    excluded.extend(transport_excluded)
    excluded_names = {name for group in excluded for name in group['tests']}
    invalid = sorted(set(xfail) & excluded_names)
    if invalid:
        raise ValueError(f'expected failures do not apply: {", ".join(invalid)}')
    effective_protocol = args.protocol
    if profile_protocol and not peer:
        if args.protocol != profile_protocol:
            raise ValueError(
                f'profile protocol {profile_protocol} does not match '
                f'{args.protocol or "the default protocol"}')
        effective_protocol = profile_protocol

    if not peer:
        return tests, profiles, peer, effective_protocol, unsupported, xfail, transport, excluded

    _, current_protocol = binary_identity(rsync_bin)
    actual_peer, actual_peer_protocol = binary_identity(rsync_bin2)
    if actual_peer != peer:
        raise ValueError(f'profile peer {peer} does not match {actual_peer}')
    if actual_peer_protocol != profile_protocol:
        raise ValueError(
            f'profile protocol {profile_protocol} does not match {actual_peer_protocol}')
    effective_protocol = min(current_protocol, actual_peer_protocol,
                             args.protocol if args.protocol is not None else current_protocol)
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
    eligible = {name for name, values in requirements.items()
                if applies_to_peer(values, peer, profile_protocol)}
    invalid = sorted(set(xfail) - eligible)
    if invalid:
        raise ValueError(f'peer deviations do not apply: {", ".join(invalid)}')
    tests = [test for test in tests
             if applies_to_peer(requirements[test_name(test)], peer,
                                effective_protocol, transport)]
    if not tests:
        raise ValueError(f'no tests apply to peer {peer}')
    return tests, profiles, peer, effective_protocol, unsupported, xfail, transport, excluded
