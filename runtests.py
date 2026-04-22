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
import glob
import os
import signal
import subprocess
import sys
import time


def parse_args():
    p = argparse.ArgumentParser(description='Run rsync test suite')
    p.add_argument('tests', nargs='*', metavar='TEST',
                   help='Test names or patterns to run (default: all)')
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
    # Also check if setfacl supports -k via --help
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
    # Symlink to source directory
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


def build_rsync_cmd(rsync_bin, args, extra_rsync_opts, scratchbase):
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
    if extra_rsync_opts:
        parts.extend(extra_rsync_opts)
    return ' '.join(parts)


def run_test(testscript, scratchdir, env, timeout):
    """Run a single test script with timeout. Returns exit code."""
    logfile = os.path.join(scratchdir, 'test.log')
    try:
        with open(logfile, 'w') as log:
            proc = subprocess.run(
                ['sh', '-e', testscript],
                stdout=log, stderr=subprocess.STDOUT,
                env=env, timeout=timeout,
                cwd=env.get('TOOLDIR', '.')
            )
        return proc.returncode
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"TIMEOUT: {testscript} took over {timeout} seconds\n")
        return 1


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

    # Read shconfig for ECHO_N/ECHO_C/ECHO_T, HOST_OS, etc.
    shconfig = read_shconfig(os.path.join(tooldir, 'shconfig'))

    # Determine TLS args and setfacl
    tls_args = get_tls_args(os.path.join(tooldir, 'config.h'))
    setfacl_nodef = find_setfacl_nodef(scratchbase)

    # Collect extra rsync options from remaining argv (after --)
    extra_rsync_opts = []

    # Build RSYNC command
    rsync_cmd = build_rsync_cmd(rsync_bin, args, extra_rsync_opts, scratchbase)

    # Validate
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
    print(f'    scratchbase={scratchbase}')

    # Build environment for test scripts
    # For Solaris compatibility
    path = os.environ.get('PATH', '')
    if os.path.isdir('/usr/xpg4/bin'):
        path = '/usr/xpg4/bin:' + path

    test_env = os.environ.copy()
    test_env.update({
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
    # Pass through shconfig values
    for k, v in shconfig.items():
        if v:
            test_env[k] = v
    # setfacl_nodef as a shell-friendly string
    if setfacl_nodef:
        test_env['setfacl_nodef'] = ' '.join(setfacl_nodef)
    else:
        test_env['setfacl_nodef'] = 'true'

    if args.log_level > 8:
        test_env['RUNSHFLAGS'] = '-e -x'

    # Collect tests
    tests = collect_tests(suitedir, args.tests)
    full_run = len(args.tests) == 0

    passed = 0
    failed = 0
    skipped = 0
    skipped_list = []

    for testscript in tests:
        testbase = os.path.basename(testscript).replace('.test', '')
        scratchdir = os.path.join(scratchbase, testbase)

        prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef)

        test_env['scratchdir'] = scratchdir

        # Longer timeout for hardlinks test
        timeout = 600 if 'hardlinks' in testbase else args.timeout

        result = run_test(testscript, scratchdir, test_env, timeout)

        logfile = os.path.join(scratchdir, 'test.log')

        # Show log on failure or if always_log
        if args.always_log or (result not in (0, 77, 78)):
            print(f'----- {testbase} log follows')
            try:
                with open(logfile) as f:
                    print(f.read(), end='')
            except FileNotFoundError:
                pass
            print(f'----- {testbase} log ends')
            rsyncd_log = os.path.join(scratchdir, 'rsyncd.log')
            if os.path.isfile(rsyncd_log):
                print(f'----- {testbase} rsyncd.log follows')
                with open(rsyncd_log) as f:
                    print(f.read(), end='')
                print(f'----- {testbase} rsyncd.log ends')

        if result == 0:
            print(f'PASS    {testbase}')
            passed += 1
            if not args.preserve_scratch and os.path.isdir(scratchdir):
                subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        elif result == 77:
            whyfile = os.path.join(scratchdir, 'whyskipped')
            why = ''
            try:
                with open(whyfile) as f:
                    why = f.read().strip()
            except FileNotFoundError:
                pass
            print(f'SKIP    {testbase} ({why})')
            skipped_list.append(testbase)
            skipped += 1
            if not args.preserve_scratch and os.path.isdir(scratchdir):
                subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        elif result == 78:
            print(f'XFAIL   {testbase}')
            failed += 1
        else:
            print(f'FAIL    {testbase}')
            failed += 1
            if args.stop_on_fail:
                break

    # Check valgrind logs for errors
    vg_errors = 0
    if args.valgrind:
        for vlog in sorted(glob.glob(os.path.join(scratchbase, 'valgrind.*.log'))):
            try:
                with open(vlog) as f:
                    content = f.read()
                # Check for non-zero error summary
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

    skipped_str = ','.join(skipped_list)
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
