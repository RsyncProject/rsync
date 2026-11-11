#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from harness import config, runner
from harness import Exit, TestContext, requires, run
from harness.metadata import test_name
from harness.profile import parse_peer_banner
from harness.results import test_fail
from harness.rsync import rsync_command_binary

@requires(features={'remote-shell'}, transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    args = SimpleNamespace(log_level=9, timeout=300, use_tcp=False, race_timeout=None)
    with patch.dict('os.environ', {'RSYNC_TEST_USE_TCP': '1', 'race_timeout': '5'}):
        env = runner.build_test_environment(
            args, str(context.tools), str(context.repository),
            str(context.repository / 'testsuite'), str(context.scratch),
            '/tmp/rsync', '/tmp/rsync', '',
            {'RUNSHFLAGS': 'wrong', 'setfacl_nodef': 'wrong', 'RSYNC': 'wrong',
             'RSYNC_PEER': 'wrong', 'TLS_ARGS': 'wrong', 'srcdir': 'wrong'}, ['setfacl', '-k'])
    if 'RSYNC_TEST_USE_TCP' in env or 'race_timeout' in env:
        test_fail('disabled runner options leaked from the environment')
    if env['RUNSHFLAGS'] != '-e -x' or env['setfacl_nodef'] != 'setfacl -k':
        test_fail('runner settings were overridden by shconfig')
    if env['RSYNC'] != '/tmp/rsync' or env['RSYNC_PEER'] != '/tmp/rsync' \
            or env['TLS_ARGS'] or env['srcdir'] != str(context.repository):
        test_fail('shconfig changed the executables or source under test')

    blocked = context.scratch / 'private-path'
    blocked.mkdir()
    commands = context.scratch / 'acl-tools'
    commands.mkdir()
    command = commands / 'setfacl'
    command.write_text(f'#!{sys.executable}\nraise SystemExit(0)\n')
    command.chmod(0o755)
    blocked.chmod(0)
    try:
        with patch.dict(os.environ, {'PATH': str(blocked) + os.pathsep + str(commands)}):
            if config.find_setfacl_nodef(str(context.scratch)) != ['setfacl', '-k']:
                test_fail('an inaccessible PATH entry prevented ACL tool discovery')
    finally:
        blocked.chmod(0o755)
    with patch.object(runner.shutil, 'which', return_value=str(command)):
        with patch.object(runner.subprocess, 'run', side_effect=PermissionError('blocked executable')):
            try:
                config.find_setfacl_nodef(str(context.scratch))
            except PermissionError:
                pass
            else:
                test_fail('an execution failure was treated as a missing ACL tool')

    tests = config.collect_tests(str(context.repository / 'testsuite'), [])
    for names in ('', 'linux'):
        args = SimpleNamespace(profiles=names, protocol=None, use_tcp=False)
        with patch.dict('os.environ', {'RSYNC_TEST_UNSUPPORTED': 'fixture'}):
            configured = config.configure_profiles(
                args, str(context.repository / 'testsuite'), str(context.repository),
                tests, tests, '', '')
        if 'fixture' in configured[4]:
            test_fail('an environment override changed profile capabilities')

    n_a_profile = context.scratch / 'n-a.json'
    n_a_profile.write_text(json.dumps({'not_applicable': {'tcp': 'pipe transport'}}))
    tcp_test = next(path for path in tests if test_name(path) == 'daemon-access-hostname')
    ordinary_test = next(path for path in tests if test_name(path) == 'runner')
    args = SimpleNamespace(profiles=str(n_a_profile), protocol=None, use_tcp=False)
    configured = config.configure_profiles(
        args, str(context.repository / 'testsuite'), str(context.repository),
        tests, [tcp_test, ordinary_test], '', '')
    if configured[0] != [ordinary_test] or configured[6] != [{
            'capability': 'tcp', 'reasons': ['pipe transport'],
            'tests': ['daemon-access-hostname']}]:
        test_fail(f'N/A test selection is wrong: {configured[0]}, {configured[6]}')
    for uid in (0, 1000, 65534):
        for names in ('root', 'nonroot', 'root,nonroot', 'nonroot,root', 'linux'):
            args = SimpleNamespace(profiles=names, protocol=None, use_tcp=False)
            valid = names == 'linux' or names == ('root' if uid == 0 else 'nonroot')
            with patch.object(runner.os, 'geteuid', return_value=uid):
                try:
                    configured = config.configure_profiles(args, str(context.repository / 'testsuite'),
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
                    if uid != 0 and names == 'nonroot' and not {'chown-fake', 'devices-fake'} <= {
                            test_name(path) for path in configured[0]}:
                        test_fail('non-root profile dropped fake-super metadata coverage')
    try:
        config.collect_tests(str(context.repository / 'testsuite'), ['missing-test'])
    except ValueError:
        pass
    else:
        test_fail('an unmatched explicit test selector was accepted')
    daemon, independent = config.select_daemon_tests(tests)
    daemon = {test_name(path) for path in daemon}
    independent = {test_name(path) for path in independent}
    if not {'daemon-auth', 'idn', 'protocol-refusals', 'inplace-checksum-bounds', 'daemon-access-hostname'} <= daemon:
        test_fail('daemon test selection dropped coverage')
    if not {'filter-merge-content-echo', 'runner'} <= independent:
        test_fail('daemon test selection retained transport-independent tests')

    daemon_call = "from harness import metadata\nmetadata(transports={'tcp'})\nstart_test_daemon(None, 1)\n"
    target = context.scratch / 'daemon-target.py'
    alias = context.scratch / 'daemon-alias.py'
    option = context.scratch / 'daemon-option.py'
    target.write_text(daemon_call)
    alias.write_text(target.name)
    option.write_text("from harness import metadata\nmetadata(transports={'tcp'})\nargs = ['--" + "daemon']\n")
    selected, _ = config.select_daemon_tests([str(alias), str(option)])
    if selected != [str(alias), str(option)]:
        test_fail('daemon test selection missed an alias or option')

    pipe_only = context.scratch / 'pipe-only.py'
    tcp_only = context.scratch / 'tcp-only.py'
    pipe_only.write_text("from harness import metadata\nmetadata(transports={'pipe'})\n" + daemon_call)
    tcp_only.write_text("from harness import metadata\nmetadata(transports={'tcp'}, tags={'daemon'})\n")
    unclassified = context.scratch / 'unclassified.py'
    unclassified.write_text("from harness import metadata\nmetadata()\n")
    selected, dropped = config.select_daemon_tests([str(pipe_only), str(tcp_only)])
    if selected != [str(tcp_only)] or dropped != [str(pipe_only)]:
        test_fail('daemon test selection ignored transport metadata')
    for use_tcp, expected, excluded in (
            (False, [str(pipe_only)], ['tcp-only']),
            (True, [str(tcp_only)], ['pipe-only'])):
        args = SimpleNamespace(profiles='linux', protocol=None, use_tcp=use_tcp)
        configured = config.configure_profiles(
            args, str(context.repository / 'testsuite'), str(context.repository),
            [*tests, str(pipe_only), str(tcp_only)], [str(pipe_only), str(tcp_only)], '', '')
        if configured[0] != expected or configured[6][-1] != {
                'capability': 'transport',
                'reasons': [f"not applicable to {'tcp' if use_tcp else 'pipe'} transport"],
                'tests': excluded}:
            test_fail('active transport did not filter declared test transports')
    try:
        config.select_transport_tests([str(unclassified)], 'pipe')
    except ValueError:
        pass
    else:
        test_fail('test without a declared transport was accepted')

    valgrind = SimpleNamespace(valgrind=True, valgrind_opts='', protocol=None)
    command = runner.build_rsync_cmd('/tmp/rsync', valgrind, str(context.scratch / 'valgrind'))
    suppression = context.scratch / 'valgrind' / 'valgrind-logs' / 'valgrind.supp'
    if not suppression.is_file() or f'--suppressions={suppression}' not in command:
        test_fail('the Valgrind suppression file is unavailable to dropped processes')

    receipt = context.scratch / 'run.json'
    nested_env = os.environ.copy()
    nested_env['scratchbase'] = str(context.scratch / 'nested')

    def invoke(args):
        receipt.unlink(missing_ok=True)
        return subprocess.run(args, env=nested_env, capture_output=True, text=True)

    binary = rsync_command_binary()
    peer_profile = context.repository / 'testsuite' / 'profiles' / 'peer-3.0.0.json'
    daemon_access = next(path for path in tests if test_name(path) == 'daemon-access')
    cli_transfers = next(path for path in tests if test_name(path) == 'cli-transfers')
    identities = {'archived': ('3.0.0', 30), 'current': ('3.5.2', 33)}
    for archived_role, primary, secondary, expected in (
            ('primary', 'archived', 'current', {'daemon-access'}),
            ('secondary', 'current', 'archived', {'daemon-access', 'cli-transfers'})):
        args = SimpleNamespace(profiles=str(peer_profile), protocol=None, use_tcp=False,
                               archived_role=archived_role)
        with patch.object(config, 'binary_identity', side_effect=lambda path: identities[path]):
            configured = config.configure_profiles(
                args, str(context.repository / 'testsuite'), str(context.repository), tests,
                [daemon_access, cli_transfers], primary, secondary)
        if {test_name(path) for path in configured[0]} != expected:
            test_fail(f'{archived_role} role selected the wrong version-mix tests')

    wrong_peer = context.scratch / 'different-peer'
    wrong_peer.write_text('different executable\n')
    self_args = SimpleNamespace(profiles='self-peer', protocol=None, use_tcp=False)
    try:
        config.configure_profiles(self_args, str(context.repository / 'testsuite'), str(context.repository),
                                  tests, [ordinary_test], binary, str(wrong_peer))
    except ValueError:
        pass
    else:
        test_fail('the self-peer profile accepted a different executable')
    archive = context.scratch / 'archive'
    directory = archive / 'old_versions'
    directory.mkdir(parents=True)
    archived = directory / 'rsync_3.2.7'
    executed = context.scratch / 'archive-executed'
    archived.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(executed)!r}).touch()\n'
                        'print("rsync  version 3.2.7  protocol version 31")\n')
    archived.chmod(0o755)
    record = {'source': 'a' * 40, 'protocol': 31, 'sha256': runner.file_digest(archived),
              'recipe': 'b' * 64, 'compiler': 'test compiler', 'target': 'test target', 'libc': 'test libc'}
    (directory / 'binaries.json').write_text(json.dumps({archived.name: record}))
    archived.write_text(archived.read_text() + '# changed\n')
    try:
        config.validate_run_paths(str(context.tools), str(archive), binary, str(archived))
        config.binary_identity(str(archived))
    except ValueError:
        pass
    else:
        test_fail('a changed archived peer was accepted')
    if executed.exists():
        test_fail('an archived peer ran before its identity was checked')
    record['sha256'] = runner.file_digest(archived)
    (directory / 'binaries.json').write_text(json.dumps({archived.name: record}))
    with patch.object(runner.subprocess, 'check_output', return_value='b' * 40):
        try:
            config.validate_run_paths(str(context.tools), str(archive), binary, str(archived))
            config.binary_identity(str(archived))
        except ValueError:
            pass
        else:
            test_fail('an archived peer with the wrong source was accepted')
    if executed.exists():
        test_fail('an archived peer ran before its source was checked')
    with patch.object(runner.subprocess, 'check_output', return_value='a' * 40):
        config.validate_run_paths(str(context.tools), str(archive), binary, str(archived))
    if not executed.is_file():
        test_fail('a qualified archived peer was not checked')
    executed.unlink()
    record['protocol'] = 32
    (directory / 'binaries.json').write_text(json.dumps({archived.name: record}))
    with patch.object(runner.subprocess, 'check_output', return_value='a' * 40):
        try:
            config.validate_run_paths(str(context.tools), str(archive), binary, str(archived))
        except ValueError:
            pass
        else:
            test_fail('an archived peer with the wrong protocol was accepted')
    executed.unlink()
    record['protocol'] = 31
    (directory / 'binaries.json').write_text(json.dumps({archived.name: record}))
    saved = context.scratch / 'saved-peer'
    archived.rename(saved)
    archived.symlink_to(saved)
    for primary, peer in ((binary, str(archived)), (str(archived), binary)):
        with patch.object(runner.subprocess, 'check_output', return_value='a' * 40):
            try:
                config.validate_run_paths(str(context.tools), str(archive), primary, peer)
                config.binary_identity(str(archived))
            except ValueError:
                pass
            else:
                test_fail('an archived symlink was accepted')
    if executed.exists():
        test_fail('an archived symlink bypassed identity checks')
    banner = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout
    peer, protocol = parse_peer_banner(banner)
    profile = context.scratch / f'peer-{peer}.json'
    command = [sys.executable, str(context.repository / 'testsuite' / 'runtests.py'), 'cli-transfers',
               f'--rsync-bin={binary}', f'--rsync-bin2={binary}', f'--tooldir={context.tools}',
               f'--srcdir={context.repository}', f'--profiles={profile}', f'--receipt={receipt}']
    role = 'root' if os.geteuid() == 0 else 'nonroot'
    for names in ('root,nonroot', 'nonroot' if role == 'root' else 'root'):
        result = invoke(command + [f'--profiles={names}'])
        if result.returncode != Exit.ERROR or 'profile' not in result.stderr:
            test_fail(f'an invalid privilege profile was accepted: {names}')
        if receipt.exists():
            test_fail('an invalid privilege profile produced a test receipt')
    result = invoke(command + [f'--profiles={role}'])
    if result.returncode:
        test_fail(f'{role} run failed: {result.stdout}{result.stderr}')
    data = json.loads(receipt.read_text())
    if data['run']['euid'] != os.geteuid() or data['run']['profiles'] != [role]:
        test_fail('the receipt identifies the wrong privilege profile')
    profile.write_text(json.dumps({'version': peer, 'protocol': protocol + 1}))
    mismatch = invoke(command + ['--archived-role=secondary'])
    if mismatch.returncode != Exit.ERROR or 'profile protocol' not in mismatch.stderr:
        test_fail('a mismatched peer protocol was accepted')

    profile.write_text(json.dumps({'version': peer, 'protocol': protocol}))
    result = invoke(command + ['--archived-role=secondary'])
    if result.returncode:
        test_fail(f'nested runner failed: {result.stdout}{result.stderr}')
    data = json.loads(receipt.read_text())
    if (data['summary']['exit_code'] or data['run']['selected'] != ['cli-transfers']
            or data['run']['archived_version'] != peer
            or data['run']['archived_role'] != 'secondary'
            or data['run']['protocol'] != protocol
            or data['run']['profiles'] != [f'peer-{peer}']
            or data['summary']['verdicts'] != {'pass': 1}):
        test_fail('receipt summary or selection is wrong')
    if (data['tests'][0]['name'] != 'cli-transfers' or data['tests'][0]['outcome'] != 'pass'
            or data['tests'][0]['expected'] != 'pass'
            or list(context.scratch.glob(f'.{receipt.name}.*.tmp'))):
        test_fail('receipt result is wrong')

    fixture = context.scratch / 'self-profile'
    testdir = fixture / 'testsuite' / 'tests'
    testdir.mkdir(parents=True)
    profile = fixture / 'profile.json'
    receipt = fixture / 'receipt.json'
    command = [sys.executable, str(context.repository / 'testsuite' / 'runtests.py'), 'conditional',
               f'--rsync-bin={binary}', f'--tooldir={context.tools}', f'--srcdir={fixture}',
               f'--profiles={profile}', f'--receipt={receipt}']
    unsupported = ('import os\nfrom pathlib import Path\n'
                   'Path(os.environ["scratchdir"], "unsupported").write_text("fixture\\n")\n'
                   'raise SystemExit(77)\n')
    allowed = {'unsupported': {'fixture': 'test-fixture'}}
    cases = (
        ({}, True, 1, 'profile_error', 'pass'),
        (allowed, True, 0, 'unsupported', 'unsupported:fixture'),
        (allowed, False, 1, 'profile_error', 'pass'),
    )
    for policy, declared, exit_code, verdict, expected in cases:
        prefix = ("from harness import metadata\n"
                  "metadata(features={'fixture'}, transports={'pipe'})\n") if declared else ''
        (testdir / 'conditional.py').write_text(prefix + unsupported)
        profile.write_text(json.dumps(policy))
        result = invoke(command)
        if result.returncode != exit_code:
            test_fail(f'wrong unsupported exit: {result.stdout}{result.stderr}')
        record = json.loads(receipt.read_text())['tests'][0]
        if (record['outcome'], record['verdict'], record['expected']) != ('unsupported', verdict, expected):
            test_fail(f'wrong unsupported receipt: {record}')

    ordinary = testdir / 'ordinary.py'
    ordinary.write_text('raise SystemExit(0)\n')
    (testdir / 'conditional.py').write_text(
        "from harness import metadata\n"
        "metadata(features={'fixture'}, transports={'pipe'})\nraise SystemExit(1)\n")
    profile.write_text(json.dumps({'not_applicable': {'fixture': 'fixture partition'}}))
    selection = command.copy()
    selection[2:3] = ['conditional', 'ordinary']
    result = invoke(selection)
    data = json.loads(receipt.read_text())
    if (result.returncode or data['schema'] != 3 or data['run']['selected'] != ['ordinary']
            or data['run']['not_applicable'] != [{
                'capability': 'fixture', 'reasons': ['fixture partition'],
                'tests': ['conditional']}]
            or data['summary']['not_applicable'] != 1
            or data['summary']['not_applicable_reasons'] != {'fixture': 1}
            or [record['name'] for record in data['tests']] != ['ordinary']):
        test_fail(f'N/A receipt is wrong: {data}')
    (testdir / 'conditional.py').write_text('raise SystemExit(1)\n')
    result = invoke(selection)
    data = json.loads(receipt.read_text())
    if (result.returncode != Exit.FAIL
            or data['run']['selected'] != ['conditional', 'ordinary']
            or data['run']['not_applicable']
            or data['summary']['not_applicable']
            or [record['name'] for record in data['tests']] != ['conditional', 'ordinary']):
        test_fail(f'applicable test was not restored to the run: {data}')
    ordinary.unlink()

    profile.write_text('{}')
    cases = (
        ("raise RuntimeError('fixture failed')\n", []),
        ('import rsync_missing_fixture_module\n', []),
        ('def broken(\n', []),
        ('def broken(\n', ['--use-tcp', '--daemon-tests-only']),
        ('raise KeyboardInterrupt\n', []),
        ('import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n', []),
        ('import signal\nsignal.pause()\n', ['--timeout=1']),
    )
    for source, options in cases:
        (testdir / 'conditional.py').write_text(source)
        result = invoke(command + options)
        if not receipt.is_file():
            test_fail(f'a terminated test disappeared without a receipt: {result.stderr}')
        record = json.loads(receipt.read_text())['tests'][0]
        if result.returncode != Exit.FAIL or record['outcome'] != 'error' or record['verdict'] != 'error':
            test_fail(f'a terminated test was not reported as an error: {record}')

    (testdir / 'conditional.py').write_text('assert False\n')
    result = invoke(command)
    record = json.loads(receipt.read_text())['tests'][0]
    if result.returncode != Exit.FAIL or record['outcome'] != 'fail' or record['verdict'] != 'fail':
        test_fail('an assertion failure was accepted')

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
    result = invoke(command)
    if result.returncode:
        test_fail(f'script identity or sibling imports changed: {result.stdout}{result.stderr}')
    sibling.unlink()

    (testdir / 'conditional.py').write_text('import sys\nassert sys.flags.no_site\n')
    result = invoke([sys.executable, '-S', *command[1:]])
    if result.returncode:
        test_fail(f'test execution enabled site packages: {result.stdout}{result.stderr}')

    (testdir / 'conditional.py').write_text(
        "import os\nfrom harness import metadata\n"
        "metadata(cost='expensive', transports={'pipe'})\n"
        "assert os.environ['TESTRUN_TIMEOUT'] == '600'\n")
    result = invoke(command + ['--timeout=3'])
    if result.returncode:
        test_fail(f'the allotted timeout was not passed to the test: {result.stdout}{result.stderr}')

    qualified = testdir / 'qualified.py'
    qualified.write_text("from harness import metadata\nmetadata(transports={'pipe'}, tags={'version-mix'})\n")
    (testdir / 'conditional.py').write_text('def broken(\n')
    peer_profile = fixture / f'peer-{peer}.json'
    peer_profile.write_text(json.dumps({'version': peer, 'protocol': protocol}))
    selection = command.copy()
    selection[2:3] = ['conditional', 'qualified']
    result = invoke(selection + [f'--profiles={peer_profile}', '--archived-role=secondary'])
    if result.returncode != Exit.ERROR or 'transport metadata' not in result.stderr or receipt.exists():
        test_fail('peer selection accepted an unclassified test')
    qualified.unlink()

    (testdir / 'conditional.py').write_text(
        'import os\nfrom pathlib import Path\nPath(os.environ["scratchdir"], "passed").touch()\n')
    waiting = testdir / 'waiting.py'
    waiting.write_text('import signal\nsignal.pause()\n')
    entry = fixture / 'testsuite' / 'runtests.py'
    entry.write_text(
        'import os, signal\nfrom pathlib import Path\nfrom harness import runner\n'
        'original = runner.subprocess.run\n'
        'def cleanup(args, *values, **options):\n'
        '    if args[0] == "rm" and (Path(args[-1]) / "passed").is_file():\n'
        '        os.kill(os.getpid(), signal.SIGTERM)\n'
        '    return original(args, *values, **options)\n'
        'runner.subprocess.run = cleanup\nrunner.main()\n')
    selection = command.copy()
    selection[1:3] = [str(entry), 'conditional', 'waiting']
    with patch.dict(nested_env, {'preserve_scratch': 'no'}):
        result = invoke(selection + ['--profiles=', '-j2'])
    data = json.loads(receipt.read_text())
    if (result.returncode != Exit.FAIL or data['summary']['exit_code'] != Exit.FAIL
            or data['run']['selected'] != ['conditional', 'waiting']
            or len(data['tests']) != 1 or data['tests'][0]['outcome'] != 'pass'):
        test_fail('cancelled unprofiled run reported success or lost its partial results')
    waiting.unlink()

    legacy = fixture / 'testsuite' / 'forgotten_test.py'
    legacy.write_text('raise SystemExit(0)\n')
    result = invoke(command)
    if result.returncode != Exit.ERROR or 'forgotten_test.py' not in result.stderr:
        test_fail('a test outside the discovery directory was ignored')
    result = invoke(command + ['--describe-tests'])
    if result.returncode != Exit.ERROR or 'forgotten_test.py' not in result.stderr:
        test_fail('test descriptions ignored a misplaced test')
    legacy.unlink()
    (testdir / 'conditional.py').unlink()
    result = invoke(command)
    if result.returncode != Exit.ERROR or 'no tests found' not in result.stderr:
        test_fail('an empty discovery directory was accepted')

    for index in range(256):
        (testdir / f'failure-{index}.py').write_text('raise SystemExit(1)\n')
    failures = command[:2] + ['failure-*', '-j8'] + command[3:]
    for policy in (f'--profiles={profile}', '--profiles='):
        result = invoke(failures + [policy])
        summary = json.loads(receipt.read_text())['summary']
        if result.returncode != Exit.FAIL or summary['exit_code'] != Exit.FAIL or summary['failed'] != 256:
            test_fail(f'256 failures returned {result.returncode}; '
                      f'receipt exit={summary["exit_code"]}, failed={summary["failed"]}')

if __name__ == '__main__':
    run(test)
