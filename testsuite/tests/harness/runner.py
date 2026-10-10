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

    tests = runner.collect_tests(str(context.repository / 'testsuite'), [])
    try:
        runner.collect_tests(str(context.repository / 'testsuite'), ['missing-test'])
    except ValueError:
        pass
    else:
        test_fail('an unmatched explicit test selector was accepted')
    daemon, independent = runner.select_daemon_tests(tests)
    daemon = {test_name(path) for path in daemon}
    independent = {test_name(path) for path in independent}
    if {'daemon-auth', 'idn', 'protocol-refusals'} > daemon:
        test_fail('daemon test selection dropped coverage')
    if {'filter-merge-content-echo', 'runner'} > independent:
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
    command = [sys.executable, str(context.repository / 'testsuite' / 'runtests.py'), 'smoke',
               f'--rsync-bin={binary}', f'--rsync-bin2={binary}', f'--tooldir={context.tools}',
               f'--srcdir={context.repository}', f'--profiles={profile}', f'--receipt={receipt}']
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
    if (data['summary']['exit_code'] or data['run']['selected'] != ['smoke']
            or data['run']['peer_version'] != peer or data['run']['protocol'] != protocol
            or data['run']['profiles'] != [f'peer-{peer}']
            or data['summary']['verdicts'] != {'pass': 1}):
        test_fail('receipt summary or selection is wrong')
    if (data['tests'][0]['name'] != 'smoke' or data['tests'][0]['outcome'] != 'pass'
            or data['tests'][0]['expected'] != 'pass'
            or list(context.scratch.glob(f'.{receipt.name}.*.tmp'))):
        test_fail('receipt result is wrong')

if __name__ == '__main__':
    run(test)
