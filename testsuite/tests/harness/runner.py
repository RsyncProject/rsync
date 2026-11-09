#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from harness import runner
from harness import Exit, TestContext, parse_peer_banner, requires, run, test_name
from harness.rsync import rsync_command_binary, test_fail

@requires(features={'remote-shell'}, transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    args = SimpleNamespace(log_level=9, timeout=300, use_tcp=False, race_timeout=None)
    with patch.dict('os.environ', {'RSYNC_TEST_USE_TCP': '1', 'race_timeout': '5'}):
        env = runner.build_test_environment(
            args, str(context.tools), str(context.repository),
            str(context.repository / 'testsuite'), str(context.scratch),
            '/tmp/rsync', '/tmp/rsync', '',
            {'RUNSHFLAGS': 'wrong', 'setfacl_nodef': 'wrong'}, ['setfacl', '-k'])
    if 'RSYNC_TEST_USE_TCP' in env or 'race_timeout' in env:
        test_fail('disabled runner options leaked from the environment')
    if env['RUNSHFLAGS'] != '-e -x' or env['setfacl_nodef'] != 'setfacl -k':
        test_fail('runner settings were overridden by shconfig')

    blocked = context.scratch / 'private-path'
    blocked.mkdir()
    commands = context.scratch / 'acl-tools'
    commands.mkdir()
    command = commands / 'setfacl'
    command.write_text('#!/bin/sh\nexit 0\n')
    command.chmod(0o755)
    blocked.chmod(0)
    try:
        with patch.dict(os.environ, {'PATH': str(blocked) + os.pathsep + str(commands)}):
            if runner.find_setfacl_nodef(str(context.scratch)) != ['setfacl', '-k']:
                test_fail('an inaccessible PATH entry prevented ACL tool discovery')
    finally:
        blocked.chmod(0o755)
    with patch.object(runner.shutil, 'which', return_value=str(command)):
        with patch.object(runner.subprocess, 'run', side_effect=PermissionError('blocked executable')):
            try:
                runner.find_setfacl_nodef(str(context.scratch))
            except PermissionError:
                pass
            else:
                test_fail('an execution failure was treated as a missing ACL tool')

    tests = runner.collect_tests(str(context.repository / 'testsuite'), [])
    for names in ('', 'linux'):
        args = SimpleNamespace(profiles=names, protocol=None, use_tcp=False)
        with patch.dict('os.environ', {'RSYNC_TEST_UNSUPPORTED': 'fixture'}):
            configured = runner.configure_profiles(
                args, str(context.repository / 'testsuite'), str(context.repository),
                tests, tests, '', '')
        if 'fixture' in configured[4]:
            test_fail('an environment override changed profile capabilities')

    n_a_profile = context.scratch / 'n-a.json'
    n_a_profile.write_text(json.dumps({'not_applicable': {'tcp': 'pipe transport'}}))
    tcp_test = next(path for path in tests if test_name(path) == 'daemon-access-hostname')
    ordinary_test = next(path for path in tests if test_name(path) == 'runner')
    args = SimpleNamespace(profiles=str(n_a_profile), protocol=None, use_tcp=False)
    configured = runner.configure_profiles(
        args, str(context.repository / 'testsuite'), str(context.repository),
        tests, [tcp_test, ordinary_test], '', '')
    if configured[0] != [ordinary_test] or configured[7] != [{
            'capability': 'tcp', 'reasons': ['pipe transport'],
            'tests': ['daemon-access-hostname']}]:
        test_fail(f'N/A test selection is wrong: {configured[0]}, {configured[7]}')
    n_a_profile.write_text(json.dumps({
        'not_applicable': {'tcp': 'pipe transport'},
        'xfail': {'daemon-access-hostname': 'issue-1'},
    }))
    try:
        runner.configure_profiles(
            args, str(context.repository / 'testsuite'), str(context.repository),
            tests, [tcp_test, ordinary_test], '', '')
    except ValueError:
        pass
    else:
        test_fail('an expected failure was attached to an N/A test')
    for uid in (0, 1000, 65534):
        for names in ('root', 'nonroot', 'root,nonroot', 'nonroot,root', 'linux'):
            args = SimpleNamespace(profiles=names, protocol=None, use_tcp=False)
            valid = names == 'linux' or names == ('root' if uid == 0 else 'nonroot')
            with patch.object(runner.os, 'geteuid', return_value=uid):
                try:
                    configured = runner.configure_profiles(args, str(context.repository / 'testsuite'),
                                                           str(context.repository), tests, tests, '', '')
                except ValueError:
                    if valid:
                        test_fail(f'{names} rejected effective UID {uid}')
                else:
                    if not valid:
                        test_fail(f'{names} accepted effective UID {uid}')
                    if uid == 0 and names == 'root' and not any(
                            test_name(path) == 'search-only-held-dirfd' for path in configured[0]):
                        test_fail('root profile dropped search-only coverage which can use setpriv')
    try:
        runner.collect_tests(str(context.repository / 'testsuite'), ['missing-test'])
    except ValueError:
        pass
    else:
        test_fail('an unmatched explicit test selector was accepted')
    daemon, independent = runner.select_daemon_tests(tests)
    daemon = {test_name(path) for path in daemon}
    independent = {test_name(path) for path in independent}
    if not {'daemon-auth', 'idn', 'protocol-refusals', 'inplace-checksum-bounds', 'daemon-access-hostname'} <= daemon:
        test_fail('daemon test selection dropped coverage')
    if not {'filter-merge-content-echo', 'runner'} <= independent:
        test_fail('daemon test selection retained transport-independent tests')

    daemon_call = 'start_test_daemon(None, 1)\n'
    target = context.scratch / 'daemon-target.py'
    alias = context.scratch / 'daemon-alias.py'
    option = context.scratch / 'daemon-option.py'
    target.write_text(daemon_call)
    alias.write_text(target.name)
    option.write_text("args = ['--" + "daemon']\n")
    selected, _ = runner.select_daemon_tests([str(alias), str(option)])
    if selected != [str(alias), str(option)]:
        test_fail('daemon test selection missed an alias or option')

    pipe_only = context.scratch / 'pipe-only.py'
    tcp_only = context.scratch / 'tcp-only.py'
    pipe_only.write_text("from harness import metadata\nmetadata(transports={'pipe'})\n" + daemon_call)
    tcp_only.write_text("from harness import metadata\nmetadata(transports={'tcp'}, tags={'daemon'})\n")
    selected, dropped = runner.select_daemon_tests([str(pipe_only), str(tcp_only)])
    if selected != [str(tcp_only)] or dropped != [str(pipe_only)]:
        test_fail('daemon test selection ignored transport metadata')

    valgrind = SimpleNamespace(valgrind=True, valgrind_opts='', protocol=None)
    command = runner.build_rsync_cmd('/tmp/rsync', valgrind, str(context.scratch / 'valgrind'))
    suppression = context.scratch / 'valgrind' / 'valgrind-logs' / 'valgrind.supp'
    if not suppression.is_file() or f'--suppressions={suppression}' not in command:
        test_fail('the Valgrind suppression file is unavailable to dropped processes')

    receipt = context.scratch / 'run.json'
    nested_env = os.environ.copy()
    nested_env['scratchbase'] = str(context.scratch / 'nested')
    binary = rsync_command_binary()
    banner = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout
    peer, protocol = parse_peer_banner(banner)
    profile = context.scratch / f'peer-{peer}.json'
    command = [sys.executable, str(context.repository / 'testsuite' / 'runtests.py'), 'cli-transfers',
               f'--rsync-bin={binary}', f'--rsync-bin2={binary}', f'--tooldir={context.tools}',
               f'--srcdir={context.repository}', f'--profiles={profile}', f'--receipt={receipt}']
    role = 'root' if os.geteuid() == 0 else 'nonroot'
    for names in ('root,nonroot', 'nonroot' if role == 'root' else 'root'):
        result = subprocess.run(command + [f'--profiles={names}'], env=nested_env,
                                capture_output=True, text=True)
        if result.returncode != Exit.ERROR or 'profile' not in result.stderr:
            test_fail(f'an invalid privilege profile was accepted: {names}')
        if receipt.exists():
            test_fail('an invalid privilege profile produced a test receipt')
    result = subprocess.run(command + [f'--profiles={role}'], env=nested_env,
                            capture_output=True, text=True)
    if result.returncode:
        test_fail(f'{role} run failed: {result.stdout}{result.stderr}')
    data = json.loads(receipt.read_text())
    if data['run']['euid'] != os.geteuid() or data['run']['profiles'] != [role]:
        test_fail('the receipt identifies the wrong privilege profile')
    profile.write_text(json.dumps({'peer': peer, 'protocol': protocol + 1}))
    mismatch = subprocess.run(command, env=nested_env, capture_output=True, text=True)
    if mismatch.returncode != Exit.ERROR or 'profile protocol' not in mismatch.stderr:
        test_fail('a mismatched peer protocol was accepted')

    profile.write_text(json.dumps({'peer': peer, 'protocol': protocol,
                                   'xfail': {'alt-dest': 'test-fixture'}}))
    result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
    if result.returncode:
        test_fail(f'nested runner failed: {result.stdout}{result.stderr}')
    data = json.loads(receipt.read_text())
    if (data['summary']['exit_code'] or data['run']['selected'] != ['cli-transfers']
            or data['run']['peer_version'] != peer or data['run']['protocol'] != protocol
            or data['run']['profiles'] != [f'peer-{peer}']
            or data['summary']['verdicts'] != {'pass': 1}):
        test_fail('receipt summary or selection is wrong')
    if (data['tests'][0]['name'] != 'cli-transfers' or data['tests'][0]['outcome'] != 'pass'
            or data['tests'][0]['expected'] != 'pass'
            or list(context.scratch.glob(f'.{receipt.name}.*.tmp'))):
        test_fail('receipt result is wrong')

    fixture = context.scratch / 'self-xfail'
    testdir = fixture / 'testsuite' / 'tests'
    testdir.mkdir(parents=True)
    profile = fixture / 'profile.json'
    receipt = fixture / 'receipt.json'
    command = [sys.executable, str(context.repository / 'testsuite' / 'runtests.py'), 'conditional',
               f'--rsync-bin={binary}', f'--tooldir={context.tools}', f'--srcdir={fixture}',
               f'--profiles={profile}', f'--receipt={receipt}']
    xfail = {'xfail': {'conditional': 'issue-1'}}
    cases = (
        (78, {}, 1, 'xfail', 'profile_error', 'pass'),
        (78, xfail, 0, 'xfail', 'xfail', 'xfail:issue-1'),
        (1, xfail, 0, 'fail', 'xfail', 'xfail:issue-1'),
        (0, xfail, 1, 'pass', 'unexpected_pass', 'xfail:issue-1'),
        (2, xfail, 1, 'error', 'profile_error', 'xfail:issue-1'),
        (3, xfail, 1, 'error', 'profile_error', 'xfail:issue-1'),
        (256, {}, 1, 'error', 'error', 'pass'),
        (77, xfail, 1, 'skip', 'profile_error', 'xfail:issue-1'),
    )
    for code, policy, exit_code, outcome, verdict, expected in cases:
        (testdir / 'conditional.py').write_text(f'raise SystemExit({code})\n')
        profile.write_text(json.dumps(policy))
        result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
        if result.returncode != exit_code:
            test_fail(f'wrong runner exit for {code}: {result.stdout}{result.stderr}')
        data = json.loads(receipt.read_text())
        record = data['tests'][0]
        if (record['outcome'], record['verdict'], record['expected']) != (outcome, verdict, expected):
            test_fail(f'wrong receipt for {code}: {record}')
        counts = data['summary']
        if (counts['failed'] != exit_code or counts['xfailed'] != (verdict == 'xfail')
                or counts['passed'] != (verdict == 'pass')):
            test_fail(f'wrong summary for {code}: {counts}')

    (testdir / 'conditional.py').write_text('raise SystemExit(78)\n')
    result = subprocess.run(command + ['--profiles='], env=nested_env,
                            capture_output=True, text=True)
    record = json.loads(receipt.read_text())['tests'][0]
    if (result.returncode != 1 or record['outcome'] != 'xfail'
            or record['verdict'] != 'profile_error' or 'expected' in record):
        test_fail('an unprofiled xfail was accepted')

    unsupported = ('import os\nfrom pathlib import Path\n'
                   'Path(os.environ["scratchdir"], "unsupported").write_text("fixture\\n")\n'
                   'raise SystemExit(77)\n')
    allowed = {'unsupported': {'fixture': 'test-fixture'}}
    cases = (
        ({}, True, 1, 'profile_error', 'pass'),
        (allowed, True, 0, 'unsupported', 'unsupported:fixture'),
        (allowed, False, 1, 'profile_error', 'pass'),
        ({**allowed, **xfail}, True, 1, 'profile_error', 'xfail:issue-1'),
    )
    for policy, declared, exit_code, verdict, expected in cases:
        prefix = "from harness import metadata\nmetadata(features={'fixture'})\n" if declared else ''
        (testdir / 'conditional.py').write_text(prefix + unsupported)
        profile.write_text(json.dumps(policy))
        result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
        if result.returncode != exit_code:
            test_fail(f'wrong unsupported exit: {result.stdout}{result.stderr}')
        record = json.loads(receipt.read_text())['tests'][0]
        if (record['outcome'], record['verdict'], record['expected']) != ('unsupported', verdict, expected):
            test_fail(f'wrong unsupported receipt: {record}')

    ordinary = testdir / 'ordinary.py'
    ordinary.write_text('raise SystemExit(0)\n')
    (testdir / 'conditional.py').write_text(
        "from harness import metadata\nmetadata(features={'fixture'})\nraise SystemExit(1)\n")
    profile.write_text(json.dumps({'not_applicable': {'fixture': 'fixture partition'}}))
    selection = command.copy()
    selection[2:3] = ['conditional', 'ordinary']
    result = subprocess.run(selection, env=nested_env, capture_output=True, text=True)
    data = json.loads(receipt.read_text())
    if (result.returncode or data['schema'] != 2 or data['run']['selected'] != ['ordinary']
            or data['run']['not_applicable'] != [{
                'capability': 'fixture', 'reasons': ['fixture partition'],
                'tests': ['conditional']}]
            or data['summary']['not_applicable'] != 1
            or data['summary']['not_applicable_reasons'] != {'fixture': 1}
            or [record['name'] for record in data['tests']] != ['ordinary']):
        test_fail(f'N/A receipt is wrong: {data}')
    (testdir / 'conditional.py').write_text('raise SystemExit(1)\n')
    result = subprocess.run(selection, env=nested_env, capture_output=True, text=True)
    data = json.loads(receipt.read_text())
    if (result.returncode != Exit.FAIL
            or data['run']['selected'] != ['conditional', 'ordinary']
            or data['run']['not_applicable']
            or data['summary']['not_applicable']
            or [record['name'] for record in data['tests']] != ['conditional', 'ordinary']):
        test_fail(f'applicable test was not restored to the run: {data}')
    ordinary.unlink()

    profile.write_text(json.dumps(xfail))
    cases = (
        ("raise RuntimeError('fixture failed')\n", []),
        ('import rsync_missing_fixture_module\n', []),
        ('def broken(\n', []),
        ('raise KeyboardInterrupt\n', []),
        ('import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n', []),
        ('import signal\nsignal.pause()\n', ['--timeout=1']),
    )
    for source, options in cases:
        (testdir / 'conditional.py').write_text(source)
        result = subprocess.run(command + options, env=nested_env,
                                capture_output=True, text=True)
        record = json.loads(receipt.read_text())['tests'][0]
        if result.returncode != Exit.FAIL or record['outcome'] != 'error' or record['verdict'] != 'profile_error':
            test_fail(f'a terminated test was accepted as an expected failure: {record}')

    (testdir / 'conditional.py').write_text('assert False\n')
    result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
    record = json.loads(receipt.read_text())['tests'][0]
    if result.returncode or record['outcome'] != 'fail' or record['verdict'] != 'xfail':
        test_fail('an assertion failure was not retained as a test failure')

    profile.write_text('{}')
    sibling = testdir / 'sibling.py'
    sibling.write_text('value = 42\n')
    (testdir / 'conditional.py').write_text(
        'import subprocess, sys\nfrom pathlib import Path\nfrom sibling import value\n'
        'assert value == 42\nassert sys.argv[0] == __file__\n'
        'assert sys.path[0] == str(Path(__file__).parent)\n'
        'if len(sys.argv) == 1:\n'
        '    subprocess.run([sys.executable, __file__, "child"], check=True)\n'
        'else:\n    assert sys.argv[1:] == ["child"]\n')
    result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
    if result.returncode:
        test_fail(f'script identity or sibling imports changed: {result.stdout}{result.stderr}')
    sibling.unlink()

    (testdir / 'conditional.py').write_text('import sys\nassert sys.flags.no_site\n')
    result = subprocess.run([sys.executable, '-S', *command[1:]], env=nested_env,
                            capture_output=True, text=True)
    if result.returncode:
        test_fail(f'test execution enabled site packages: {result.stdout}{result.stderr}')

    (testdir / 'conditional.py').write_text(
        "import os\nfrom harness import metadata\nmetadata(cost='expensive')\n"
        "assert os.environ['TESTRUN_TIMEOUT'] == '600'\n")
    result = subprocess.run(command + ['--timeout=3'], env=nested_env,
                            capture_output=True, text=True)
    if result.returncode:
        test_fail(f'the allotted timeout was not passed to the test: {result.stdout}{result.stderr}')

    legacy = fixture / 'testsuite' / 'forgotten_test.py'
    legacy.write_text('raise SystemExit(0)\n')
    result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
    if result.returncode != Exit.ERROR or 'forgotten_test.py' not in result.stderr:
        test_fail('a test outside the discovery directory was ignored')
    result = subprocess.run(command + ['--describe-tests'], env=nested_env,
                            capture_output=True, text=True)
    if result.returncode != Exit.ERROR or 'forgotten_test.py' not in result.stderr:
        test_fail('test descriptions ignored a misplaced test')
    legacy.unlink()
    (testdir / 'conditional.py').unlink()
    result = subprocess.run(command, env=nested_env, capture_output=True, text=True)
    if result.returncode != Exit.ERROR or 'no tests found' not in result.stderr:
        test_fail('an empty discovery directory was accepted')

    for index in range(256):
        (testdir / f'failure-{index}.py').write_text('raise SystemExit(1)\n')
    failures = command[:2] + ['failure-*', '-j8'] + command[3:]
    for policy in (f'--profiles={profile}', '--profiles='):
        result = subprocess.run(failures + [policy], env=nested_env,
                                capture_output=True, text=True)
        summary = json.loads(receipt.read_text())['summary']
        if result.returncode != Exit.FAIL or summary['exit_code'] != Exit.FAIL or summary['failed'] != 256:
            test_fail(f'256 failures returned {result.returncode}; '
                      f'receipt exit={summary["exit_code"]}, failed={summary["failed"]}')

if __name__ == '__main__':
    run(test)
