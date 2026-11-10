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

import argparse
import concurrent.futures
import fnmatch
import glob
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .metadata import (applies_to_peer, discover_tests, placeholder_target,
                       read_requirements, test_name)
from .process import file_digest
from .profile import load_binaries, load_profile, merge_profiles, parse_peer_banner
from .results import Exit, TestResult, read_receipt, verdict_of, write_receipt

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

def _move_aside(path):
    n = 0
    while os.path.exists(f"{path}.corrupt.{os.getpid()}.{n}"):
        n += 1
    try:
        os.rename(path, f"{path}.corrupt.{os.getpid()}.{n}")
    except OSError:
        pass

def prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef):
    if os.path.isdir(scratchdir):
        subprocess.run(['chmod', '-R', 'u+rwX', scratchdir], capture_output=True)
        subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        if os.path.isdir(scratchdir):
            _move_aside(scratchdir)
    os.makedirs(scratchdir, exist_ok=True)
    if setfacl_nodef:
        subprocess.run(setfacl_nodef + [scratchdir], capture_output=True)
    try:
        os.chmod(scratchdir, os.stat(scratchdir).st_mode & ~0o2000)
    except OSError:
        pass
    src_link = os.path.join(scratchdir, 'src')
    if not os.path.exists(src_link):
        if os.path.isabs(srcdir):
            os.symlink(srcdir, src_link)
        else:
            os.symlink(os.path.join(tooldir, srcdir), src_link)

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

def select_applicable_tests(tests, not_applicable):
    selected = []
    excluded = {}
    capabilities_to_exclude = set(not_applicable)
    for path in tests:
        requirements = read_requirements(path) or {}
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

def describe_tests(tests):
    descriptions = []
    for path in tests:
        metadata = read_requirements(path)
        descriptions.append({'name': test_name(path), **(metadata or {})})
    return descriptions

_TIMING_TOP = 25

def print_timing_report(durations, outcomes, run_wall, parallel):
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(durations.values())
    print(f'----- slowest tests (of {len(ranked)}, wall-clock each):')
    for name, secs in ranked[:_TIMING_TOP]:
        print(f'      {secs:7.1f}s  {name:<32} {outcomes.get(name, "?")}')
    print(f'      serial sum {total:.0f}s over {len(ranked)} tests; '
          f'run took {run_wall:.0f}s with -j{parallel}')
    slowest, slowest_secs = ranked[0]
    if parallel > 1:
        print(f'      floor {slowest_secs:.0f}s ({slowest}) = '
              f'{100.0 * slowest_secs / run_wall:.0f}% of this run; '
              f'ideal at -j{parallel} is {total / parallel:.0f}s')

def build_rsync_cmd(rsync_bin, args, scratchbase):
    parts = []
    if args.valgrind:
        vgdir = os.path.join(scratchbase, 'valgrind-logs')
        os.makedirs(vgdir, exist_ok=True)
        os.chmod(vgdir, 0o1777)
        vlog = os.path.join(vgdir, 'valgrind.%p.log')
        vopts = f'--log-file={vlog}'
        supp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'valgrind.supp')
        if os.path.exists(supp):
            local_supp = os.path.join(vgdir, 'valgrind.supp')
            shutil.copyfile(supp, local_supp)
            os.chmod(local_supp, 0o644)
            vopts += f' --suppressions={local_supp}'
        if args.valgrind_opts:
            vopts += ' ' + args.valgrind_opts
        parts.append(f'valgrind {vopts}')
    parts.append(rsync_bin)
    if args.protocol is not None:
        parts.append(f'--protocol={args.protocol}')
    return ' '.join(parts)

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

def source_identity(srcdir):
    if not (Path(srcdir) / '.git').exists():
        return None, None
    command = ['git', '--no-optional-locks', '-c', f'safe.directory={Path(srcdir).resolve()}',
               '-C', str(srcdir)]
    try:
        revision = subprocess.run(command + ['rev-parse', '--verify', 'HEAD'],
                                  capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        status = subprocess.run(command + ['status', '--porcelain'],
                                capture_output=True, text=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return revision, bool(status)

def run_one_test(testscript, testbase, scratchdir, base_env, timeout,
                 srcdir, tooldir, setfacl_nodef, always_log, cancelled):
    started = time.monotonic()
    if cancelled.is_set():
        return TestResult(testbase, Exit.ERROR, f'ERROR   {testbase} (cancelled)')
    prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef)

    env = base_env.copy()
    env['scratchdir'] = scratchdir
    env['TESTRUN_TIMEOUT'] = str(timeout)

    target = placeholder_target(testscript)
    if target:
        testscript = os.path.join(scratchdir, os.path.basename(testscript))
        shutil.copyfile(target, testscript)

    cmd = [sys.executable, '-S', os.path.join(_SUITE_DIR, 'harness', 'execute.py'), testscript]

    logfile = os.path.join(scratchdir, 'test.log')
    with open(logfile, 'w') as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log, stderr=subprocess.STDOUT,
            env=env, cwd=env.get('TOOLDIR', '.'),
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        reason = None
        while True:
            if cancelled.is_set():
                reason = 'CANCELLED: run interrupted'
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = f'TIMEOUT: test took over {timeout} seconds'
                break
            try:
                result = proc.wait(timeout=min(remaining, 0.25))
                break
            except subprocess.TimeoutExpired:
                continue
        if reason:
            # The test can exit before its children
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if sig == signal.SIGKILL:
                        raise
            result = Exit.ERROR
            log.write(f'\n{reason}\n')

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
    unsupported = ''
    if result == Exit.PASS:
        output_parts.append(f'PASS    {testbase}')
    elif result == Exit.SKIP:
        whyfile = os.path.join(scratchdir, 'whyskipped')
        try:
            with open(whyfile) as f:
                skipped_reason = f.read().strip()
        except FileNotFoundError:
            pass
        try:
            with open(os.path.join(scratchdir, 'unsupported')) as stream:
                unsupported = stream.read().strip()
        except FileNotFoundError:
            pass
        label = 'UNSUPPORTED' if unsupported else 'SKIP'
        output_parts.append(f'{label}    {testbase} ({skipped_reason})')
    elif result == Exit.XFAIL:
        output_parts.append(f'XFAIL   {testbase}')
    else:
        label = 'FAIL' if result == Exit.FAIL else 'ERROR'
        output_parts.append(f'{label}    {testbase}')

    return TestResult(testbase, result, '\n'.join(output_parts), skipped_reason,
                      unsupported, time.monotonic() - started)

_print_lock = threading.Lock()

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


def print_run_header(args, tooldir, srcdir, scratchbase, rsync_cmd, rsync_peer_cmd, tls_args):
    print('=' * 60)
    print(f'{sys.argv[0]} running in {tooldir}')
    print(f'    rsync_bin={rsync_cmd}')
    if rsync_peer_cmd != rsync_cmd:
        print(f'    rsync_peer={rsync_peer_cmd}')
    print(f'    srcdir={srcdir}')
    print(f'    TLS_ARGS={tls_args}')
    print(f'    testuser={get_testuser()}')
    print(f'    os={subprocess.check_output(["uname", "-a"], text=True).strip()}')
    print(f'    preserve_scratch={"yes" if args.preserve_scratch else "no"}')
    if args.valgrind:
        print('    valgrind=enabled (logs in valgrind-logs/valgrind.*.log)')
    if args.parallel > 1:
        print(f'    parallel={args.parallel}')
    print(f'    daemon_transport={"tcp (loopback)" if args.use_tcp else "pipe (secure default)"}')
    print(f'    scratchbase={scratchbase}')

def build_test_environment(args, tooldir, srcdir, suitedir, scratchbase,
                           rsync_cmd, rsync_peer_cmd, tls_args, shconfig, setfacl_nodef):
    path = os.environ.get('PATH', '')
    if os.path.isdir('/usr/xpg4/bin'):
        path = '/usr/xpg4/bin:' + path
    pythonpath = suitedir
    if os.environ.get('PYTHONPATH'):
        pythonpath += os.pathsep + os.environ['PYTHONPATH']

    env = os.environ.copy()
    env.update({key: value for key, value in shconfig.items() if value})
    env.update({
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
        'HOME': scratchbase,
        'PYTHONPATH': pythonpath,
    })
    if args.use_tcp:
        env['RSYNC_TEST_USE_TCP'] = '1'
    else:
        env.pop('RSYNC_TEST_USE_TCP', None)
    if args.race_timeout is None:
        env.pop('race_timeout', None)
    else:
        env['race_timeout'] = str(args.race_timeout)
    env['setfacl_nodef'] = ' '.join(setfacl_nodef) if setfacl_nodef else 'true'
    if args.log_level > 8:
        env['RUNSHFLAGS'] = '-e -x'
    return env

def configure_profiles(args, suitedir, srcdir, all_tests, tests,
                       rsync_bin, rsync_bin2):
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
    tests, excluded = select_applicable_tests(tests, not_applicable)
    excluded_names = {name for group in excluded for name in group['tests']}
    invalid = sorted(set(xfail) & excluded_names)
    if invalid:
        raise ValueError(f'expected failures do not apply: {", ".join(invalid)}')
    effective_protocol = args.protocol
    transport = 'tcp' if args.use_tcp else 'pipe'
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
    requirements = {test_name(test): read_requirements(test) for test in all_tests}
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

def main():
    args = parse_args()
    if args.check_receipt:
        try:
            data = read_receipt(args.check_receipt)
            if args.rsync_bin and data['run'].get('binary_sha256') != file_digest(args.rsync_bin):
                raise ValueError('receipt identifies a different binary')
            if args.profiles is not None:
                expected = [Path(value.strip()).name for value in args.profiles.split(',') if value.strip()]
                expected = [value[:-5] if value.endswith('.json') else value for value in expected]
                if data['run']['profiles'] != expected:
                    raise ValueError('receipt identifies different profiles')
        except (OSError, ValueError) as error:
            sys.stderr.write(f'invalid test receipt: {error}\n')
            sys.exit(Exit.ERROR)
        sys.exit(data['summary']['exit_code'])
    apply_environment_defaults(args)
    tooldir, srcdir, suitedir, rsync_bin, rsync_bin2 = resolve_run_paths(args)
    try:
        all_tests = collect_tests(suitedir, [])
        tests = all_tests if not args.tests else collect_tests(suitedir, args.tests)
    except ValueError as error:
        sys.stderr.write(f'runtests.py: {error}\n')
        sys.exit(Exit.ERROR)
    if args.describe_tests:
        excluded = [item.strip() for item in args.exclude.split(',') if item.strip()]
        if excluded:
            tests = [path for path in tests
                     if not any(fnmatch.fnmatch(test_name(path), item)
                                for item in excluded)]
        print(json.dumps(describe_tests(tests), separators=(',', ':'), sort_keys=True))
        return

    scratchbase = os.path.join(os.environ.get('scratchbase', tooldir), 'testtmp')
    os.makedirs(scratchbase, exist_ok=True)
    os.environ['HOME'] = scratchbase

    shconfig = read_shconfig(os.path.join(tooldir, 'shconfig'))
    tls_args = get_tls_args(os.path.join(tooldir, 'config.h'))
    setfacl_nodef = find_setfacl_nodef(scratchbase)
    rsync_cmd = build_rsync_cmd(rsync_bin, args, scratchbase)
    rsync_peer_cmd = build_rsync_cmd(rsync_bin2, args, scratchbase)
    try:
        validate_run_paths(tooldir, srcdir, rsync_bin, rsync_bin2)
    except (OSError, ValueError) as error:
        sys.stderr.write(f'runtests.py: {error}\n')
        sys.exit(Exit.ERROR)

    excl = [e.strip() for e in args.exclude.split(',') if e.strip()]
    if excl:
        before = len(tests)
        tests = [t for t in tests
                 if not any(fnmatch.fnmatch(test_name(t), pat) for pat in excl)]
        if before != len(tests):
            print(f"Excluding {before - len(tests)} test(s) matching: "
                  f"{', '.join(excl)}")

    if args.daemon_tests_only:
        tests, dropped = select_daemon_tests(tests)
        print(f'Daemon-transport tests only: running {len(tests)}, skipping '
              f'{len(dropped)} test(s) that cannot observe the transport')

    try:
        (tests, profiles, profile_peer, effective_protocol,
         profile_unsupported, profile_xfail, transport,
         not_applicable) = configure_profiles(
            args, suitedir, srcdir, all_tests, tests, rsync_bin, rsync_bin2)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f'runtests.py: {error}\n')
        sys.exit(Exit.ERROR)

    if not tests:
        sys.stderr.write('runtests.py: no tests selected\n')
        sys.exit(Exit.ERROR)
    if rsync_bin != rsync_bin2:
        try:
            versions = [binary_identity(binary)[0] for binary in (rsync_bin, rsync_bin2)]
        except ValueError as error:
            sys.stderr.write(f'runtests.py: {error}\n')
            sys.exit(Exit.ERROR)
        if any(int(version.split('.')[0]) < 3 for version in versions):
            tls_args = ' '.join(argument for argument in tls_args.split() if argument != '-l')
    identity = {}
    if args.receipt:
        try:
            revision, dirty = source_identity(srcdir)
            identity = {'source_revision': revision, 'source_dirty': dirty, 'euid': os.geteuid(),
                        'binary_sha256': file_digest(rsync_bin), 'peer_sha256': file_digest(rsync_bin2)}
        except OSError as error:
            sys.stderr.write(f'runtests.py: cannot identify test inputs: {error}\n')
            sys.exit(Exit.ERROR)
    print_run_header(args, tooldir, srcdir, scratchbase, rsync_cmd, rsync_peer_cmd, tls_args)
    base_env = build_test_environment(
        args, tooldir, srcdir, suitedir, scratchbase,
        rsync_cmd, rsync_peer_cmd, tls_args, shconfig, setfacl_nodef,
    )

    not_applicable_count = len({name for group in not_applicable for name in group['tests']})
    metadata_by_test = {test_name(test): read_requirements(test) for test in tests}

    def timeout_for(name):
        metadata = metadata_by_test.get(name) or {}
        return max(args.timeout, 600) if metadata.get('cost') == 'expensive' else args.timeout

    def expected_outcome(name, unsupported=''):
        if name in profile_xfail:
            return f'xfail:{profile_xfail[name]}'
        metadata = metadata_by_test.get(name) or {}
        if unsupported in profile_unsupported and unsupported in metadata.get('features', ()):
            return f'unsupported:{unsupported}'
        return 'pass'

    def mismatch(name, actual, unsupported=''):
        verdict = verdict_of(actual, expected_outcome(name, unsupported))
        return verdict not in ('pass', 'xfail', 'unsupported')

    test_order = {test_name(test): index for index, test in enumerate(tests)}

    passed = 0
    failed = 0
    skipped = 0
    unsupported_count = 0
    xfailed = 0
    outcomes = {}
    unsupported_outcomes = {}
    durations = {}
    test_results = {}

    def process_result(result):
        nonlocal passed, failed, skipped, unsupported_count, xfailed
        with _print_lock:
            if result.output:
                print(result.output)
        scratchdir = os.path.join(scratchbase, result.name)
        outcome = result.outcome
        outcomes[result.name] = outcome
        test_results[result.name] = result
        unsupported_outcomes[result.name] = result.unsupported
        durations[result.name] = result.duration
        expected = expected_outcome(result.name, result.unsupported) if profiles else None
        verdict = verdict_of(outcome, expected)
        if verdict == 'pass':
            passed += 1
        elif verdict == 'unsupported':
            unsupported_count += 1
        elif verdict == 'skip':
            skipped += 1
        elif verdict == 'xfail':
            xfailed += 1
        else:
            failed += 1
        is_failure = (mismatch(result.name, outcome, result.unsupported) if profiles
                      else verdict not in ('pass', 'skip', 'unsupported'))
        if not is_failure and not args.preserve_scratch and os.path.isdir(scratchdir):
            subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        return is_failure

    run_started = time.monotonic()
    cancelled = threading.Event()

    def cancel(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        cancelled.set()

    previous = signal.signal(signal.SIGTERM, cancel)
    try:
        if args.parallel > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
                futures = {}
                for testscript in tests:
                    testbase = test_name(testscript)
                    scratchdir = os.path.join(scratchbase, testbase)
                    timeout = timeout_for(testbase)
                    f = executor.submit(
                        run_one_test, testscript, testbase, scratchdir,
                        base_env, timeout, srcdir, tooldir, setfacl_nodef,
                        args.always_log, cancelled,
                    )
                    futures[f] = testbase

                # Poll so worker-delivered signals reach the main-thread handler
                while futures:
                    done, _ = concurrent.futures.wait(
                        futures, timeout=0.25, return_when=concurrent.futures.FIRST_COMPLETED)
                    if not done:
                        continue
                    f = done.pop()
                    del futures[f]
                    result = f.result()
                    is_fail = process_result(result)
                    if cancelled.is_set() or is_fail and args.stop_on_fail:
                        for pending in futures:
                            pending.cancel()
                        break
        else:
            for testscript in tests:
                testbase = test_name(testscript)
                scratchdir = os.path.join(scratchbase, testbase)
                timeout = timeout_for(testbase)
                result = run_one_test(
                    testscript, testbase, scratchdir,
                    base_env, timeout, srcdir, tooldir, setfacl_nodef,
                    args.always_log, cancelled,
                )
                is_fail = process_result(result)
                if cancelled.is_set() or is_fail and args.stop_on_fail:
                    break
    finally:
        signal.signal(signal.SIGTERM, previous)

    run_wall = time.monotonic() - run_started

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

    print('-' * 60)
    print('----- overall results:')
    print(f'      {passed} passed')
    if failed > 0:
        print(f'      {failed} failed')
    if xfailed > 0:
        print(f'      {xfailed} xfailed (expected)')
    if skipped > 0:
        print(f'      {skipped} skipped')
    if unsupported_count > 0:
        print(f'      {unsupported_count} unsupported')
    if not_applicable_count > 0:
        print(f'      {not_applicable_count} N/A')
    if vg_errors > 0:
        print(f'      {vg_errors} valgrind error(s) found (see logs in {os.path.join(scratchbase, "valgrind-logs")})')

    if args.timing:
        print_timing_report(durations, outcomes, run_wall, args.parallel)

    def emit_receipt(exit_code, mismatches=()):
        if not args.receipt:
            return
        ordered = sorted(test_results, key=lambda name: test_order.get(name, 1 << 30))
        records = []
        verdicts = {}
        for name in ordered:
            expected = expected_outcome(name, unsupported_outcomes.get(name, '')) if profiles else None
            record = test_results[name].record(expected)
            if metadata_by_test.get(name):
                record['requirements'] = metadata_by_test[name]
            records.append(record)
            verdicts[record['verdict']] = verdicts.get(record['verdict'], 0) + 1
        data = {
            'schema': 2,
            'run': {
                **identity,
                'binary': rsync_cmd,
                'peer': rsync_peer_cmd,
                'peer_version': profile_peer,
                'profiles': [profile.name for profile in profiles],
                'protocol': effective_protocol,
                'transport': transport,
                'parallel': args.parallel,
                'valgrind': args.valgrind,
                'selected': list(test_order),
                'not_applicable': not_applicable,
            },
            'tests': records,
            'summary': {
                'passed': passed,
                'failed': failed,
                'skipped': skipped,
                'unsupported': unsupported_count,
                'xfailed': xfailed,
                'not_applicable': not_applicable_count,
                'not_applicable_reasons': {
                    group['capability']: len(group['tests']) for group in not_applicable
                },
                'verdicts': verdicts,
                'valgrind_errors': vg_errors,
                'duration_seconds': round(run_wall, 6),
                'exit_code': exit_code,
                'mismatches': list(mismatches),
            },
        }
        write_receipt(args.receipt, data)

    if profiles:
        mismatches = []
        expected_tests = {test_name(test) for test in tests}
        for name in sorted(expected_tests, key=lambda item: test_order.get(item, 1 << 30)):
            actual = outcomes.get(name, 'notrun')
            unsupported = unsupported_outcomes.get(name, '')
            if actual == 'notrun' or mismatch(name, actual, unsupported):
                expected = expected_outcome(name, unsupported)
                verdict = ('profile_error' if actual == 'notrun'
                           else verdict_of(actual, expected))
                mismatches.append((name, expected, actual, verdict))
        if mismatches:
            print('----- profile mismatches:')
            for name, expected, actual, verdict in mismatches:
                print(f'      {verdict.upper()} {name}: expected {expected}, got {actual}')
        print('-' * 60)
        exit_code = int(bool(mismatches or vg_errors))
        emit_receipt(exit_code, ({'name': name, 'expected': expected,
                                  'actual': actual, 'verdict': verdict}
                                 for name, expected, actual, verdict in mismatches))
        print(f'overall result is {exit_code}')
        sys.exit(exit_code)

    print('-' * 60)

    exit_code = int(bool(failed or vg_errors or len(test_results) != len(tests)))
    emit_receipt(exit_code)
    print(f'overall result is {exit_code}')
    sys.exit(exit_code)

if __name__ == '__main__':
    main()
