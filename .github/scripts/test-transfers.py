import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import interop_ci as ci
import interop_enrol as enrol
from interop_network import NetworkError

broker = runpy.run_path(str(Path(__file__).with_name('interop-broker.py')))
endpoint = runpy.run_path(str(Path(__file__).with_name('interop-endpoint.py')))
client = runpy.run_path(str(Path(__file__).with_name('interop-client.py')))
host = runpy.run_path(str(Path(__file__).with_name('interop-host.py')))
from harness.interoperability import PLATFORMS, seed, transfer_cases, update
from harness.interop_metadata import FEATURES, metadata_cases, supported
from harness.ssh import public_key


class Transfers(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = dict(run_id='123', run_attempt='2', source_sha='a' * 40)
        self.nodes = {role: dict(self.state, role=role, node_id='n' + str(index), address='100.64.0.' + str(index))
                      for index, role in enumerate(ci.ROLES, 1)}
        self.run = dict(id=123, run_attempt=2, path='.github/workflows/ci.yml', name='CI',
                        repository=dict(id=266877886), event='pull_request', head_sha='b' * 40,
                        head_repository=dict(id=999, full_name='contributor/rsync'),
                        referenced_workflows=[dict(path=ci.WORKFLOW + 'a' * 40, sha='a' * 40)])
        self.env = patch.dict(os.environ, dict(GITHUB_REPOSITORY_ID='266877886', GITHUB_EVENT_NAME='pull_request',
                                               GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', GH_TOKEN='fixture'), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        ci.source.cache_clear()
        ci.api_cache.clear()
        ci.bound = None
        self.addCleanup(ci.source.cache_clear)
        self.addCleanup(setattr, ci, 'bound', None)

    def receipts(self):
        cases = transfer_cases()
        result = {role: dict(platform=role, identity=dict(platform=role, source_sha=self.state['source_sha'],
                             metadata=dict(acl=FEATURES[role][0], xattrs=FEATURES[role][1])), passed=True,
                           commands=[list(case) for case in cases if (case[0] if case[2] == 'push' else case[1]) == role],
                           verified=[list(case) for case in cases if case[1] == role]) for role in ci.ROLES}
        for role, record in result.items():
            cases = metadata_cases()
            record.update(metadata_commands=[list(case) for case in cases if (case[0] if case[2] == 'push' else case[1]) == role],
                          metadata_updates=[list(case) for case in cases if (case[0] if case[2] == 'push' else case[1]) == role
                                            and case[-1] != 'backup' and supported(case)],
                          metadata_verified=[list(case) for case in cases if case[1] == role],
                          metadata_initial_verified=[list(case) for case in cases if case[1] == role],
                          metadata_restored=[list(case) for case in cases if case[0] == role and case[-1] == 'backup'])
        return result

    def test_complete_native_case_set(self):
        self.assertEqual(set(ci.ROLES), set(PLATFORMS))
        cases = transfer_cases()
        self.assertEqual(len(cases), 168)
        self.assertEqual(len({(a, b) for a, b, _, _ in cases if 'linux' not in (a, b)}), 30)
        broker['verify_cases'](self.receipts(), self.state['source_sha'])

    def test_missing_duplicate_foreign_and_misassigned_cases_fail(self):
        for role in ci.ROLES:
            for field in ('commands', 'verified'):
                for mutation in ('missing', 'duplicate', 'foreign', 'wrong role'):
                    values = self.receipts()
                    cases = values[role][field]
                    if mutation == 'missing':
                        cases.pop()
                    elif mutation == 'duplicate':
                        cases[0] = cases[1]
                    elif mutation == 'foreign':
                        cases[0] = ['unknown', *cases[0][1:]]
                    else:
                        other = next(value for value in ci.ROLES if value != role)
                        cases[0] = values[other][field][0]
                    with self.subTest(role=role, field=field, mutation=mutation), self.assertRaises(NetworkError):
                        broker['verify_cases'](values, self.state['source_sha'])
            values = self.receipts()
            del values[role]
            with self.subTest(missing=role), self.assertRaises(NetworkError):
                broker['verify_cases'](values, self.state['source_sha'])

    def test_metadata_evidence_cannot_be_omitted_or_substituted(self):
        for role in ci.ROLES:
            for field in ('metadata_commands', 'metadata_updates', 'metadata_initial_verified', 'metadata_verified', 'metadata_restored'):
                values = self.receipts()
                del values[role][field]
                with self.subTest(role=role, missing=field), self.assertRaises(NetworkError):
                    broker['verify_cases'](values, self.state['source_sha'])
                values = self.receipts()
                cases = values[role][field]
                if cases:
                    cases[0] = ['unknown', *cases[0][1:]]
                    with self.subTest(role=role, substituted=field), self.assertRaises(NetworkError):
                        broker['verify_cases'](values, self.state['source_sha'])
                    values = self.receipts()
                    values[role][field].append(values[role][field][0])
                    with self.subTest(role=role, duplicate=field), self.assertRaises(NetworkError):
                        broker['verify_cases'](values, self.state['source_sha'])
            values = self.receipts()
            values[role]['identity']['metadata']['xattrs'] = not FEATURES[role][1]
            with self.subTest(role=role, feature='xattrs'), self.assertRaises(NetworkError):
                broker['verify_cases'](values, self.state['source_sha'])
            values = self.receipts()
            values[role]['identity']['platform'] = 'host'
            with self.subTest(substitute=role), self.assertRaises(NetworkError):
                broker['verify_cases'](values, self.state['source_sha'])

    def test_merge_revision_not_head_is_tested(self):
        with patch.object(ci, 'api', return_value=self.run):
            self.assertEqual(ci.identity(), self.state)
            self.assertNotEqual(ci.identity()['source_sha'], self.run['head_sha'])

    def test_native_revision_errors_are_identified_without_command_output(self):
        failures = (FileNotFoundError('fixture-private-path'), subprocess.TimeoutExpired('git', 10),
                    subprocess.CalledProcessError(128, 'git', stderr=b'fixture-private-output'))
        for error in failures:
            with self.subTest(error=type(error).__name__), \
                    patch.dict(endpoint['serve'].__globals__, endpoint_identity=lambda *_: {}), \
                    patch.object(subprocess, 'check_output', side_effect=error), \
                    self.assertRaisesRegex(ValueError, '^Native endpoint revision check failed$'):
                endpoint['serve'](self.root, 'linux', self.root / 'rsync', self.root / 'rrsync',
                                  revision=self.state['source_sha'])
            self.assertEqual(json.loads((self.root / 'error.json').read_text()),
                             dict(error='Native endpoint revision check failed'))
            self.assertFalse((self.root / 'data').exists())

    def test_native_revision_must_match_the_candidate(self):
        with patch.dict(endpoint['serve'].__globals__, endpoint_identity=lambda *_: {}), \
                patch.object(subprocess, 'check_output', return_value='b' * 40 + '\n'), \
                self.assertRaisesRegex(ValueError, 'Native endpoint has a different candidate revision'):
            endpoint['serve'](self.root, 'linux', self.root / 'rsync', self.root / 'rrsync',
                              revision=self.state['source_sha'])
        self.assertFalse((self.root / 'data').exists())

    def test_fork_needs_no_oidc(self):
        with patch.object(ci, 'api', return_value=self.run):
            self.assertEqual(ci.identity(), self.state)
        self.assertNotIn('ACTIONS_ID_TOKEN_REQUEST_TOKEN', os.environ)

    def test_unknown_ci_or_missing_workflow_is_refused(self):
        for field, value in dict(path='.github/workflows/other.yml', run_attempt=3, status='completed',
                                 repository=dict(id=1), referenced_workflows=[]).items():
            ci.source.cache_clear()
            with self.subTest(field=field), patch.object(ci, 'api', return_value=dict(self.run, **{field: value})), self.assertRaises(NetworkError):
                ci.identity()
        with patch.dict(os.environ, GITHUB_EVENT_NAME='pull_request_target'), self.assertRaises(NetworkError):
            ci.identity()

    def test_recovery_keeps_the_original_ci_attempt(self):
        ci.bind(self.state)
        with patch.object(ci, 'api', side_effect=AssertionError('Recovery read a newer CI attempt')):
            self.assertEqual(ci.identity(), self.state)
        for key, value in dict(run_id='../1', run_attempt='0', source_sha='not a hash').items():
            with self.subTest(key=key), self.assertRaises(NetworkError):
                ci.bind(dict(self.state, **{key: value}))

    def test_stale_record_is_refused(self):
        for key, value in dict(run_id='124', run_attempt='3', source_sha='b' * 40).items():
            with self.subTest(key=key), self.assertRaises(NetworkError):
                ci.validate(dict(self.state, **{key: value}), self.state)

    def test_enrolment_is_private_and_bound_to_the_tested_revision(self):
        enrol.generate(self.root, 'linux', self.state)
        recipient = json.loads((self.root / 'public-key.json').read_text())
        record = enrol.seal(self.root, 'linux', recipient, 'key1', 'tskey-auth-fixture', self.state)
        self.assertNotIn('tskey-auth-fixture', json.dumps(record))
        enrol.decrypt(self.root, 'linux', record, self.state)
        self.assertEqual((self.root / 'enrol-key').read_text(), 'tskey-auth-fixture')
        enrol.forget(self.root)
        self.assertFalse((self.root / 'enrol-key').exists())
        with self.assertRaises(NetworkError):
            enrol.seal(self.root, 'linux', recipient, 'key1', 'tskey-auth-fixture', dict(self.state, run_attempt='3'))

    def test_bad_public_keys_are_refused(self):
        for value in ('', 'ssh-rsa AAAA', 'ssh-ed25519 AAAA', 'ssh-ed25519 AAAA\ncommand=evil'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_key(value)

    def test_case_names_cannot_escape_the_root(self):
        for case in (['../outside', 'linux', 'push', 'ssh'], ['linux', 'linux', 'push', 'ssh'], ['linux']):
            with self.subTest(case=case), self.assertRaises(ValueError):
                endpoint['name'](case)

    def test_native_verification_checks_initial_and_updated_data(self):
        root = self.root
        (root / 'data' / 'source').mkdir(parents=True)
        (root / 'data' / 'destination').mkdir()
        seed(root / 'reference')
        for case in transfer_cases():
            if case[0] == 'linux':
                seed(root / 'data' / 'source' / endpoint['name'](case))
            if case[1] == 'linux':
                seed(root / 'data' / 'destination' / endpoint['name'](case))
        with patch.dict(endpoint['verify'].__globals__, verify_metadata=lambda *_: []):
            result = endpoint['verify'](root, 'linux', 1)
            self.assertEqual(len(result['verified']), 24)
            with self.assertRaises(ValueError):
                endpoint['verify'](root, 'linux', 2)
            for case in transfer_cases():
                if case[1] == 'linux':
                    update(root / 'data' / 'destination' / endpoint['name'](case))
            self.assertEqual(len(endpoint['verify'](root, 'linux', 2)['verified']), 24)
            first = next(case for case in transfer_cases() if case[1] == 'linux')
            (root / 'data' / 'destination' / endpoint['name'](first) / 'space name').write_text('corrupt')
            with self.assertRaises(ValueError):
                endpoint['verify'](root, 'linux', 2)

    def test_client_packages_are_verified_before_execution(self):
        opener = type('Opener', (), {'open': lambda *_args, **_kwargs: io.BytesIO(b'not the expected package')})()
        with patch('urllib.request.build_opener', return_value=opener), self.assertRaises(NetworkError):
            client['download']('https://pkgs.tailscale.com/stable/example', self.root / 'package', '0' * 64)

    def test_missing_client_tool_is_identified_without_arguments(self):
        with patch.object(subprocess, 'run', side_effect=FileNotFoundError(2, 'not found', 'go')):
            with self.assertRaisesRegex(NetworkError, '^Required command not found: go$'):
                client['run'](['go', 'fixture-not-an-access-token'])


    def test_barriers_precede_copy_and_do_not_wait_for_their_own_upload(self):
        calls = []
        def native(root, role, phase):
            calls.append(phase)
            return {}
        def barrier(kind, **kwargs):
            calls.append(kind)
            return {}
        shared = dict(identity=lambda: self.state, call=native, records=barrier, write=lambda *args, **kwargs: None)
        for phase, expected in (('transfer1', ['admitted', 'transfer1']), ('transfer2', ['next', 'transfer2']),
                                ('next', ['transfer1', 'next'])):
            calls.clear()
            with self.subTest(phase=phase), patch.object(sys, 'argv', ['interop-host.py', phase, 'linux', '--root', str(self.root)]), \
                    patch.dict(host['main'].__globals__, shared):
                self.assertEqual(host['main'](), 0)
            self.assertEqual(calls, expected)

    def test_result_retains_both_metadata_verification_phases(self):
        record = self.receipts()['linux']
        initial = list(reversed(record['metadata_verified']))
        (self.root / 'ready.json').write_text(json.dumps(dict(identity=record['identity'], node_id='n1')))
        (self.root / 'transfer1.json').write_text(json.dumps(dict(metadata_commands=record['metadata_commands'])))
        (self.root / 'next.json').write_text(json.dumps(dict(metadata_verified=initial)))
        (self.root / 'transfer2.json').write_text(json.dumps(dict(commands=record['commands'],
            metadata_commands=record['metadata_updates'], metadata_restored=record['metadata_restored'])))
        publish = unittest.mock.Mock()
        final = dict(verified=record['verified'], metadata_verified=record['metadata_verified'])
        with patch.object(sys, 'argv', ['interop-host.py', 'verify', 'linux', '--root', str(self.root)]), \
                patch.dict(host['main'].__globals__, identity=lambda: self.state, records=lambda *args, **kwargs: {},
                           call=lambda *args: final, write=publish):
            self.assertEqual(host['main'](), 0)
        self.assertEqual(publish.call_args[0][1], 'result')
        self.assertEqual(publish.call_args[1]['metadata_initial_verified'], initial)
        self.assertEqual(publish.call_args[1]['metadata_verified'], record['metadata_verified'])
        self.assertEqual(publish.call_args[1]['metadata_commands'], record['metadata_commands'])
        self.assertEqual(publish.call_args[1]['metadata_updates'], record['metadata_updates'])

    def test_peer_failure_records_a_blocked_phase(self):
        environment = self.root / 'github-env'
        ci.bind(self.state)
        with patch.object(sys, 'argv', ['interop-host.py', 'transfer1', 'linux', '--root', str(self.root)]), \
                patch.dict(os.environ, GITHUB_ENV=str(environment)), \
                patch.dict(host['main'].__globals__, identity=lambda: self.state,
                           records=unittest.mock.Mock(side_effect=ci.PeerUnavailable('Unavailable endpoints: NetBSD')),
                           call=lambda *args: self.fail('A blocked transfer ran')):
            self.assertEqual(host['main'](), 0)
        self.assertEqual(environment.read_text(), 'INTEROP_BLOCKED=true\n')
        record = json.loads((self.root / 'blocked.json').read_text())
        self.assertEqual(record['role'], 'linux')
        self.assertEqual(record['phase'], 'transfer1')
        self.assertIs(record['passed'], False)
        self.assertFalse((self.root / 'transfer1.json').exists())

    def test_local_failures_are_not_blocked_results(self):
        environment = self.root / 'github-env'
        with patch.object(sys, 'argv', ['interop-host.py', 'transfer1', 'linux', '--root', str(self.root)]), \
                patch.dict(os.environ, GITHUB_ENV=str(environment)), \
                patch.dict(host['main'].__globals__, identity=lambda: self.state,
                           records=unittest.mock.Mock(side_effect=NetworkError('Invalid receipt'))), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(host['main'](), 1)
        self.assertFalse(environment.exists())
        self.assertFalse((self.root / 'blocked.json').exists())

    def test_result_rejects_blocked_failed_and_missing_outcomes(self):
        for role, job in (('netbsd', dict(result='success', outputs=dict(blocked='true'))),
                          ('cygwin', dict(result='failure', outputs={})),
                          ('linux', dict(result='success', outputs={}))):
            jobs = {name: dict(result='success', outputs=dict(blocked='false')) for name in ci.ROLES}
            jobs[role] = job
            with self.subTest(role=role), patch.dict(os.environ, INTEROP_JOBS=json.dumps(jobs)), \
                    patch.object(sys, 'argv', ['interop-host.py', 'result', 'all', '--root', str(self.root)]), \
                    patch.dict(host['main'].__globals__, identity=lambda: self.state,
                               records=lambda *args: self.fail('Incomplete endpoints reached receipt validation')), \
                    patch('sys.stderr', new_callable=io.StringIO) as stderr:
                self.assertEqual(host['main'](), 1)
            self.assertIn(role, stderr.getvalue())
        self.assertFalse((self.root / 'blocked.json').exists())

    def test_result_requires_transfers_and_verified_cleanup(self):
        jobs = {name: dict(result='success', outputs=dict(blocked='false')) for name in ci.ROLES}
        valid = dict(passed=True, cleaned=True, roles=list(ci.ROLES))
        for proof, expected in ((valid, 0), (dict(valid, passed=False), 1), (dict(valid, cleaned=False), 1),
                                (dict(valid, roles=list(ci.ROLES)[:-1]), 1)):
            with self.subTest(proof=proof), patch.dict(os.environ, INTEROP_JOBS=json.dumps(jobs)), \
                    patch.object(sys, 'argv', ['interop-host.py', 'result', 'all', '--root', str(self.root)]), \
                    patch.dict(host['main'].__globals__, identity=lambda: self.state,
                               records=lambda *args: self.receipts(), published=lambda *args: proof), \
                    patch('sys.stderr', new_callable=io.StringIO), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(host['main'](), expected)

    def test_controller_failure_still_fails_result(self):
        jobs = {name: dict(result='success', outputs=dict(blocked='false')) for name in ci.ROLES}
        with patch.dict(os.environ, INTEROP_JOBS=json.dumps(jobs)), \
                patch.object(sys, 'argv', ['interop-host.py', 'result', 'all', '--root', str(self.root)]), \
                patch.dict(host['main'].__globals__, identity=lambda: self.state, records=lambda *args: self.receipts(),
                           published=unittest.mock.Mock(side_effect=ci.PeerUnavailable('Network controller failed'))), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(host['main'](), 1)
        self.assertFalse((self.root / 'blocked.json').exists())

    def test_denial_has_healthy_controls_before_and_after(self):
        ci.bind(self.state)
        (self.root / 'ready.json').write_text(json.dumps(self.nodes['linux']))
        calls = []
        def connected(address, port):
            calls.append((address, port))
            return address == self.nodes['linux']['address']
        shared = dict(published=lambda *args: dict(nodes=self.nodes), connect=connected)
        with patch.dict(host['network'].__globals__, shared):
            host['network'](self.root, 'linux', 'quarantine')
        for port in (18731, 18732, 18733):
            self.assertEqual(calls.count((self.nodes['linux']['address'], port)), 2)
        for role, node in self.nodes.items():
            if role != 'linux':
                self.assertIn((node['address'], 18731), calls)
        self.assertTrue(json.loads((self.root / 'quarantine.json').read_text())['passed'])

    def test_a_dead_service_cannot_prove_network_denial(self):
        (self.root / 'ready.json').write_text(json.dumps(self.nodes['linux']))
        with patch.dict(host['network'].__globals__, published=lambda *args: dict(nodes=self.nodes), connect=lambda *args: False):
            with self.assertRaisesRegex(NetworkError, 'not listening'):
                host['network'](self.root, 'linux', 'quarantine')
        self.assertFalse((self.root / 'quarantine.json').exists())

    def test_client_checks_active_preferences_without_retaining_private_data(self):
        (self.root / 'client.json').write_text(json.dumps(dict(command=['tailscale'], windows=False)))
        prefs = dict(RouteAll=False, CorpDNS=False, Persist=dict(PrivateNodeKey='fixture-not-a-key'))
        responses = [subprocess.CompletedProcess([], 0, b'{}'), subprocess.CompletedProcess([], 0, b''),
                     subprocess.CompletedProcess([], 0, json.dumps(prefs).encode()),
                     subprocess.CompletedProcess([], 0, b'{"Self":{"ID":"n1"}}')]
        with patch.dict(client['connect'].__globals__, run=unittest.mock.Mock(side_effect=responses)), patch.object(subprocess, 'Popen'):
            self.assertEqual(client['connect'](self.root, self.root / 'enrol-key', 'fixture')['ID'], 'n1')
        self.assertNotIn('fixture-not-a-key', (self.root / 'client.json').read_text())
        for value in (True, None, 0, 'false'):
            broken = json.dumps(dict(prefs, CorpDNS=value)).encode()
            results = [subprocess.CompletedProcess([], 0, b'{}'), subprocess.CompletedProcess([], 0, b''),
                       subprocess.CompletedProcess([], 0, broken)]
            with self.subTest(value=value), patch.dict(client['connect'].__globals__, run=unittest.mock.Mock(side_effect=results)), patch.object(subprocess, 'Popen'):
                with self.assertRaisesRegex(NetworkError, 'still enabled'):
                    client['connect'](self.root, self.root / 'enrol-key', 'fixture')


if __name__ == '__main__':
    unittest.main()
