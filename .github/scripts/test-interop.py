import copy
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

import interop_ci as ci
import interop_enrol as enrol
import interop_network as network
from interop_policy import PORTS, QUARANTINE, ROLES, update_policy

broker = runpy.run_path(str(Path(__file__).with_name('interop-broker.py')))


class Network(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = dict(run_id='123', run_attempt='2', source_sha='a' * 40)
        self.event = dict(action='in_progress', repository=dict(id=266877886, owner=dict(id=166180282)),
                          workflow=dict(path='.github/workflows/ci.yml'),
                          workflow_run=dict(id=123, run_attempt=2, head_sha='b' * 40, name='CI', event='pull_request'))
        (self.root / 'event.json').write_text(json.dumps(self.event))
        self.env = dict(GITHUB_REPOSITORY_ID='266877886', GITHUB_EVENT_NAME='workflow_run',
                        GITHUB_REF='refs/heads/master', GITHUB_RUN_ID='456', GITHUB_RUN_ATTEMPT='1',
                        GITHUB_WORKFLOW_SHA='c' * 40, GITHUB_EVENT_PATH=str(self.root / 'event.json'), GH_TOKEN='fixture')
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        ci.bind(self.state)
        self.addCleanup(setattr, ci, 'bound', None)
        self.devices, self.nodes = [], {}
        for index, role in enumerate(ROLES, 1):
            address = '100.64.0.' + str(index)
            self.devices.append(dict(nodeId='n' + str(index), id=str(index), hostname='ci-123-2-' + role,
                                     addresses=[address], authorized=True, tags=[QUARANTINE]))
            self.nodes[role] = dict(self.state, role=role, node_id='n' + str(index), address=address, key_id=role,
                                    oidc_available=False, identity=dict(platform=role, source_sha=self.state['source_sha']))
        broker['save'](self.root, 'keys', keys={role: role for role in ROLES})

    def test_trusted_events_and_owner(self):
        network.workflow_event()
        for field, value in dict(GITHUB_EVENT_NAME='pull_request', GITHUB_REF='refs/pull/1/merge',
                                 GITHUB_REPOSITORY_ID='1').items():
            with self.subTest(field=field), patch.dict(os.environ, {field: value}), self.assertRaises(network.NetworkError):
                network.workflow_event()

    def test_unselected_ci_has_no_network_side_effects(self):
        output = self.root / 'output'
        def absent():
            raise ci.NotSelected('Not selected')
        with patch.object(sys, 'argv', ['interop-broker.py', 'metadata', '--root', str(self.root)]), \
                patch.dict(os.environ, GITHUB_OUTPUT=str(output)), \
                patch.dict(broker['main'].__globals__, identity=absent,
                           Tailnet=lambda: self.fail('Unselected CI obtained network authority')):
            self.assertEqual(broker['main'](), 0)
        self.assertEqual(output.read_text(), 'enabled=false\n')
        self.assertFalse((self.root / 'context.json').exists())

    def test_recovery_uses_workflow_path_not_the_run_title(self):
        event = copy.deepcopy(self.event)
        event.update(action='completed', workflow=dict(path='.github/workflows/interoperability.yml'))
        event['workflow_run'].update(name='arbitrary title', event='workflow_run')
        (self.root / 'event.json').write_text(json.dumps(event))
        network.workflow_event()
        event['workflow_run']['event'] = 'pull_request'
        (self.root / 'event.json').write_text(json.dumps(event))
        with self.assertRaisesRegex(network.NetworkError, 'Unsupported cleanup event'):
            network.workflow_event()

    def test_ci_identity_uses_the_merge_commit_and_accepts_forks(self):
        ci.bound = None
        ci.source.cache_clear()
        self.addCleanup(ci.source.cache_clear)
        run = dict(id=123, run_attempt=2, path='.github/workflows/ci.yml', name='CI', event='pull_request',
                   repository=dict(id=266877886), head_repository=dict(id=999), head_sha='b' * 40,
                   display_title='testsuite, ci: rework tests', created_at='2026-11-14T00:00:00Z',
                   pull_requests=[dict(number=1, base=dict(sha='c' * 40, repo=dict(id=266877886)),
                                       head=dict(sha='d' * 40, repo=dict(id=999)))],
                   referenced_workflows=[dict(path=ci.WORKFLOW + 'a' * 40, sha='a' * 40)])
        request = dict(number=1, base=dict(sha='c' * 40, repo=dict(id=266877886)),
                       head=dict(sha='d' * 40, repo=dict(id=999)))
        def production(path, array=False):
            if path == '/actions/runs/123':
                return run
            if path == '/pulls/1':
                return request
            if path == '/pulls/1/files?per_page=100&page=1':
                self.assertTrue(array)
                return [dict(status='modified', filename='receiver.c')]
            self.fail('Unexpected API request: ' + path)
        with patch.object(ci, 'api', side_effect=production):
            self.assertEqual(ci.identity(), self.state)
        ci.source.cache_clear()
        docs = dict(run, referenced_workflows=[])
        def documentation(path, array=False):
            if path == '/actions/runs/123':
                return docs
            if path == '/pulls/1':
                return request
            if path == '/pulls/1/files?per_page=100&page=1':
                self.assertTrue(array)
                return [dict(status='modified', filename='README.md')]
            self.fail('Unexpected API request: ' + path)
        with patch.object(ci, 'api', side_effect=documentation), \
                patch.object(time, 'sleep', side_effect=AssertionError('Unselected CI waited')), \
                self.assertRaises(ci.NotSelected):
            ci.source()
        ci.source.cache_clear()
        without_reference = dict(run, event='push', pull_requests=[], referenced_workflows=[])
        with patch.object(ci, 'api', return_value=without_reference), self.assertRaises(ci.NotSelected):
            ci.source()
        ci.source.cache_clear()
        with patch.object(ci, 'api', return_value=dict(without_reference, display_title='')), self.assertRaises(network.NetworkError):
            ci.source()

    def test_controller_uses_trusted_change_selection(self):
        run = dict(event='pull_request', pull_requests=[dict(
            number=1, base=dict(sha='a' * 40, repo=dict(id=266877886)),
            head=dict(sha='b' * 40, repo=dict(id=999)))])
        request = dict(number=1, base=dict(sha='a' * 40, repo=dict(id=266877886)),
                       head=dict(sha='b' * 40, repo=dict(id=999)))
        def files(items, current=request):
            def response(path, array=False):
                if path == '/pulls/1':
                    return current
                if path == '/pulls/1/files?per_page=100&page=1':
                    self.assertTrue(array)
                    return items
                self.fail('Unexpected API request: ' + path)
            return response
        with patch.object(ci, 'api', side_effect=files([dict(status='modified', filename='README.md')])):
            self.assertFalse(ci.selected(run))
        with patch.object(ci, 'api', side_effect=files([dict(status='modified', filename='receiver.c')])):
            self.assertTrue(ci.selected(run))
        renamed = [dict(status='renamed', filename='README.md', previous_filename='receiver.c')]
        with patch.object(ci, 'api', side_effect=files(renamed)):
            self.assertTrue(ci.selected(run))
        stale = dict(request, head=dict(sha='c' * 40, repo=dict(id=999)))
        with patch.object(ci, 'api', side_effect=files([], stale)):
            self.assertTrue(ci.selected(run))
        malformed = [dict(status='renamed', filename='README.md')]
        with patch.object(ci, 'api', side_effect=files(malformed)):
            self.assertTrue(ci.selected(run))

    def test_api_preserves_the_expected_json_shape(self):
        ci.api_cache.clear()
        self.addCleanup(ci.api_cache.clear)
        expected = [dict(status='modified', filename='README.md')]
        with patch.object(ci, 'fetch', return_value=(json.dumps(expected).encode(), {})):
            self.assertEqual(ci.api('/pulls/1/files', array=True), expected)
        with patch.object(ci, 'fetch', return_value=(b'[]', {})), self.assertRaisesRegex(
                network.NetworkError, 'JSON object'):
            ci.api('/actions/runs/1')

    def test_controller_discovery_requires_trusted_code_and_exact_attempt(self):
        origin = dict(self.state, head_sha='b' * 40, display_title='testsuite, ci: rework tests',
                      created_at='2026-11-14T00:00:00Z')
        metadata = dict(self.state, control_run_id='456', control_attempt='1', control_sha='c' * 40)
        titles = ('Network controller: testsuite, ci: rework tests', 'Network CI 123 attempt 2')
        for title in titles:
            run = dict(id=456, run_attempt=1, path='.github/workflows/interoperability.yml', head_branch='master',
                       event='workflow_run', display_title=title, repository=dict(id=266877886),
                       head_sha='c' * 40, status='in_progress', created_at='2026-11-14T00:00:01Z')
            with self.subTest(title=title), patch.object(ci, 'source', return_value=origin), \
                    patch.object(ci, 'api', side_effect=[dict(total_count=1, workflow_runs=[run]),
                        dict(total_count=1, artifacts=[dict(name='interoperability-context-1', expired=False)])]), \
                    patch.object(ci, 'download_records', return_value={None: (metadata, b'{}')}):
                self.assertEqual(ci.controller(), '456')
        for field, value in dict(head_branch='untrusted', event='pull_request', repository=dict(id=1)).items():
            with self.subTest(field=field), patch.object(ci, 'source', return_value=origin), \
                    patch.object(ci, 'api', return_value=dict(
                        total_count=1, workflow_runs=[dict(run, **{field: value})])), self.assertRaises(network.NetworkError):
                ci.controller()

    def test_first_controller_context_survives_a_later_duplicate(self):
        origin = dict(self.state, head_sha='b' * 40, display_title='testsuite, ci: rework tests',
                      created_at='2026-11-14T00:00:00Z')
        run = dict(id=456, run_attempt=1, path='.github/workflows/interoperability.yml', head_branch='master',
                   event='workflow_run', display_title='Network controller: testsuite, ci: rework tests',
                   repository=dict(id=266877886),
                   head_sha='c' * 40, status='completed', conclusion='success',
                   created_at='2026-11-14T00:00:01Z')
        duplicate = dict(run, id=457, conclusion='failure')
        metadata = dict(self.state, control_run_id='456', control_attempt='1', control_sha='c' * 40)
        with patch.object(ci, 'source', return_value=origin), patch.object(ci, 'api', side_effect=[
                dict(total_count=2, workflow_runs=[duplicate, run]),
                dict(total_count=1, artifacts=[dict(name='interoperability-context-1', expired=False)])]), \
                patch.object(ci, 'download_records', return_value={None: (metadata, b'{}')}):
            self.assertEqual(ci.controller(), '456')

    def test_controller_listing_is_paged_and_bounded_to_the_ci_run(self):
        origin = dict(self.state, head_sha='b' * 40, display_title='current',
                      created_at='2026-11-14T00:00:20Z')
        unrelated = [dict(id=500 + index, created_at='2026-11-14T00:00:30Z',
                          display_title='other') for index in range(20)]
        current = dict(id=456, run_attempt=1, path='.github/workflows/interoperability.yml',
                       head_branch='master', event='workflow_run', display_title='Network controller: current',
                       repository=dict(id=266877886), head_sha='c' * 40, status='in_progress',
                       created_at='2026-11-14T00:00:21Z')
        older = dict(unrelated[0], id=455, created_at='2026-11-14T00:00:19Z')
        artifact = dict(total_count=1, artifacts=[dict(name='interoperability-context-1', expired=False)])
        metadata = dict(self.state, control_run_id='456', control_attempt='1', control_sha='c' * 40)
        with patch.object(ci, 'source', return_value=origin), patch.object(ci, 'api', side_effect=[
                dict(total_count=22, workflow_runs=unrelated),
                dict(total_count=22, workflow_runs=[current, older]), artifact]) as api, \
                patch.object(ci, 'download_records', return_value={None: (metadata, b'{}')}):
            self.assertEqual(ci.controller(), '456')
        self.assertIn('per_page=20&page=1', api.call_args_list[0][0][0])
        self.assertIn('per_page=20&page=2', api.call_args_list[1][0][0])

    def test_controller_title_collision_uses_the_context_identity(self):
        origin = dict(self.state, head_sha='b' * 40, display_title='same title',
                      created_at='2026-11-14T00:00:00Z')
        run = dict(run_attempt=1, path='.github/workflows/interoperability.yml', head_branch='master',
                   event='workflow_run', display_title='Network controller: same title',
                   repository=dict(id=266877886), head_sha='c' * 40, status='in_progress',
                   created_at='2026-11-14T00:00:01Z')
        stale, current = dict(run, id=455), dict(run, id=456)
        artifact = dict(total_count=1, artifacts=[dict(name='interoperability-context-1', expired=False)])
        stale_metadata = dict(self.state, run_id='122', control_run_id='455',
                              control_attempt='1', control_sha='c' * 40)
        current_metadata = dict(self.state, control_run_id='456', control_attempt='1', control_sha='c' * 40)
        with patch.object(ci, 'source', return_value=origin), \
                patch.object(ci, 'api', side_effect=[
                    dict(total_count=2, workflow_runs=[current, stale]), artifact, artifact]), \
                patch.object(ci, 'download_records', side_effect=[
                    {None: (stale_metadata, b'{}')}, {None: (current_metadata, b'{}')}]), \
                patch.object(time, 'sleep'):
            self.assertEqual(ci.controller(), '456')

    def test_duplicate_controller_never_issues_another_enrolment(self):
        output = self.root / 'output'
        with patch.object(sys, 'argv', ['interop-broker.py', 'metadata', '--root', str(self.root)]), \
                patch.dict(os.environ, GITHUB_OUTPUT=str(output)), patch.dict(broker['main'].__globals__,
                    find_controller=lambda **kwargs: dict(id=111),
                    Tailnet=lambda: self.fail('Duplicate controller obtained network authority')):
            self.assertEqual(broker['main'](), 0)
        self.assertEqual(output.read_text(), 'enabled=false\n')
        self.assertFalse((self.root / 'context.json').exists())

    def test_controller_event_is_bound_to_repository_and_workflow(self):
        for field in ('path', 'owner', 'repository'):
            event = copy.deepcopy(self.event)
            if field == 'path':
                event['workflow']['path'] = '.github/workflows/other.yml'
            elif field == 'owner':
                event['repository']['owner']['id'] = 1
            else:
                event['repository']['id'] = 1
            (self.root / 'event.json').write_text(json.dumps(event))
            with self.subTest(field=field), self.assertRaises(network.NetworkError):
                network.workflow_event()

    def test_endpoints_cannot_request_oidc(self):
        for key in ('ACTIONS_ID_TOKEN_REQUEST_URL', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'fixture'}), self.assertRaises(network.NetworkError):
                enrol.generate(self.root, 'linux', self.state)
        self.assertFalse((self.root / 'enrol-private.pem').exists())

    def test_enrolment_rejects_replay_and_tampering(self):
        enrol.generate(self.root, 'linux', self.state)
        public = json.loads((self.root / 'public-key.json').read_text())
        record = enrol.seal(self.root, 'linux', public, 'key1', 'tskey-auth-fixture', self.state)
        for field, value in dict(role='macos', run_attempt='1', source_sha='b' * 40,
                                 public_key_sha256='0' * 64, encrypted_key='A' * 512).items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                enrol.decrypt(self.root, 'linux', dict(record, **{field: value}), self.state)
        self.assertFalse((self.root / 'enrol-key').exists())

    def test_auth_key_scope(self):
        with patch.object(network, 'exchange', return_value='fixture'), patch.object(network, 'fetch',
                return_value=(b'{"id":"key1","key":"tskey-auth-fixture"}', {})) as fetch:
            network.Tailnet().create_key('linux', self.state)
        payload = json.loads(fetch.call_args[0][2])
        self.assertEqual(payload['expirySeconds'], 900)
        self.assertEqual(payload['description'], 'ci-123-2-linux')
        self.assertEqual(payload['capabilities']['devices']['create'],
                         dict(reusable=False, ephemeral=True, preauthorized=True, tags=[QUARANTINE]))

    def test_readiness_is_unique_and_frozen_before_admission(self):
        class API:
            def devices(api):
                return self.devices
        with patch.dict(broker['prepare'].__globals__, records=lambda *args: self.nodes):
            broker['prepare'](self.root, API())
        lease = broker['load'](self.root, 'lease')
        self.assertEqual(set(lease['nodes']), set(ROLES))
        self.assertEqual(len(lease['grant']['src']), 7)
        for field, value in dict(key_id='other', oidc_available=True, node_id='foreign').items():
            nodes = copy.deepcopy(self.nodes)
            nodes['linux'][field] = value
            with self.subTest(field=field), patch.dict(broker['prepare'].__globals__, records=lambda *args: nodes), self.assertRaises(network.NetworkError):
                broker['prepare'](self.root, API())

    def test_cleanup_preserves_foreign_policy_and_devices(self):
        own = dict(src=['100.64.0.1'], dst=['100.64.0.1'], ip=list(PORTS))
        foreign = dict(src=['100.64.1.1'], dst=['100.64.1.1'], ip=list(PORTS))
        class API:
            policy = dict(acls=[], grants=[own, foreign])
            deleted = []
            def devices(api):
                return [dict(id='new', nodeId='foreign', addresses=['100.64.0.1'])]
            def change(api, transform):
                api.policy = transform(api.policy)
            def delete(api, key):
                api.deleted.append(key)
            def delete_key(api, key):
                api.deleted.append(key)
        api = API()
        broker['cleanup'](api, dict(grant=own, devices=[dict(node_id='old', device_id='old')], keys={'linux': 'key1'}))
        self.assertEqual(api.policy['grants'], [foreign])
        self.assertNotIn('new', api.deleted)
        self.assertIn('key1', api.deleted)

    def test_cleanup_never_deletes_a_reassigned_device_id(self):
        class API:
            def devices(api):
                return [dict(id='old', nodeId='foreign')]
            def change(api, transform):
                transform(dict(acls=[], grants=[]))
            def delete(api, key):
                self.fail('Foreign device was deleted')
            def delete_key(api, key):
                pass
        with self.assertRaisesRegex(network.NetworkError, 'identity changed'):
            broker['cleanup'](API(), dict(grant=dict(src=['100.64.0.1'], dst=['100.64.0.1'], ip=list(PORTS)),
                                         devices=[dict(node_id='own', device_id='old')], keys={}))

    def test_cleanup_attempts_all_owned_resources_after_policy_failure(self):
        calls = []
        class API:
            def devices(api):
                return []
            def change(api, transform):
                raise network.NetworkError('Policy update failed')
            def delete(api, key):
                calls.append(key)
                if key == '1':
                    raise network.NetworkError('Device removal failed')
            def delete_key(api, key):
                calls.append(key)
        lease = dict(grant={}, devices=[dict(node_id='n1', device_id='1'), dict(node_id='n2', device_id='2')], keys={'linux': 'key1'})
        with self.assertRaises(network.NetworkError):
            broker['cleanup'](API(), lease)
        self.assertEqual(calls, ['1', '2', 'key1'])

    def test_finish_without_enrolment_needs_no_network_cleanup(self):
        (self.root / 'keys.json').unlink()
        with patch.object(sys, 'argv', ['interop-broker.py', 'finish', '--root', str(self.root)]), \
                patch.dict(broker['main'].__globals__, identity=lambda: self.state,
                           Tailnet=lambda: self.fail('Empty cleanup obtained network authority')):
            self.assertEqual(broker['main'](), 0)
        result = json.loads((self.root / 'result.json').read_text())
        self.assertEqual(result, dict(self.state, passed=False, cleaned=True, roles=[]))

    def test_finish_reports_transfer_failure_after_proven_cleanup(self):
        lease = dict(nodes={role: dict(node_id=role) for role in ROLES}, devices=[], keys={}, grant={})
        broker['save'](self.root, 'lease', **lease)
        cleanup = Mock()
        with patch.object(sys, 'argv', ['interop-broker.py', 'finish', '--root', str(self.root)]), \
                patch.dict(broker['main'].__globals__,
                           records=Mock(side_effect=ci.PeerUnavailable('fixture failure')),
                           cleanup=cleanup, Tailnet=Mock(return_value=object())), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(broker['main'](), 0)
        cleanup.assert_called_once()
        result = json.loads((self.root / 'result.json').read_text())
        self.assertEqual(result, dict(self.state, passed=False, cleaned=True, roles=list(ROLES)))

    def test_interrupted_key_creation_is_not_empty_cleanup(self):
        (self.root / 'keys.json').unlink()
        def issue(role, state):
            self.assertEqual(broker['load'](self.root, 'keys')['keys'], {})
            raise network.NetworkError('Key response lost')
        api = Mock()
        api.policy.return_value = ({}, {})
        api.create_key.side_effect = issue
        recipients = {role: {'public_key': role} for role in ROLES}
        with patch.dict(broker['main'].__globals__, identity=lambda: self.state, Tailnet=lambda: api,
                        records=lambda *args, **kwargs: recipients, check_policy=lambda policy: None), \
                patch('sys.stderr', new_callable=io.StringIO):
            for phase in ('broker', 'finish'):
                with patch.object(sys, 'argv', ['interop-broker.py', phase, '--root', str(self.root)]):
                    self.assertEqual(broker['main'](), 1)
        self.assertFalse((self.root / 'result.json').exists())

    def test_recovery_handles_completed_and_unstarted_controllers(self):
        run = dict(id=456, run_attempt=1, path='.github/workflows/interoperability.yml', head_branch='master',
                   event='workflow_run', head_sha='c' * 40, repository=dict(id=266877886),
                   status='completed', conclusion='failure')
        completed = dict(name='Admission and cleanup', run_id=456, run_attempt=1, status='completed',
                         steps=[dict(name='Verify revocation and remove devices', conclusion='success')])
        variants = [dict(total_count=1, jobs=[completed]), dict(total_count=0, jobs=[])]
        for listing in variants:
            responses = [(json.dumps(run).encode(), {}), (json.dumps(listing).encode(), {})]
            with self.subTest(listing=listing), \
                    patch.dict(os.environ, INTEROP_RECOVER_RUN='456', INTEROP_RECOVER_ATTEMPT='1'), \
                    patch.object(network, 'fetch', side_effect=responses), \
                    patch.dict(broker['recover'].__globals__,
                               download_records=lambda *args, **kwargs: self.fail('Clean controller requested a lease'),
                               Tailnet=lambda: self.fail('Clean controller obtained network authority')):
                broker['recover'](self.root)

    def test_recovery_verifies_exact_bytes_before_any_mutation(self):
        run = dict(id=456, run_attempt=1, path='.github/workflows/interoperability.yml',
                   head_branch='master', event='workflow_run', head_sha='c' * 40, repository=dict(id=266877886),
                   status='completed', conclusion='failure')
        lease = dict(self.state, control_run_id='456', control_attempt='1', control_sha='c' * 40)
        raw = json.dumps(lease, indent=4).encode()
        env = dict(INTEROP_RECOVER_RUN='456', INTEROP_RECOVER_ATTEMPT='1',
                   ACTIONS_ID_TOKEN_REQUEST_TOKEN='fixture-not-forwarded')
        def verified(argv, **kwargs):
            self.assertEqual(Path(argv[3]).read_bytes(), raw)
            self.assertNotIn('ACTIONS_ID_TOKEN_REQUEST_TOKEN', kwargs['env'])
            return subprocess.CompletedProcess(argv, 1)
        responses = [(json.dumps(run).encode(), {}),
                     (json.dumps({'total_count': 1, 'jobs': [dict(
                         name='Admission and cleanup', run_id=456, run_attempt=1,
                         status='completed', steps=[])]}).encode(), {})]
        with patch.dict(os.environ, env), patch.object(network, 'fetch', side_effect=responses), \
                patch.dict(broker['recover'].__globals__, download_records=lambda *args, **kwargs: {None: (lease, raw)},
                           cleanup=lambda *args: self.fail('Mutation preceded attestation')), \
                patch.object(subprocess, 'run', side_effect=verified):
            with self.assertRaisesRegex(network.NetworkError, 'attested'):
                broker['recover'](self.root)

    def test_cancelled_or_superseded_ci_aborts_rendezvous(self):
        for current in (dict(run_attempt=3), dict(run_attempt=2, conclusion='cancelled'),
                        dict(run_attempt=2, status='completed')):
            def downloading(*args, **kwargs):
                kwargs['progress']()
                self.fail('Failed CI did not abort')
            with self.subTest(current=current), patch.object(ci, 'api', return_value=current), \
                    patch.object(ci, 'download_records', side_effect=downloading), patch.object(time, 'monotonic', return_value=100):
                with self.assertRaises(network.NetworkError) as caught:
                    ci.records('ready')
                self.assertNotIsInstance(caught.exception, ci.PeerUnavailable)

    def test_failed_peer_blocks_rendezvous(self):
        def downloading(*args, **kwargs):
            kwargs['progress']()
            self.fail('Missing peer did not block the phase')
        for conclusion in ('failure', 'cancelled', 'timed_out', 'action_required'):
            jobs = dict(total_count=1, jobs=[dict(name='Interoperability / NetBSD', conclusion=conclusion)])
            with self.subTest(conclusion=conclusion), patch.object(ci, 'api', side_effect=[dict(run_attempt=2), jobs]), \
                    patch.object(ci, 'download_records', side_effect=downloading), patch.object(time, 'monotonic', return_value=100), \
                    self.assertRaisesRegex(ci.PeerUnavailable, 'NetBSD'):
                ci.records('ready')

    def test_failed_controller_blocks_its_dependants(self):
        for conclusion in ('failure', 'cancelled', 'timed_out'):
            with self.subTest(conclusion=conclusion), patch.object(ci, 'find_controller', return_value=dict(conclusion=conclusion)), \
                    self.assertRaisesRegex(ci.PeerUnavailable, 'Network controller failed'):
                ci.controller()

    def test_invalid_recovery_lease_never_reaches_cleanup(self):
        run = dict(id=456, run_attempt=1, path='.github/workflows/interoperability.yml', head_branch='master',
                   event='workflow_run', head_sha='c' * 40, repository=dict(id=266877886),
                   status='completed', conclusion='failure')
        lease = dict(self.state, control_run_id='456', control_attempt='2', control_sha='c' * 40)
        responses = [(json.dumps(run).encode(), {}),
                     (json.dumps({'total_count': 1, 'jobs': [dict(
                         name='Admission and cleanup', run_id=456, run_attempt=1,
                         status='completed', steps=[])]}).encode(), {})]
        with patch.dict(os.environ, INTEROP_RECOVER_RUN='456', INTEROP_RECOVER_ATTEMPT='1'), \
                patch.object(network, 'fetch', side_effect=responses), \
                patch.dict(broker['recover'].__globals__, download_records=lambda *args, **kwargs: {None: (lease, json.dumps(lease).encode())},
                           cleanup=lambda *args: self.fail('Stale lease reached cleanup')), \
                patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(network.NetworkError, 'attested'):
                broker['recover'](self.root)

    def test_artifact_archive_retry_and_duplicates(self):
        record = dict(self.state, role='linux')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('ready.json', json.dumps(record))
        item = dict(name='ready', id=1, expired=False, archive_download_url='https://attacker.invalid')
        listing = json.dumps(dict(artifacts=[item], total_count=1)).encode()
        responses = [(listing, {'ETag': 'v1'}), network.NetworkError('Not ready', 404),
                     network.NetworkError('Not modified', 304), (buffer.getvalue(), {})]
        with patch.object(network, 'fetch', side_effect=responses) as fetch, patch.object(time, 'sleep'):
            value = network.download_records('123', {'ready': 'linux'})
        self.assertEqual(value['linux'][0], record)
        self.assertEqual(fetch.call_args_list[2][1]['headers'], {'If-None-Match': 'v1'})
        self.assertEqual(fetch.call_args[0][0], 'https://api.github.com/repos/RsyncProject/rsync/actions/artifacts/1/zip')
        for code in (401, 403, 500):
            with self.subTest(code=code), patch.object(network, 'fetch', side_effect=[(listing, {}), network.NetworkError('Failed', code)]), patch.object(time, 'sleep') as sleep:
                with self.assertRaises(network.NetworkError):
                    network.download_records('123', {'ready': 'linux'})
                sleep.assert_not_called()
        duplicate = json.dumps(dict(artifacts=[item, dict(item, id=2)], total_count=2)).encode()
        with patch.object(network, 'fetch', return_value=(duplicate, {})), self.assertRaisesRegex(network.NetworkError, 'Duplicate'):
            network.download_records('123', {'ready': 'linux'})

    def test_redacted_http_errors(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise urllib.error.HTTPError('https://example.test/private-token', 403, 'private body', {}, io.BytesIO(b'private body'))
        with patch.object(network.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaises(network.NetworkError) as caught:
                network.fetch('https://example.test', 'fixture-bearer')
        self.assertEqual(str(caught.exception), 'Network request: HTTP 403')
        self.assertEqual(caught.exception.code, 403)
        with patch.object(network.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(network.NetworkError, 'Worker acl access probe: HTTP 403'):
                network.fetch('https://example.test', 'fixture-bearer', operation='Worker acl access probe')

    def test_artifact_redirect_drops_authorisation(self):
        requests = []
        class Response:
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, size):
                return b'{}'
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                if len(requests) == 1:
                    raise urllib.error.HTTPError(request.full_url, 302, 'redirect',
                                                 {'Location': 'https://fixture.blob.core.windows.net/result?fixture=signed'}, io.BytesIO())
                return Response()
        with patch.object(network.urllib.request, 'build_opener', return_value=Opener()):
            network.fetch('https://api.github.com/example', 'fixture-token', allowed_redirect=True)
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer fixture-token')
        self.assertIsNone(requests[1].get_header('Authorization'))

    def test_cas_preserves_other_cohort(self):
        own = dict(src=['100.64.0.1'], dst=['100.64.0.1'], ip=list(PORTS))
        foreign = dict(src=['100.64.1.1'], dst=['100.64.1.1'], ip=list(PORTS))
        initial, concurrent = dict(acls=[], grants=[]), dict(acls=[], grants=[foreign])
        responses = [(json.dumps(initial).encode(), {'ETag': 'v1'}), network.NetworkError('HTTP 412', 412),
                     (json.dumps(concurrent).encode(), {'ETag': 'v2'}), (b'{}', {'ETag': 'v3'})]
        with patch.object(network, 'exchange', return_value='fixture'), patch.object(network, 'fetch', side_effect=responses) as fetch:
            from interop_policy import update_policy
            version = network.Tailnet().change(lambda policy: update_policy(policy, own))
        self.assertEqual(version, dict(before='v2', after='v3'))
        final = fetch.call_args_list[-1]
        self.assertEqual(json.loads(final[0][2])['grants'], [foreign, own])
        self.assertEqual(final[0][3]['If-Match'], 'v2')
        self.assertTrue(all(call[0][0] == 'https://api.tailscale.com/api/v2/tailnet/-/acl' for call in fetch.call_args_list))
        with patch.object(network, 'exchange', return_value='fixture'), patch.object(network, 'fetch', return_value=(b'{}', {})):
            with self.assertRaises(network.NetworkError):
                network.Tailnet().change(lambda value: value)


if __name__ == '__main__':
    unittest.main()
