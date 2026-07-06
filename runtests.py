#!/usr/bin/env python3

# Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
# Copyright (C) 2003-2022 Wayne Davison
# Copyright (C) 2026 Andrew Tridgell
#
# Rewrite of runtests.sh in Python (runtests.sh is now deprecated).
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version
# 2 as published by the Free Software Foundation.

"""rsync test runner.

Invokes test scripts from testsuite/ and reports results.
Can be called by 'make check' or directly.

Usage:
    ./runtests.py [options] [TEST ...]

Each TEST is a test name (e.g. 'delete') or glob pattern (e.g. 'xattr*').
If no tests are specified, all tests are run.
"""

import argparse
import concurrent.futures
import fnmatch
import glob
import os
import subprocess
import sys
import threading

# Share the test exit-code enum with the test helpers. exitcodes.py lives in
# testsuite/ (next to this script); it has no import-time side effects.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testsuite'))
from exitcodes import Exit


def parse_args():
    p = argparse.ArgumentParser(description='Run rsync test suite')
    p.add_argument('tests', nargs='*', metavar='TEST',
                   help='Test names or patterns to run (default: all)')
    p.add_argument('--exclude', default=None, metavar='LIST',
                   help='Comma-separated test names/globs to skip entirely: '
                        'they are not run and not reported as skipped. Useful '
                        'for tests that cannot work in a given build/CI '
                        'environment (e.g. a restricted buildd chroot). '
                        'Falls back to the RSYNC_EXCLUDE environment variable.')
    p.add_argument('-j', '--parallel', type=int, default=1, metavar='N',
                   help='Run up to N tests in parallel (default: 1)')
    p.add_argument('--valgrind', action='store_true',
                   help='Run rsync under valgrind (logs to per-process files)')
    p.add_argument('--valgrind-opts', default='', metavar='OPTS',
                   help='Extra valgrind options (e.g. "--leak-check=full")')
    p.add_argument('--preserve-scratch', action='store_true',
                   help='Keep scratch directories after tests complete')
    p.add_argument('--log-level', type=int, default=1, metavar='N',
                   help='Verbosity level 1-10 (default: 1)')
    p.add_argument('--always-log', action='store_true',
                   help='Show test logs even for passing tests')
    p.add_argument('--stop-on-fail', action='store_true',
                   help='Stop after first test failure')
    p.add_argument('--timeout', type=int, default=300, metavar='SECS',
                   help='Per-test timeout in seconds (default: 300)')
    p.add_argument('--race-timeout', type=float, default=5.0, metavar='SECS',
                   help='Budget (seconds) a TOCTOU symlink-race test may spend '
                        'trying to win its race before concluding (default: 5)')
    p.add_argument('--rsync-bin', default=None, metavar='PATH',
                   help='Path to rsync binary (default: ./rsync)')
    p.add_argument('--rsync-bin2', default=None, metavar='PATH',
                   help='Path to a second ("peer") rsync binary used for the '
                        'daemon side and remote-shell --rsync-path. Lets the '
                        'suite mix two rsync versions over the wire. Default: '
                        'same as --rsync-bin (no version mixing).')
    p.add_argument('--tooldir', default=None, metavar='DIR',
                   help='Tool/build directory (default: cwd)')
    p.add_argument('--srcdir', default=None, metavar='DIR',
                   help='Source directory (default: script directory)')
    p.add_argument('--protocol', type=int, default=None, metavar='VER',
                   help='Force protocol version (adds --protocol=VER to rsync)')
    p.add_argument('--expect-skipped', default=None, metavar='LIST',
                   help='Comma-separated list of expected-skipped tests')
    p.add_argument('--expect-result', default=None, metavar='FILE',
                   help='Path to an expected-outcome manifest (one '
                        '"<testname> <pass|skip|fail|xfail>" per line). When '
                        'set, ONLY the tests listed in FILE are run, and each '
                        "test's actual outcome is compared against its "
                        'expected one; any mismatch (including an unexpected '
                        'pass) fails the run. Used for version-mixing CI.')
    p.add_argument('--use-tcp', action='store_true',
                   help='Run daemon tests against a real rsyncd bound to '
                        '127.0.0.1 (non-default). The default is the secure '
                        'stdio-pipe transport, which opens no listening '
                        'socket; --use-tcp exposes a loopback port for the '
                        'duration of each daemon test.')
    return p.parse_args()


def find_setfacl_nodef(scratchbase):
    """Determine the setfacl command to remove default ACLs."""
    for cmd in [
        ['setacl', '-k', 'u::7,g::5,o:5', scratchbase],
        ['setfacl', '-k', scratchbase],
        ['setfacl', '-s', 'u::7,g::5,o:5', scratchbase],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
            return cmd[:2] if cmd[0] == 'setacl' else cmd[:2]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    try:
        r = subprocess.run(['setfacl', '--help'], capture_output=True, text=True, timeout=5)
        if '-k,' in r.stdout or '-k,' in r.stderr:
            return ['setfacl', '-k']
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_tls_args(config_h):
    """Determine TLS_ARGS from config.h."""
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
    """Read shell config variables from shconfig."""
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
    """Determine the current test user."""
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


def prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef):
    """Prepare a scratch directory for a test."""
    if os.path.isdir(scratchdir):
        subprocess.run(['chmod', '-R', 'u+rwX', scratchdir], capture_output=True)
        subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
    os.makedirs(scratchdir, exist_ok=True)
    if setfacl_nodef:
        subprocess.run(setfacl_nodef + [scratchdir], capture_output=True)
    try:
        os.chmod(scratchdir, os.stat(scratchdir).st_mode & ~0o2000)  # clear setgid
    except OSError:
        pass
    src_link = os.path.join(scratchdir, 'src')
    if not os.path.exists(src_link):
        if os.path.isabs(srcdir):
            os.symlink(srcdir, src_link)
        else:
            os.symlink(os.path.join(tooldir, srcdir), src_link)


# Python tests are identified by a positive "_test.py" suffix so that
# helper modules (e.g. rsyncfns.py) sit in testsuite/ without being mistaken
# for tests.
_PY_TEST_SUFFIX = '_test.py'


def _is_test_path(path):
    return os.path.basename(path).endswith(_PY_TEST_SUFFIX)


def _testbase(path):
    """Strip the test extension to get the canonical test name."""
    base = os.path.basename(path)
    if base.endswith(_PY_TEST_SUFFIX):
        return base[:-len(_PY_TEST_SUFFIX)]
    return base


def collect_tests(suitedir, patterns):
    """Collect test scripts (_test.py) matching the given patterns."""
    if not patterns:
        candidates = glob.glob(os.path.join(suitedir, '*' + _PY_TEST_SUFFIX))
        tests = sorted(p for p in candidates if _is_test_path(p))
    else:
        seen = set()
        tests = []
        for pat in patterns:
            # Accept either bare name ("mkpath"), explicit extension, or glob.
            if pat.endswith('.py'):
                pats = [pat]
            else:
                pats = [pat + _PY_TEST_SUFFIX]
            for p in pats:
                for m in sorted(glob.glob(os.path.join(suitedir, p))):
                    if _is_test_path(m) and m not in seen:
                        seen.add(m)
                        tests.append(m)
    return tests


_VALID_OUTCOMES = ('pass', 'skip', 'fail', 'xfail')


def parse_expect_result(path):
    """Parse an expected-outcome manifest into {testbase: outcome}.

    One "<testname> <outcome>" entry per line; '#' comments and blank lines
    are ignored. outcome is one of pass|skip|fail|xfail. The set of listed
    tests doubles as the run set (see main()). Exits 2 on a malformed file.
    """
    expect = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 2 or fields[1] not in _VALID_OUTCOMES:
                sys.stderr.write(
                    f"{path}:{lineno}: expected '<testname> "
                    f"<{'|'.join(_VALID_OUTCOMES)}>', got: {raw.rstrip()}\n"
                )
                sys.exit(Exit.ERROR)
            expect[fields[0]] = fields[1]
    return expect


def outcome_of(result):
    """Map a per-test exit code to an outcome string."""
    if result == Exit.PASS:
        return 'pass'
    if result == Exit.SKIP:
        return 'skip'
    if result == Exit.XFAIL:
        return 'xfail'
    return 'fail'


def build_rsync_cmd(rsync_bin, args, scratchbase):
    """Build the RSYNC command string for tests."""
    parts = []
    if args.valgrind:
        # Logs go in a world-writable+sticky subdir so that rsync children
        # which drop privileges (the setpriv cap-drop in partial_nowrite, a
        # daemon dropping to the module's uid) can still create their log file
        # even when scratchbase itself is root-owned.
        vgdir = os.path.join(scratchbase, 'valgrind-logs')
        os.makedirs(vgdir, exist_ok=True)
        os.chmod(vgdir, 0o1777)
        vlog = os.path.join(vgdir, 'valgrind.%p.log')
        vopts = f'--log-file={vlog}'
        supp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'testsuite', 'valgrind.supp')
        if os.path.exists(supp):
            vopts += f' --suppressions={supp}'
        if args.valgrind_opts:
            vopts += ' ' + args.valgrind_opts
        parts.append(f'valgrind {vopts}')
    parts.append(rsync_bin)
    if args.protocol is not None:
        parts.append(f'--protocol={args.protocol}')
    return ' '.join(parts)


class TestResult:
    """Result of a single test execution."""
    __slots__ = ('testbase', 'result', 'output', 'skipped_reason')

    def __init__(self, testbase, result, output='', skipped_reason=''):
        self.testbase = testbase
        self.result = result
        self.output = output
        self.skipped_reason = skipped_reason


def run_one_test(testscript, testbase, scratchdir, base_env, timeout,
                 srcdir, tooldir, setfacl_nodef, always_log):
    """Run a single test. Returns a TestResult.

    This function is safe to call from multiple threads — it uses only
    per-test state (unique scratchdir, copy of env).
    """
    prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef)

    env = base_env.copy()
    env['scratchdir'] = scratchdir

    # Dispatch by extension: shell tests via /bin/sh -e, Python tests via
    # the same python3 that's running this runner.
    if testscript.endswith('.py'):
        cmd = [sys.executable, testscript]
    else:
        cmd = ['sh', '-e', testscript]

    logfile = os.path.join(scratchdir, 'test.log')
    try:
        with open(logfile, 'w') as log:
            proc = subprocess.run(
                cmd,
                stdout=log, stderr=subprocess.STDOUT,
                env=env, timeout=timeout,
                cwd=env.get('TOOLDIR', '.')
            )
        result = proc.returncode
    except subprocess.TimeoutExpired:
        result = 1
        with open(logfile, 'a') as log:
            log.write(f"\nTIMEOUT: test took over {timeout} seconds\n")

    # Build output text
    output_parts = []

    show_log = always_log or (result not in (Exit.PASS, Exit.SKIP, Exit.XFAIL))
    if show_log:
        output_parts.append(f'----- {testbase} log follows')
        try:
            with open(logfile) as f:
                output_parts.append(f.read().rstrip())
        except FileNotFoundError:
            pass
        output_parts.append(f'----- {testbase} log ends')
        rsyncd_log = os.path.join(scratchdir, 'rsyncd.log')
        if os.path.isfile(rsyncd_log):
            output_parts.append(f'----- {testbase} rsyncd.log follows')
            with open(rsyncd_log) as f:
                output_parts.append(f.read().rstrip())
            output_parts.append(f'----- {testbase} rsyncd.log ends')

    skipped_reason = ''
    if result == Exit.PASS:
        output_parts.append(f'PASS    {testbase}')
    elif result == Exit.SKIP:
        whyfile = os.path.join(scratchdir, 'whyskipped')
        try:
            with open(whyfile) as f:
                skipped_reason = f.read().strip()
        except FileNotFoundError:
            pass
        output_parts.append(f'SKIP    {testbase} ({skipped_reason})')
    elif result == Exit.XFAIL:
        output_parts.append(f'XFAIL   {testbase}')
    else:
        output_parts.append(f'FAIL    {testbase}')

    return TestResult(testbase, result, '\n'.join(output_parts), skipped_reason)


# Lock for serializing output in parallel mode
_print_lock = threading.Lock()


def main():
    args = parse_args()

    # Also accept legacy environment variables
    if args.preserve_scratch or os.environ.get('preserve_scratch') == 'yes':
        args.preserve_scratch = True
    if args.log_level == 1:
        args.log_level = int(os.environ.get('loglevel', '1'))
    if args.expect_skipped is None:
        args.expect_skipped = os.environ.get('RSYNC_EXPECT_SKIPPED', 'IGNORE')
    if args.exclude is None:
        args.exclude = os.environ.get('RSYNC_EXCLUDE', '')
    if os.environ.get('whichtests'):
        args.tests = [os.environ['whichtests']]

    # Determine directories
    tooldir = args.tooldir or os.environ.get('TOOLDIR') or os.getcwd()
    script_path = os.path.dirname(os.path.abspath(__file__))
    srcdir = args.srcdir or script_path
    if not srcdir or srcdir == '.':
        srcdir = tooldir
    rsync_bin = args.rsync_bin or os.environ.get('rsync_bin') or os.path.join(tooldir, 'rsync')
    # Absolutize: tests run with subprocess(cwd=TOOLDIR) below, so a relative
    # argv[0] would re-resolve against TOOLDIR rather than the runner's
    # invocation cwd, breaking --rsync-bin=../foo/rsync forms.  abspath()
    # captures os.getcwd() now, which is what the operator intended.
    if rsync_bin and not os.path.isabs(rsync_bin):
        rsync_bin = os.path.abspath(rsync_bin)

    # Optional second ("peer") binary for the daemon / remote-shell side, so a
    # run can mix two rsync versions. Defaults to rsync_bin -> no mixing.
    rsync_bin2 = args.rsync_bin2 or os.environ.get('rsync_bin2') or rsync_bin
    if rsync_bin2 and not os.path.isabs(rsync_bin2):
        rsync_bin2 = os.path.abspath(rsync_bin2)

    suitedir = os.path.join(srcdir, 'testsuite')
    scratchbase = os.path.join(os.environ.get('scratchbase', tooldir), 'testtmp')
    os.makedirs(scratchbase, exist_ok=True)

    shconfig = read_shconfig(os.path.join(tooldir, 'shconfig'))
    tls_args = get_tls_args(os.path.join(tooldir, 'config.h'))
    setfacl_nodef = find_setfacl_nodef(scratchbase)
    rsync_cmd = build_rsync_cmd(rsync_bin, args, scratchbase)
    rsync_peer_cmd = build_rsync_cmd(rsync_bin2, args, scratchbase)

    if not os.path.isfile(rsync_bin):
        sys.stderr.write(f"rsync_bin {rsync_bin} is not a file\n")
        sys.exit(Exit.ERROR)
    if not os.path.isfile(rsync_bin2):
        sys.stderr.write(f"rsync_bin2 {rsync_bin2} is not a file\n")
        sys.exit(Exit.ERROR)
    if not os.path.isdir(srcdir):
        sys.stderr.write(f"srcdir {srcdir} is not a directory\n")
        sys.exit(Exit.ERROR)

    # Helper programs the test scripts invoke directly. Missing any of these
    # would cause many tests to fail with confusing "not found" errors, so
    # check up front and point the user at the make target that builds them.
    required_helpers = ['tls', 'trimslash', 't_unsafe', 't_chmod_secure',
                        't_secure_relpath',
                        'wildtest', 'getgroups', 'getfsdev']
    missing = [h for h in required_helpers
               if not os.path.isfile(os.path.join(tooldir, h))]
    if missing:
        sys.stderr.write(
            f"runtests.py: missing test helper program(s) in {tooldir}: "
            f"{', '.join(missing)}\n"
            f"Build them with: make {' '.join(missing)}\n"
            f"or run the full test target: make check\n"
        )
        sys.exit(Exit.ERROR)

    testuser = get_testuser()

    # Print header
    print('=' * 60)
    print(f'{sys.argv[0]} running in {tooldir}')
    print(f'    rsync_bin={rsync_cmd}')
    if rsync_peer_cmd != rsync_cmd:
        print(f'    rsync_peer={rsync_peer_cmd}')
    print(f'    srcdir={srcdir}')
    print(f'    TLS_ARGS={tls_args}')
    print(f'    testuser={testuser}')
    print(f'    os={subprocess.check_output(["uname", "-a"], text=True).strip()}')
    print(f'    preserve_scratch={"yes" if args.preserve_scratch else "no"}')
    if args.valgrind:
        print(f'    valgrind=enabled (logs in valgrind-logs/valgrind.*.log)')
    if args.parallel > 1:
        print(f'    parallel={args.parallel}')
    print(f'    daemon_transport={"tcp (loopback)" if args.use_tcp else "pipe (secure default)"}')
    print(f'    scratchbase={scratchbase}')

    # Build base environment for test scripts
    path = os.environ.get('PATH', '')
    if os.path.isdir('/usr/xpg4/bin'):
        path = '/usr/xpg4/bin:' + path

    # Make the testsuite/ directory importable so Python tests can `import rsyncfns`.
    pythonpath = suitedir
    if os.environ.get('PYTHONPATH'):
        pythonpath = suitedir + os.pathsep + os.environ['PYTHONPATH']

    base_env = os.environ.copy()
    base_env.update({
        'PATH': path,
        'POSIXLY_CORRECT': '1',
        'TOOLDIR': tooldir,
        'srcdir': srcdir,
        'RSYNC': rsync_cmd,
        'RSYNC_PEER': rsync_peer_cmd,
        'TLS_ARGS': tls_args,
        'RUNSHFLAGS': '-e',
        'scratchbase': scratchbase,
        'suitedir': suitedir,
        'TESTRUN_TIMEOUT': str(args.timeout),
        'race_timeout': str(args.race_timeout),
        'HOME': scratchbase,
        'PYTHONPATH': pythonpath,
    })
    if args.use_tcp:
        # Opt-in: daemon tests start a real rsyncd on a claimed loopback port.
        # Default (unset) keeps the secure stdio-pipe transport.
        base_env['RSYNC_TEST_USE_TCP'] = '1'
    for k, v in shconfig.items():
        if v:
            base_env[k] = v
    if setfacl_nodef:
        base_env['setfacl_nodef'] = ' '.join(setfacl_nodef)
    else:
        base_env['setfacl_nodef'] = 'true'
    if args.log_level > 8:
        base_env['RUNSHFLAGS'] = '-e -x'

    # Collect tests
    tests = collect_tests(suitedir, args.tests)
    full_run = len(args.tests) == 0

    # Drop excluded tests entirely (matched by basename against name/glob).
    excl = [e.strip() for e in args.exclude.split(',') if e.strip()]
    if excl:
        before = len(tests)
        tests = [t for t in tests
                 if not any(fnmatch.fnmatch(_testbase(t), pat) for pat in excl)]
        if before != len(tests):
            print(f"Excluding {before - len(tests)} test(s) matching: "
                  f"{', '.join(excl)}")

    # An expected-result manifest defines BOTH the run set (its keys) and the
    # expected per-test outcome (its values). Used for version-mixing runs.
    expect = parse_expect_result(args.expect_result) if args.expect_result else None
    if expect is not None:
        have = {_testbase(t) for t in tests}
        unknown = sorted(k for k in expect if k not in have)
        if unknown:
            sys.stderr.write(
                "runtests.py: --expect-result lists test(s) with no matching "
                f"test file (ignored): {', '.join(unknown)}\n"
            )
        tests = [t for t in tests if _testbase(t) in expect]
        full_run = False

    def _cls(outcome):
        """Equivalence class for outcome comparison: fail and xfail both just
        mean 'broke', so a manifest 'fail' matches an actual fail OR xfail."""
        return 'broken' if outcome in ('fail', 'xfail') else outcome

    def mismatch(testbase, actual):
        """True if actual outcome disagrees with the manifest expectation."""
        return expect is not None and _cls(expect[testbase]) != _cls(actual)

    # Record test order for consistent skipped-list output
    test_order = {_testbase(t): i for i, t in enumerate(tests)}

    passed = 0
    failed = 0
    skipped = 0
    xfailed = 0
    skipped_list = []
    outcomes = {}  # testbase -> actual outcome string ('pass'/'skip'/'fail'/'xfail')

    def process_result(tr):
        """Process a TestResult and update counters. Returns True if the test
        should count as a failure for --stop-on-fail purposes."""
        nonlocal passed, failed, skipped, xfailed
        with _print_lock:
            if tr.output:
                print(tr.output)
        scratchdir = os.path.join(scratchbase, tr.testbase)
        oc = outcome_of(tr.result)
        outcomes[tr.testbase] = oc
        if tr.result == Exit.PASS:
            passed += 1
        elif tr.result == Exit.SKIP:
            skipped_list.append(tr.testbase)
            skipped += 1
        elif tr.result == Exit.XFAIL:
            # XFAIL: an expected failure (a known, documented residual the test
            # asserts against). Reported distinctly but does NOT fail the suite;
            # when the underlying issue is fixed the test returns 0 instead.
            xfailed += 1
        else:
            failed += 1
        if tr.result in (Exit.PASS, Exit.SKIP, Exit.XFAIL) and not args.preserve_scratch \
                and os.path.isdir(scratchdir):
            subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        # With a manifest, only a mismatch is a "failure" (an expected fail is
        # fine); without one, any non-pass/non-skip/non-xfail result is a failure.
        if expect is not None:
            return mismatch(tr.testbase, oc)
        return tr.result not in (Exit.PASS, Exit.SKIP, Exit.XFAIL)

    if args.parallel > 1:
        # Parallel execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {}
            for testscript in tests:
                testbase = _testbase(testscript)
                scratchdir = os.path.join(scratchbase, testbase)
                timeout = 600 if 'hardlinks' in testbase else args.timeout
                f = executor.submit(
                    run_one_test, testscript, testbase, scratchdir,
                    base_env, timeout, srcdir, tooldir, setfacl_nodef,
                    args.always_log
                )
                futures[f] = testbase

            for f in concurrent.futures.as_completed(futures):
                tr = f.result()
                is_fail = process_result(tr)
                if is_fail and args.stop_on_fail:
                    # Cancel pending futures
                    for pending in futures:
                        pending.cancel()
                    break
    else:
        # Sequential execution
        for testscript in tests:
            testbase = _testbase(testscript)
            scratchdir = os.path.join(scratchbase, testbase)
            timeout = 600 if 'hardlinks' in testbase else args.timeout
            tr = run_one_test(
                testscript, testbase, scratchdir,
                base_env, timeout, srcdir, tooldir, setfacl_nodef,
                args.always_log
            )
            is_fail = process_result(tr)
            if is_fail and args.stop_on_fail:
                break

    # Check valgrind logs for errors
    vg_errors = 0
    if args.valgrind:
        for vlog in sorted(glob.glob(os.path.join(scratchbase, 'valgrind-logs', 'valgrind.*.log'))):
            try:
                with open(vlog) as f:
                    content = f.read()
                for line in content.splitlines():
                    if 'ERROR SUMMARY:' in line and 'ERROR SUMMARY: 0 errors' not in line:
                        vg_errors += 1
                        print(f'----- valgrind errors in {os.path.basename(vlog)}:')
                        print(content)
                        break
            except FileNotFoundError:
                pass

    # Summary
    print('-' * 60)
    print('----- overall results:')
    print(f'      {passed} passed')
    if failed > 0:
        print(f'      {failed} failed')
    if xfailed > 0:
        print(f'      {xfailed} xfailed (expected)')
    if skipped > 0:
        print(f'      {skipped} skipped')
    if vg_errors > 0:
        print(f'      {vg_errors} valgrind error(s) found (see logs in {os.path.join(scratchbase, "valgrind-logs")})')

    if expect is not None:
        # Version-mixing mode: the run is judged purely on whether each test's
        # actual outcome matched its manifest expectation. An expected 'fail'
        # is fine; an UNEXPECTED pass (xpass) or any other divergence is not.
        mismatches = []
        for tb in sorted(expect, key=lambda x: test_order.get(x, 1 << 30)):
            actual = outcomes.get(tb, 'notrun')
            if actual == 'notrun' or mismatch(tb, actual):
                mismatches.append((tb, expect[tb], actual))
        if mismatches:
            print('----- expected-result mismatches:')
            for tb, want, got in mismatches:
                tag = ' (xpass)' if _cls(want) == 'broken' and got == 'pass' else ''
                print(f'      {tb}: expected {want}, got {got}{tag}')
        print('-' * 60)
        exit_code = len(mismatches) + vg_errors
        print(f'overall result is {exit_code}')
        sys.exit(exit_code)

    skipped_str = ','.join(sorted(skipped_list, key=lambda x: test_order.get(x, 0)))
    if full_run and args.expect_skipped != 'IGNORE':
        print('----- skipped results:')
        print(f'      expected: {args.expect_skipped}')
        print(f'      got:      {skipped_str}')
    else:
        skipped_str = ''
        args.expect_skipped = ''

    print('-' * 60)

    exit_code = failed + vg_errors
    if exit_code == 0:
        # Compare the skipped set order-insensitively: which tests skipped is
        # what matters, not the order runtests happened to collect them in
        # (that order is just sorted filenames -- an easy thing to get subtly
        # wrong when maintaining the per-platform expected lists).
        got = set(s for s in skipped_str.split(',') if s)
        want = set(s for s in args.expect_skipped.split(',') if s)
        if got != want:
            exit_code = 1

    print(f'overall result is {exit_code}')
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
