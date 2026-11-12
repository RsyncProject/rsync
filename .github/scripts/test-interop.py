import copy
import io
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

import interop_network as network
import interop_enrol as enrol
from interop_policy import PORTS, QUARANTINE

worker = runpy.run_path(str(Path(__file__).with_name('interop-worker.py')))
control = runpy.run_path(str(Path(__file__).with_name('interop-control.py')))


class Isolation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.event = dict(action='in_progress', repository=dict(id=266877886, owner=dict(id=166180282)),
                          workflow=dict(path='.github/workflows/ci.yml'),
                          workflow_run=dict(id=456, run_attempt=3, head_sha='a' * 40, name='CI', event='pull_request',
                                            head_repository=dict(id=999, full_name='contributor/rsync')))
        (self.root / 'event.json').write_text(json.dumps(self.event))
        self.env = dict(GITHUB_REPOSITORY_ID='266877886', GITHUB_EVENT_NAME='workflow_run',
                        GITHUB_REF='refs/heads/master', GITHUB_RUN_ID='123', GITHUB_WORKFLOW_SHA='c' * 40,
                        GITHUB_EVENT_PATH=str(self.root / 'event.json'), RUNNER_TEMP=str(self.root),
                        GITHUB_RUN_ATTEMPT='2', GITHUB_SHA='c' * 40, GH_TOKEN='fixture-not-a-credential')
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.preflight = dict(network.context(), api=network.TAILNET_API, readable=['acl', 'devices'])
        (self.root / 'preflight.json').write_text(json.dumps(self.preflight))
        (self.root / 'keys.json').write_text(json.dumps(dict(network.context(), keys={slot: slot for slot in network.SLOTS})))
        self.nodes, self.devices = {}, []
        for i, slot in enumerate(network.SLOTS, 1):
            self.nodes[slot] = dict(network.context(), slot=slot, node_id='n' + str(i), address='100.64.0.' + str(i),
                                    nonce=str(i) * 48, oidc_available=False, key_id=slot)
            self.nodes[slot]['preferences'] = dict(RouteAll=False, CorpDNS=False)
            self.devices.append(dict(nodeId='n' + str(i), id=str(i), hostname='ci-123-2-' + slot,
                                     addresses=['100.64.0.' + str(i)], authorized=True, tags=[QUARANTINE]))

    def test_context(self):
        self.assertEqual(network.context()['run_id'], '123')
        self.assertEqual(network.context()['source_sha'], 'a' * 40)
        for key, value in dict(GITHUB_EVENT_NAME='pull_request_target', GITHUB_REF='refs/pull/7/merge',
                                GITHUB_RUN_ID='../123', GITHUB_RUN_ATTEMPT='0').items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), self.assertRaises(network.NetworkError):
                network.context()
        for event in ('pull_request', 'push', 'schedule', 'workflow_dispatch'):
            self.event['workflow_run']['event'] = event
            (self.root / 'event.json').write_text(json.dumps(self.event))
            self.assertEqual(network.context()['source_sha'], 'a' * 40)

    def test_untrusted_workflow_context(self):
        for field, value in (('path', '.github/workflows/untrusted.yml'), ('owner', 1), ('repository', 1), ('event', 'pull_request_target')):
            event = copy.deepcopy(self.event)
            if field == 'path':
                event['workflow']['path'] = value
            elif field == 'owner':
                event['repository']['owner']['id'] = value
            elif field == 'repository':
                event['repository']['id'] = value
            else:
                event['workflow_run']['event'] = value
            (self.root / 'event.json').write_text(json.dumps(event))
            with self.subTest(field=field), self.assertRaises(network.NetworkError):
                network.context()

    def test_encrypted_single_use_enrolment(self):
        enrol.generate(self.root, 'a1')
        public = json.loads((self.root / 'public-key.json').read_text())
        key = 'tskey-auth-test-fixture'
        record = enrol.seal(self.root, 'a1', public, 'fixtureKey', key)
        self.assertNotIn(key, json.dumps(record))
        enrol.decrypt(self.root, 'a1', record)
        self.assertEqual((self.root / 'enrol-key').read_text(), key)
        self.assertEqual((self.root / 'enrol-key').stat().st_mode & 0o777, 0o600)
        enrol.forget(self.root)
        self.assertFalse((self.root / 'enrol-private.pem').exists())
        self.assertFalse((self.root / 'enrol-key').exists())

    def test_enrolment_rejects_replay_and_tampering(self):
        enrol.generate(self.root, 'a1')
        public = json.loads((self.root / 'public-key.json').read_text())
        record = enrol.seal(self.root, 'a1', public, 'fixtureKey', 'tskey-auth-test-fixture')
        for field, value in dict(slot='b1', run_attempt='1', source_sha='b' * 40, public_key_sha256='0' * 64,
                                 encrypted_key='A' * 512).items():
            with self.subTest(field=field), self.assertRaises(network.NetworkError):
                enrol.decrypt(self.root, 'a1', dict(record, **{field: value}))
        with patch.dict(os.environ, GITHUB_RUN_ATTEMPT='3'), self.assertRaises(network.NetworkError):
            enrol.decrypt(self.root, 'a1', dict(record, run_attempt='3'))
        self.assertFalse((self.root / 'enrol-key').exists())

    def test_auth_key_scope(self):
        with patch.object(network, 'exchange', return_value='fixture'), patch.object(network, 'fetch',
                return_value=(b'{"id":"fixtureKey","key":"tskey-auth-test-fixture"}', {})) as fetch:
            self.assertEqual(network.Tailnet().create_key('a1'), ('fixtureKey', 'tskey-auth-test-fixture'))
        payload = json.loads(fetch.call_args[0][2])
        self.assertEqual(payload['expirySeconds'], 900)
        self.assertEqual(payload['capabilities']['devices']['create'],
                         dict(reusable=False, ephemeral=True, preauthorized=True, tags=[QUARANTINE]))

    def test_status_cannot_overwrite_a_newer_run(self):
        responses = [(b'{"run_attempt":3}', {}), (b'{"workflow_runs":[{"id":456}]}', {}), (b'{}', {})]
        fetch = Mock(side_effect=responses)
        with patch.dict(control['publish_status'].__globals__, fetch=fetch):
            control['publish_status']('success')
        self.assertTrue(fetch.call_args[0][0].endswith('/statuses/' + 'a' * 40))
        for responses in ([(b'{"run_attempt":4}', {})],
                          [(b'{"run_attempt":3}', {}), (b'{"workflow_runs":[{"id":457}]}', {})]):
            fetch = Mock(side_effect=responses)
            with patch.dict(control['publish_status'].__globals__, fetch=fetch), self.assertRaisesRegex(network.NetworkError, 'superseded'):
                control['publish_status']('success')

    def test_cleanup_requires_attested_lease(self):
        lease = dict(network.context(), nodes=self.nodes, keys={}, devices=[], grants=[], control_sha='c' * 40)
        (self.root / 'lease.json').write_text(json.dumps(lease))
        class API:
            def devices(api):
                self.fail('API accessed before attestation verification')
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(network.NetworkError, 'attestation'):
                control['cleanup'](self.root, API())

    def test_recovery_records_keep_the_attested_bytes(self):
        state = network.context()
        event = copy.deepcopy(self.event)
        event.update(action='completed', workflow=dict(path='.github/workflows/interoperability.yml'))
        event['workflow_run'].update(id=123, run_attempt=2, head_sha='c' * 40, name='Interoperability', event='workflow_run')
        (self.root / 'event.json').write_text(json.dumps(event))
        root = self.root / 'interoperability-cleanup'
        root.mkdir()
        metadata = dict(state, control_sha='c' * 40)
        lease = dict(metadata, nodes=self.nodes, grants=[], devices=[], keys={})
        meta_raw, lease_raw = json.dumps(metadata, indent=2).encode(), json.dumps(lease, indent=4).encode()
        records = [{None: (metadata, meta_raw)}, {None: (lease, lease_raw)}]
        with patch.dict(control['recovery_records'].__globals__, download_records=Mock(side_effect=records)):
            control['recovery_records'](root)
        self.assertEqual((root / 'lease.json').read_bytes(), lease_raw)
        self.assertEqual(network.context(), state)
        event['workflow_run']['event'] = 'pull_request'
        (self.root / 'event.json').write_text(json.dumps(event))
        with self.assertRaises(network.NetworkError):
            network.context()

    def test_cleanup_does_not_touch_reassigned_addresses(self):
        (self.root / 'lease.json').write_text(json.dumps(dict(network.context(), nodes=self.nodes, keys={}, devices=[], grants=[], control_sha='c' * 40)))
        class API:
            def devices(api):
                return [dict(nodeId='foreign', addresses=['100.64.0.1'])]
            def change(api, transform):
                self.fail('Foreign policy was changed')
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as verify:
            with self.assertRaisesRegex(network.NetworkError, 'reassigned'):
                control['cleanup'](self.root, API())
        self.assertIn('--signer-digest', verify.call_args[0][0])
        self.assertIn('--deny-self-hosted-runners', verify.call_args[0][0])

    def test_success_requires_revocation_receipts_and_resource_removal(self):
        policies = dict(before='v1', after='v2')
        for name, data in dict(admission=dict(policy=policies), verified=dict(passed=True),
                                revocation=dict(end_at=100, policy=dict(before='v2', after='v3'))).items():
            (self.root / (name + '.json')).write_text(json.dumps(data))
        removed = []
        class API:
            def change(api, transform):
                transform(dict(acls=[], grants=[]))
            def delete(api, node_id):
                removed.append(node_id)
            def delete_key(api, key_id):
                removed.append(key_id)
            def devices(api):
                return []
        lease = dict(nodes=self.nodes, grants=[], keys=dict(a1='key1'), devices=[dict(node_id='n1', device_id='1')])
        records = {slot: dict(finished_at=100) for slot in network.SLOTS}
        with patch.dict(control['finish'].__globals__, receipts=lambda *args: records):
            control['finish'](self.root, API(), lease)
        result = json.loads((self.root / 'result.json').read_text())
        self.assertTrue(result['passed'])
        self.assertEqual(result['admission_policy'], policies)
        self.assertEqual(removed, ['1', 'key1'])

    def test_network_preferences(self):
        prefs = dict(RouteAll=False, CorpDNS=False, Config=dict(PrivateNodeKey='fixture-not-a-key'))
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(prefs))) as run:
            self.assertEqual(worker['configure_network'](), dict(RouteAll=False, CorpDNS=False))
        self.assertEqual(run.call_args_list[0][0][0], ['sudo', 'tailscale', 'set', '--accept-routes=false', '--accept-dns=false'])
        self.assertEqual(run.call_args_list[1][0][0], ['sudo', 'tailscale', 'debug', 'prefs'])
        for key in ('RouteAll', 'CorpDNS'):
            for value in (True, None, 0, 'false'):
                with self.subTest(key=key, value=value), patch.object(subprocess, 'run',
                        return_value=subprocess.CompletedProcess([], 0, json.dumps(dict(prefs, **{key: value})))):
                    with self.assertRaises(network.NetworkError):
                        worker['configure_network']()
        with patch.object(subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['tailscale'])):
            with self.assertRaises(subprocess.CalledProcessError):
                worker['configure_network']()
        def fail_setup():
            raise ValueError('setup failed')
        with patch.dict(worker['prepare'].__globals__, configure_network=fail_setup,
                        worker_authority=lambda: self.fail('Authority checked before network setup')):
            with self.assertRaises(ValueError):
                worker['prepare'](self.root, 'a1')
        self.assertFalse((self.root / 'ready.json').exists())

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

    def test_controller_preflight_precedes_worker_wait(self):
        calls = []
        class API:
            def devices(api):
                calls.append('devices')
            def policy(api):
                calls.append('policy')
                return dict(acls=[], grants=[]), {}
        args = ['interop-control.py', 'preflight', '--root', str(self.root)]
        with patch.object(sys, 'argv', args), patch.dict(control['main'].__globals__, Tailnet=API,
                prepare=lambda root, api: calls.append('prepare')):
            self.assertEqual(control['main'](), 0)
        self.assertEqual(calls, ['devices', 'policy'])
        self.assertEqual(json.loads((self.root / 'preflight.json').read_text()), self.preflight)
        calls.clear()
        with patch.object(sys, 'argv', args), patch.dict(control['main'].__globals__, Tailnet=API,
                prepare=lambda root, api: self.fail('Worker wait started after a failed preflight')), \
                patch.object(API, 'policy', side_effect=network.NetworkError('Read Tailscale policy: HTTP 404', 404)), \
                patch.object(sys, 'stderr', io.StringIO()) as stderr:
            self.assertEqual(control['main'](), 1)
        self.assertIn('Read Tailscale policy: HTTP 404', stderr.getvalue())
        with patch.object(sys, 'argv', args), patch.dict(control['main'].__globals__, Tailnet=API), \
                patch.object(API, 'policy', return_value=({}, {})), patch.object(sys, 'stderr', io.StringIO()):
            self.assertEqual(control['main'](), 1)
        with patch.object(sys, 'argv', ['interop-control.py', 'revoke', '--root', str(self.root)]), \
                patch.dict(control['main'].__globals__, Tailnet=lambda: self.fail('No lease requires no API access')):
            (self.root / 'keys.json').unlink()
            self.assertEqual(control['main'](), 0)

    def test_worker_has_no_oidc_authority(self):
        with patch.object(network, 'artifacts', return_value={None: self.preflight}):
            self.assertIs(network.worker_authority(), False)
        for key in ('ACTIONS_ID_TOKEN_REQUEST_URL', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN'):
            with patch.dict(os.environ, {key: 'fixture'}), self.assertRaisesRegex(network.NetworkError, 'OIDC'):
                network.worker_authority()
            with patch.dict(os.environ, {key: 'fixture'}), self.assertRaisesRegex(network.NetworkError, 'OIDC'):
                enrol.generate(self.root, 'a1')
        self.assertFalse((self.root / 'enrol-private.pem').exists())

    def test_denial_requires_current_positive_control(self):
        for field, value in dict(run_attempt='1', source_sha='b' * 40, api='https://example.test', readable=['devices']).items():
            with self.subTest(field=field):
                with patch.object(network, 'artifacts', return_value={None: dict(self.preflight, **{field: value})}), \
                        patch.object(network, 'fetch') as fetch:
                    with self.assertRaisesRegex(network.NetworkError, 'positive control'):
                        network.worker_authority()
                    fetch.assert_not_called()
        (self.root / 'preflight.json').write_text(json.dumps(dict(self.preflight, run_attempt='1')))
        with patch.dict(control['prepare'].__globals__, artifacts=lambda *args, **kwargs: self.fail('Stale preflight accepted')):
            with self.assertRaisesRegex(network.NetworkError, 'positive control'):
                control['prepare'](self.root, None)

    def test_setup_guard_messages_are_reported(self):
        args = ['interop-worker.py', 'prepare', 'a1', '--root', str(self.root)]
        for message in ('Worker obtained a controller credential', 'Route or DNS acceptance is still enabled'):
            def fail(root, slot):
                raise network.NetworkError(message)
            with patch.object(sys, 'argv', args), patch.dict(worker['main'].__globals__, prepare=fail), \
                    patch.object(sys, 'stderr', io.StringIO()) as stderr:
                self.assertEqual(worker['main'](), 1)
            self.assertIn(message, stderr.getvalue())

    def test_policy_lifecycle(self):
        foreign = dict(src=['100.64.1.1'], dst=['100.64.1.1'], ip=list(PORTS))
        class API:
            policy = dict(acls=[], grants=[])
            def devices(api):
                return self.devices
            def change(api, transform):
                api.policy = transform(copy.deepcopy(api.policy))
        api = API()
        with patch.dict(control['prepare'].__globals__, artifacts=lambda *args, **kwargs: self.nodes):
            control['prepare'](self.root, api)
        self.assertEqual(api.policy['grants'], [])
        lease = json.loads((self.root / 'lease.json').read_text())
        with patch.dict(control['admit'].__globals__, receipts=lambda *args: None):
            control['admit'](self.root, api, lease)
        self.assertEqual(len(api.policy['grants']), 2)
        api.policy['grants'].append(foreign)
        control['revoke'](api, lease)
        self.assertEqual(api.policy['grants'], [foreign])
        control['revoke'](api, lease)
        self.assertEqual(api.policy['grants'], [foreign])
        api.policy['acls'] = [{}]
        with self.assertRaises(ValueError):
            control['revoke'](api, lease)

    def test_admission_waits_without_overwriting_another_cohort(self):
        own = dict(src=['100.64.0.1', '100.64.0.2'], dst=['100.64.0.1', '100.64.0.2'], ip=list(PORTS))
        foreign = dict(src=['100.64.1.1'], dst=['100.64.1.1'], ip=list(PORTS))
        class API:
            calls = 0
            def change(api, transform):
                api.calls += 1
                result = transform(dict(acls=[], grants=[foreign] if api.calls == 1 else []))
                self.assertEqual(result['grants'], [own])
        api = API()
        with patch.dict(control['admit'].__globals__, receipts=lambda *args: None), patch.object(time, 'sleep') as sleep:
            control['admit'](self.root, api, dict(nodes=self.nodes, grants=[own]))
        self.assertEqual(api.calls, 2)
        sleep.assert_called_once_with(5)

    def test_admission_wait_is_bounded(self):
        own = dict(src=['100.64.0.1'], dst=['100.64.0.1'], ip=list(PORTS))
        foreign = dict(src=['100.64.1.1'], dst=['100.64.1.1'], ip=list(PORTS))
        class API:
            def change(api, transform):
                transform(dict(acls=[], grants=[foreign]))
        with patch.dict(control['admit'].__globals__, receipts=lambda *args: None), \
                patch.object(time, 'monotonic', side_effect=[0, 1, 481]), patch.object(time, 'sleep'):
            with self.assertRaisesRegex(network.NetworkError, 'release admission'):
                control['admit'](self.root, API(), dict(nodes=self.nodes, grants=[own]))
        self.assertFalse((self.root / 'admission.json').exists())

    def test_cleanup_attempts_every_device_after_policy_failure(self):
        deleted = []
        class API:
            def change(api, transform):
                raise network.NetworkError('HTTP 500', 500)
            def delete(api, device_id):
                deleted.append(device_id)
                if device_id == '1':
                    raise network.NetworkError('HTTP 500', 500)
            def devices(api):
                return []
        lease = dict(nodes=self.nodes, grants=[], devices=[dict(node_id='n1', device_id='1'), dict(node_id='n2', device_id='2')])
        with self.assertRaises(ValueError):
            control['finish'](self.root, API(), lease)
        self.assertEqual(deleted, ['1', '2'])
        self.assertFalse((self.root / 'result.json').exists())

    def test_readiness_identity(self):
        class API:
            def devices(api):
                return self.devices
            def change(api, transform):
                transform(dict(acls=[]))
        for field, value in dict(nonce='', oidc_available=True, key_id='foreign',
                                 node_id='n2', address='100.64.0.2', preferences={}).items():
            nodes = copy.deepcopy(self.nodes)
            nodes['a1'][field] = value
            with self.subTest(field=field), patch.dict(control['prepare'].__globals__, artifacts=lambda *args, **kwargs: nodes):
                with self.assertRaises(ValueError):
                    control['prepare'](self.root, API())

    def test_live_tcp(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(2)
            listener.settimeout(5)
            port = listener.getsockname()[1]
            def reply():
                for _ in range(3):
                    with listener.accept()[0] as peer:
                        peer.sendall(b'nonce')
            thread = threading.Thread(target=reply)
            thread.start()
            try:
                self.assertTrue(worker['connect']('127.0.0.1', port, 'nonce'))
                with self.assertRaisesRegex(ValueError, 'Forbidden'):
                    worker['denied']('127.0.0.1', port)
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    worker['connect']('127.0.0.1', port, 'wrong')
            finally:
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertFalse(worker['connect']('127.0.0.1', port))
        with patch.dict(worker['health'].__globals__, connect=lambda *args: False):
            with self.assertRaisesRegex(ValueError, 'not listening'):
                worker['health'](dict(address='127.0.0.1', nonce='nonce'))

    def test_exercise_probes_both_foreign_nodes_and_wrong_port(self):
        (self.root / 'ready.json').write_text(json.dumps(self.nodes['a1']))
        connections, denials = [], []
        def connected(ip, port, nonce=None):
            connections.append((ip, port, nonce))
            return True
        def published(kind):
            return {None: dict(nodes=self.nodes, start_at=time.time())}
        globals_ = worker['probe'].__globals__
        with patch.dict(globals_, artifacts=published, connect=connected, denied=lambda *args: denials.append(args),
                        wait_until=lambda value: None):
            worker['probe'](self.root, 'a1', 'exercise')
        self.assertEqual(denials, [('100.64.0.3', 18731), ('100.64.0.4', 18731), ('100.64.0.2', 18732)])
        self.assertEqual(connections.count(('100.64.0.2', 18731, '2' * 48)), 2)
        self.assertEqual(connections.count(('100.64.0.1', 18732, '1' * 48)), 2)
        self.assertTrue(json.loads((self.root / 'exercise.json').read_text())['passed'])

    def test_revocation_keeps_listeners_alive_through_the_barrier(self):
        (self.root / 'ready.json').write_text(json.dumps(self.nodes['a1']))
        start = time.time()
        connections, denials = [], []
        def connected(ip, port, nonce=None):
            connections.append((ip, port))
            return ip == '100.64.0.1'
        def published(kind):
            return {None: dict(nodes=self.nodes, start_at=start, end_at=start + 90)}
        with patch.dict(worker['probe'].__globals__, artifacts=published, connect=connected,
                        denied=lambda *args: denials.append(args), wait_until=lambda value: None), patch.object(time, 'sleep') as sleep:
            worker['probe'](self.root, 'a1', 'revoked')
        self.assertEqual(denials, [('100.64.0.2', 18731)] * 3)
        self.assertTrue(89 < sleep.call_args[0][0] <= 90)
        self.assertEqual(connections.count(('100.64.0.1', 18732)), 2)

    def test_late_barriers_are_rejected(self):
        for value in (time.time() - 10, time.time() + 300, True, 'now', float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker['wait_until'](value)

    def test_artifacts_reject_stale_duplicate_and_redirected_identity(self):
        def zipped(record):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('ready.json', json.dumps(record))
            return output.getvalue()
        item = dict(name=network.artifact_name('ready', 'a1'), id=1, expired=False,
                    archive_download_url='https://attacker.invalid')
        listing = json.dumps(dict(artifacts=[item], total_count=1)).encode()
        with patch.object(network, 'fetch', side_effect=[(listing, {}), (zipped(self.nodes['a1']), {})]) as fetch:
            self.assertEqual(network.artifacts('ready', ('a1',))['a1'], self.nodes['a1'])
        self.assertEqual(fetch.call_args[0][0], 'https://api.github.com/repos/RsyncProject/rsync/actions/artifacts/1/zip')
        responses = [(listing, {}), network.NetworkError('Download GitHub artifact: HTTP 404', 404),
                     (listing, {}), (zipped(self.nodes['a1']), {})]
        with patch.object(network, 'fetch', side_effect=responses) as fetch, patch.object(time, 'sleep') as sleep:
            self.assertEqual(network.artifacts('ready', ('a1',))['a1'], self.nodes['a1'])
        self.assertEqual(fetch.call_args_list[0][0][0], fetch.call_args_list[2][0][0])
        sleep.assert_called_once_with(5)
        responses = [(listing, {'ETag': 'list-v1'}), network.NetworkError('Not ready', 404),
                     network.NetworkError('Not modified', 304), (zipped(self.nodes['a1']), {})]
        with patch.object(network, 'fetch', side_effect=responses) as fetch, patch.object(time, 'sleep'):
            self.assertEqual(network.artifacts('ready', ('a1',))['a1'], self.nodes['a1'])
        self.assertEqual(fetch.call_args_list[2][1]['headers'], {'If-None-Match': 'list-v1'})
        for code in (401, 403, 500):
            with self.subTest(code=code), patch.object(network, 'fetch',
                    side_effect=[(listing, {}), network.NetworkError('Download failed', code)]), patch.object(time, 'sleep') as sleep:
                with self.assertRaises(network.NetworkError) as caught:
                    network.artifacts('ready', ('a1',))
                self.assertEqual(caught.exception.code, code)
                sleep.assert_not_called()
        with patch.object(network, 'fetch', side_effect=[(listing, {}), network.NetworkError('Not ready', 404)]), \
                patch.object(time, 'monotonic', side_effect=[0, 0, 2]), patch.object(time, 'sleep'):
            with self.assertRaisesRegex(network.NetworkError, 'Timed out'):
                network.artifacts('ready', ('a1',), timeout=1)
        stale = dict(self.nodes['a1'], run_attempt='1')
        with patch.object(network, 'fetch', side_effect=[(listing, {}), (zipped(stale), {})]):
            with self.assertRaisesRegex(network.NetworkError, 'Stale'):
                network.artifacts('ready', ('a1',))
        listing = json.dumps(dict(artifacts=[item, dict(item, id=2)], total_count=2)).encode()
        with patch.object(network, 'fetch', return_value=(listing, {})):
            with self.assertRaisesRegex(network.NetworkError, 'Duplicate'):
                network.artifacts('ready', ('a1',))

    def test_receipts_reject_missing_or_failed_worker(self):
        records = {slot: dict(node, passed=True) for slot, node in self.nodes.items()}
        with patch.dict(control['receipts'].__globals__, artifacts=lambda *args, **kwargs: {}):
            with self.assertRaises(ValueError):
                control['receipts']('exercise', self.nodes)
        for field, value in dict(slot='b1', node_id='other', passed=False).items():
            modified = copy.deepcopy(records)
            modified['a1'][field] = value
            with patch.dict(control['receipts'].__globals__, artifacts=lambda *args, **kwargs: modified):
                with self.assertRaises(ValueError):
                    control['receipts']('exercise', self.nodes)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux canary process ownership')
    def test_canary_lifecycle(self):
        ready = dict(address='127.0.0.1', nonce='test-canary')
        (self.root / 'ready.json').write_text(json.dumps(ready))
        command = [sys.executable, '-B', '-S', str(Path(__file__).with_name('interop-worker.py')),
                   'serve', 'a1', '--root', str(self.root)]
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True) as child:
            try:
                deadline = time.monotonic() + 5
                while not (self.root / 'listening').exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail('Canary startup failed')
                    time.sleep(0.05)
                worker['health'](ready)
                stat = Path('/proc') / str(child.pid) / 'stat'
                (self.root / 'process.json').write_text(json.dumps(dict(pid=child.pid, start=stat.read_text().split()[21])))
                worker['stop'](self.root)
                self.assertEqual(child.wait(timeout=5), -15)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
