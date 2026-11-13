#!/usr/bin/env python3

# Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
# Copyright (C) 2003-2022 Wayne Davison
# Copyright (C) 2026 Andrew Tridgell
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version
# 2 as published by the Free Software Foundation.

import concurrent.futures
import fnmatch
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from . import config
from .metadata import placeholder_target, read_requirements, test_name
from .process import file_digest
from .results import Exit, TestResult, read_receipt, verdict_of, write_receipt

_SUITE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
        supp = os.path.join(_SUITE_DIR, 'fixtures', 'valgrind.supp')
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

def source_identity(srcdir):
    if not (Path(srcdir) / '.git').exists():
        return None, None
    command = ['git', '--no-optional-locks', '--git-dir=' + str(Path(srcdir).resolve() / '.git'),
               '--work-tree=' + str(Path(srcdir).resolve()), '-C', str(srcdir)]
    try:
        revision = subprocess.run(command + ['rev-parse', '--verify', 'HEAD'],
                                  capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        status = subprocess.run(command + ['status', '--porcelain'],
                                capture_output=True, text=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('cannot identify the source checkout') from error
    return revision, status.splitlines()

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

    show_log = always_log or result not in (Exit.PASS, Exit.SKIP)
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
    else:
        label = 'FAIL' if result == Exit.FAIL else 'ERROR'
        output_parts.append(f'{label}    {testbase}')

    return TestResult(testbase, result, '\n'.join(output_parts), skipped_reason,
                      unsupported, time.monotonic() - started)

_print_lock = threading.Lock()

def print_run_header(args, tooldir, srcdir, scratchbase, rsync_cmd, rsync_peer_cmd, tls_args):
    print('=' * 60)
    print(f'{sys.argv[0]} running in {tooldir}')
    print(f'    primary={rsync_cmd}')
    if rsync_peer_cmd != rsync_cmd:
        print(f'    secondary={rsync_peer_cmd}')
        print(f'    archived_role={args.archived_role}')
    print(f'    srcdir={srcdir}')
    print(f'    TLS_ARGS={tls_args}')
    print(f'    testuser={config.get_testuser()}')
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

def main():
    args = config.parse_args()
    if args.check_receipt:
        try:
            data = read_receipt(args.check_receipt)
            if args.rsync_bin:
                if data['run'].get('primary_sha256') != file_digest(args.rsync_bin):
                    raise ValueError('receipt identifies a different primary binary')
                if data['run'].get('primary_version') != config.binary_identity(args.rsync_bin)[0]:
                    raise ValueError('receipt identifies a different primary version')
            if args.rsync_bin2:
                if data['run'].get('secondary_sha256') != file_digest(args.rsync_bin2):
                    raise ValueError('receipt identifies a different secondary binary')
                if data['run'].get('secondary_version') != config.binary_identity(args.rsync_bin2)[0]:
                    raise ValueError('receipt identifies a different secondary version')
            if args.archived_role is not None and data['run'].get('archived_role') != args.archived_role:
                raise ValueError('receipt identifies a different archived role')
            if args.profiles is not None:
                expected = [Path(value.strip()).name for value in args.profiles.split(',') if value.strip()]
                expected = [value[:-5] if value.endswith('.json') else value for value in expected]
                if data['run']['profiles'] != expected:
                    raise ValueError('receipt identifies different profiles')
        except (OSError, ValueError) as error:
            sys.stderr.write(f'invalid test receipt: {error}\n')
            sys.exit(Exit.ERROR)
        sys.exit(data['summary']['exit_code'])
    config.apply_environment_defaults(args)
    tooldir, srcdir, suitedir, rsync_bin, rsync_bin2 = config.resolve_run_paths(args)
    try:
        all_tests = config.collect_tests(suitedir, [])
        tests = all_tests if not args.tests else config.collect_tests(suitedir, args.tests)
    except ValueError as error:
        sys.stderr.write(f'runtests.py: {error}\n')
        sys.exit(Exit.ERROR)
    if args.describe_tests:
        excluded = [item.strip() for item in args.exclude.split(',') if item.strip()]
        if excluded:
            tests = [path for path in tests
                     if not any(fnmatch.fnmatch(test_name(path), item)
                                for item in excluded)]
        print(json.dumps(config.describe_tests(tests), separators=(',', ':'), sort_keys=True))
        return

    scratchbase = os.path.join(os.environ.get('scratchbase', tooldir), 'testtmp')
    os.makedirs(scratchbase, exist_ok=True)
    os.environ['HOME'] = scratchbase

    shconfig = config.read_shconfig(os.path.join(tooldir, 'shconfig'))
    tls_args = config.get_tls_args(os.path.join(tooldir, 'config.h'))
    setfacl_nodef = config.find_setfacl_nodef(scratchbase)
    rsync_cmd = build_rsync_cmd(rsync_bin, args, scratchbase)
    rsync_peer_cmd = build_rsync_cmd(rsync_bin2, args, scratchbase)
    try:
        config.validate_run_paths(tooldir, srcdir, rsync_bin, rsync_bin2)
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
        tests, dropped = config.select_daemon_tests(tests)
        print(f'Daemon-transport tests only: running {len(tests)}, skipping '
              f'{len(dropped)} test(s) that cannot observe the transport')

    try:
        (tests, profiles, profile_version, effective_protocol,
         profile_unsupported, transport,
         not_applicable) = config.configure_profiles(
            args, suitedir, srcdir, all_tests, tests, rsync_bin, rsync_bin2)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f'runtests.py: {error}\n')
        sys.exit(Exit.ERROR)

    if not tests:
        sys.stderr.write('runtests.py: no tests selected\n')
        sys.exit(Exit.ERROR)
    try:
        primary_version, _ = config.binary_identity(rsync_bin)
        secondary_version = (primary_version if rsync_bin == rsync_bin2
                             else config.binary_identity(rsync_bin2)[0])
    except ValueError as error:
        sys.stderr.write(f'runtests.py: {error}\n')
        sys.exit(Exit.ERROR)
    if any(int(version.split('.')[0]) < 3 for version in (primary_version, secondary_version)):
        tls_args = ' '.join(argument for argument in tls_args.split() if argument != '-l')
    identity = {}
    if args.receipt:
        try:
            revision, dirty = source_identity(srcdir)
            identity = {'source_revision': revision, 'source_dirty': None if dirty is None else bool(dirty),
                        'source_changes': dirty, 'euid': os.geteuid(),
                        'primary_sha256': file_digest(rsync_bin),
                        'secondary_sha256': file_digest(rsync_bin2),
                        'primary_version': primary_version,
                        'secondary_version': secondary_version}
        except (OSError, ValueError) as error:
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
        metadata = metadata_by_test.get(name) or {}
        if unsupported in profile_unsupported and unsupported in metadata.get('features', ()):
            return f'unsupported:{unsupported}'
        return 'pass'

    def mismatch(name, actual, unsupported=''):
        verdict = verdict_of(actual, expected_outcome(name, unsupported))
        return verdict not in ('pass', 'unsupported')

    test_order = {test_name(test): index for index, test in enumerate(tests)}

    passed = 0
    failed = 0
    skipped = 0
    unsupported_count = 0
    outcomes = {}
    unsupported_outcomes = {}
    durations = {}
    test_results = {}

    def process_result(result):
        nonlocal passed, failed, skipped, unsupported_count
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
            'schema': 3,
            'run': {
                **identity,
                'primary': rsync_cmd,
                'secondary': rsync_peer_cmd,
                'archived_version': profile_version,
                'archived_role': args.archived_role,
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
