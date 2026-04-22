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
import glob
import os
import subprocess
import sys
import threading


def parse_args():
    p = argparse.ArgumentParser(description='Run rsync test suite')
    p.add_argument('tests', nargs='*', metavar='TEST',
                   help='Test names or patterns to run (default: all)')
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
    p.add_argument('--rsync-bin', default=None, metavar='PATH',
                   help='Path to rsync binary (default: ./rsync)')
    p.add_argument('--tooldir', default=None, metavar='DIR',
                   help='Tool/build directory (default: cwd)')
    p.add_argument('--srcdir', default=None, metavar='DIR',
                   help='Source directory (default: script directory)')
    p.add_argument('--protocol', type=int, default=None, metavar='VER',
                   help='Force protocol version (adds --protocol=VER to rsync)')
    p.add_argument('--expect-skipped', default=None, metavar='LIST',
                   help='Comma-separated list of expected-skipped tests')
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


def collect_tests(suitedir, patterns):
    """Collect test scripts matching the given patterns."""
    if not patterns:
        tests = sorted(glob.glob(os.path.join(suitedir, '*.test')))
    else:
        tests = []
        for pat in patterns:
            if not pat.endswith('.test'):
                pat = pat + '.test'
            matches = sorted(glob.glob(os.path.join(suitedir, pat)))
            tests.extend(matches)
    return tests


def build_rsync_cmd(rsync_bin, args, scratchbase):
    """Build the RSYNC command string for tests."""
    parts = []
    if args.valgrind:
        vlog = os.path.join(scratchbase, 'valgrind.%p.log')
        vopts = f'--log-file={vlog}'
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

    logfile = os.path.join(scratchdir, 'test.log')
    try:
        with open(logfile, 'w') as log:
            proc = subprocess.run(
                ['sh', '-e', testscript],
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

    show_log = always_log or (result not in (0, 77, 78))
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
    if result == 0:
        output_parts.append(f'PASS    {testbase}')
    elif result == 77:
        whyfile = os.path.join(scratchdir, 'whyskipped')
        try:
            with open(whyfile) as f:
                skipped_reason = f.read().strip()
        except FileNotFoundError:
            pass
        output_parts.append(f'SKIP    {testbase} ({skipped_reason})')
    elif result == 78:
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
    if os.environ.get('whichtests'):
        args.tests = [os.environ['whichtests']]

    # Determine directories
    tooldir = args.tooldir or os.environ.get('TOOLDIR') or os.getcwd()
    script_path = os.path.dirname(os.path.abspath(__file__))
    srcdir = args.srcdir or script_path
    if not srcdir or srcdir == '.':
        srcdir = tooldir
    rsync_bin = args.rsync_bin or os.environ.get('rsync_bin') or os.path.join(tooldir, 'rsync')

    suitedir = os.path.join(srcdir, 'testsuite')
    scratchbase = os.path.join(os.environ.get('scratchbase', tooldir), 'testtmp')
    os.makedirs(scratchbase, exist_ok=True)

    shconfig = read_shconfig(os.path.join(tooldir, 'shconfig'))
    tls_args = get_tls_args(os.path.join(tooldir, 'config.h'))
    setfacl_nodef = find_setfacl_nodef(scratchbase)
    rsync_cmd = build_rsync_cmd(rsync_bin, args, scratchbase)

    if not os.path.isfile(rsync_bin):
        sys.stderr.write(f"rsync_bin {rsync_bin} is not a file\n")
        sys.exit(2)
    if not os.path.isdir(srcdir):
        sys.stderr.write(f"srcdir {srcdir} is not a directory\n")
        sys.exit(2)

    testuser = get_testuser()

    # Print header
    print('=' * 60)
    print(f'{sys.argv[0]} running in {tooldir}')
    print(f'    rsync_bin={rsync_cmd}')
    print(f'    srcdir={srcdir}')
    print(f'    TLS_ARGS={tls_args}')
    print(f'    testuser={testuser}')
    print(f'    os={subprocess.check_output(["uname", "-a"], text=True).strip()}')
    print(f'    preserve_scratch={"yes" if args.preserve_scratch else "no"}')
    if args.valgrind:
        print(f'    valgrind=enabled (logs in valgrind.*.log)')
    if args.parallel > 1:
        print(f'    parallel={args.parallel}')
    print(f'    scratchbase={scratchbase}')

    # Build base environment for test scripts
    path = os.environ.get('PATH', '')
    if os.path.isdir('/usr/xpg4/bin'):
        path = '/usr/xpg4/bin:' + path

    base_env = os.environ.copy()
    base_env.update({
        'PATH': path,
        'POSIXLY_CORRECT': '1',
        'TOOLDIR': tooldir,
        'srcdir': srcdir,
        'RSYNC': rsync_cmd,
        'TLS_ARGS': tls_args,
        'RUNSHFLAGS': '-e',
        'scratchbase': scratchbase,
        'suitedir': suitedir,
        'TESTRUN_TIMEOUT': str(args.timeout),
        'HOME': scratchbase,
    })
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

    # Record test order for consistent skipped-list output
    test_order = {os.path.basename(t).replace('.test', ''): i for i, t in enumerate(tests)}

    passed = 0
    failed = 0
    skipped = 0
    skipped_list = []

    def process_result(tr):
        """Process a TestResult and update counters. Returns True if test failed."""
        nonlocal passed, failed, skipped
        with _print_lock:
            if tr.output:
                print(tr.output)
        scratchdir = os.path.join(scratchbase, tr.testbase)
        if tr.result == 0:
            passed += 1
            if not args.preserve_scratch and os.path.isdir(scratchdir):
                subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
            return False
        elif tr.result == 77:
            skipped_list.append(tr.testbase)
            skipped += 1
            if not args.preserve_scratch and os.path.isdir(scratchdir):
                subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
            return False
        elif tr.result == 78:
            failed += 1
            return True
        else:
            failed += 1
            return True

    if args.parallel > 1:
        # Parallel execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {}
            for testscript in tests:
                testbase = os.path.basename(testscript).replace('.test', '')
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
            testbase = os.path.basename(testscript).replace('.test', '')
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
        for vlog in sorted(glob.glob(os.path.join(scratchbase, 'valgrind.*.log'))):
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
    if skipped > 0:
        print(f'      {skipped} skipped')
    if vg_errors > 0:
        print(f'      {vg_errors} valgrind error(s) found (see logs in {scratchbase})')

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
    if exit_code == 0 and skipped_str != args.expect_skipped:
        exit_code = 1

    print(f'overall result is {exit_code}')
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
