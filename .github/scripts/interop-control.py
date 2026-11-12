import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from interop_enrol import seal
from interop_network import NetworkError, SLOTS, TAILNET_API, Tailnet, artifact_name, artifacts, context, download_records, fetch, decode, validate_preflight, workflow_event
from interop_policy import TAILNET, admission, check_policy, update_policy


class AdmissionBusy(Exception):
    pass


def write(root, name, **data):
    (root / (name + '.json')).write_text(json.dumps(dict(context(), **data)) + '\n')


def receipts(phase, nodes):
    records = artifacts(phase, SLOTS, timeout=300)
    if set(records) != set(SLOTS):
        raise NetworkError('Missing worker receipt')
    for slot, record in records.items():
        if record.get('slot') != slot or record.get('node_id') != nodes[slot]['node_id'] or record.get('passed') is not True:
            raise NetworkError('Invalid worker receipt')
    return records


def prepare(root, api):
    validate_preflight(json.loads((root / 'preflight.json').read_text()))
    nodes = artifacts('ready', SLOTS, timeout=300)
    devices = api.devices()
    keys = json.loads((root / 'keys.json').read_text())['keys']
    ids, ips, grants, owned = set(), set(), [], []
    for slot, node in nodes.items():
        if (node.get('slot') != slot or node.get('oidc_available') is not False
                or node.get('key_id') != keys.get(slot)
                or node.get('preferences') != {'RouteAll': False, 'CorpDNS': False}
                or not isinstance(node.get('nonce'), str) or not re.fullmatch(r'[0-9a-f]{48}', node['nonce'])):
            raise NetworkError('Invalid worker readiness')
        node_id, ip = node['node_id'], node['address']
        if node_id in ids or ip in ips:
            raise NetworkError('Duplicate worker identity')
        ids.add(node_id)
        ips.add(ip)
        matches = [device for device in devices if device.get('nodeId') == node_id]
        hostname = 'ci-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + slot
        if len(matches) != 1 or matches[0].get('hostname') != hostname:
            raise NetworkError('Worker device does not belong to this attempt')
        owned.append(dict(node_id=node_id, device_id=matches[0]['id']))
    for cohort in ('a', 'b'):
        roles = (cohort + '1', cohort + '2')
        manifest = dict(context(), tailnet=TAILNET, repository_id='266877886',
                        nodes={slot: {key: nodes[slot][key] for key in ('node_id', 'address')} for slot in roles})
        grants.append(admission(manifest, context(), devices, roles=roles))
    write(root, 'lease', nodes=nodes, grants=grants, devices=owned, keys=keys,
          control_sha=os.environ['GITHUB_WORKFLOW_SHA'])
    # Validate the existing policy without granting access before quarantine probes.
    def unchanged(policy):
        for grant in grants:
            update_policy(policy, grant)
        return policy
    api.change(unchanged)


def admit(root, api, lease):
    receipts('quarantine', lease['nodes'])
    def add(policy):
        original = policy
        for grant in lease['grants']:
            policy = update_policy(policy, grant)
        if original.get('grants'):
            raise AdmissionBusy()
        return policy
    # Leave time to publish admission within the workers' 600-second wait.
    deadline = time.monotonic() + 480
    while True:
        try:
            version = api.change(add)
            break
        except AdmissionBusy:
            if time.monotonic() >= deadline:
                raise NetworkError('Another cohort did not release admission') from None
            time.sleep(5)
    now = time.time()
    write(root, 'admission', nodes=lease['nodes'], policy=version, admitted_at=now, start_at=now + 60)


def revoke(api, lease):
    def remove(policy):
        for grant in lease['grants']:
            if grant in policy.get('grants', []):
                policy = update_policy(policy, grant, previous=grant, remove=True)
            else:
                update_policy(policy, grant)
        return policy
    return api.change(remove)


def finish(root, api, lease):
    error = None
    try:
        if all((root / (name + '.json')).exists() for name in ('admission', 'verified', 'revocation')):
            records = receipts('revoked', lease['nodes'])
            window = json.loads((root / 'revocation.json').read_text())
            if any(not window['end_at'] <= record.get('finished_at', 0) <= window['end_at'] + 30
                   for record in records.values()):
                raise NetworkError('Revocation receipt is outside the live-listener window')
        else:
            raise NetworkError('Admission or revocation did not complete')
    except ValueError:
        error = True
    finally:
        try:
            revoke(api, lease)
        except ValueError:
            error = True
        for device in lease['devices']:
            try:
                api.delete(device['device_id'])
            except ValueError:
                error = True
        for key_id in lease.get('keys', {}).values():
            try:
                api.delete_key(key_id)
            except NetworkError:
                error = True
        remaining = {device.get('nodeId') for device in api.devices()}
        if any(device['node_id'] in remaining for device in lease['devices']):
            raise NetworkError('Worker devices remain after cleanup')
    if error:
        raise NetworkError('Live isolation proof or cleanup did not complete')
    write(root, 'result', passed=True, scope='two live cohorts in one GitHub run',
          concurrent_github_runs_exercised=False, slots=list(SLOTS), finished_at=time.time(),
          admission_policy=json.loads((root / 'admission.json').read_text()).get('policy'),
          revocation_policy=json.loads((root / 'revocation.json').read_text()).get('policy'))


def broker(root, api):
    public = artifacts('public', SLOTS, timeout=300)
    keys, recipients = {}, {}
    if len({record.get('public_key') for record in public.values()}) != len(SLOTS):
        raise NetworkError('Enrolment recipients share a public key')
    for slot in SLOTS:
        key_id, key = api.create_key(slot)
        keys[slot] = key_id
        write(root, 'keys', keys=keys)
        recipients[slot] = seal(root, slot, public[slot], key_id, key)
    write(root, 'enrolments', recipients=recipients)


def publish_status(state):
    event = workflow_event()
    source = event['workflow_run']
    body, _ = fetch('https://api.github.com/repos/RsyncProject/rsync/actions/runs/' + str(source['id']),
                    os.environ['GH_TOKEN'], operation='Check upstream CI attempt')
    if decode(body).get('run_attempt') != source['run_attempt']:
        raise NetworkError('A newer CI attempt superseded this result')
    body, _ = fetch('https://api.github.com/repos/RsyncProject/rsync/actions/workflows/ci.yml/runs?head_sha='
                    + context()['source_sha'] + '&per_page=1', os.environ['GH_TOKEN'], operation='Check latest CI run')
    runs = decode(body).get('workflow_runs', [])
    if not runs or runs[0].get('id') != source['id']:
        raise NetworkError('A newer CI run superseded this result')
    description = 'CI ' + str(source['id']) + '/' + str(source['run_attempt']) + ': network isolation'
    payload = dict(state=state, context='Interoperability', description=description,
                   target_url='https://github.com/RsyncProject/rsync/actions/runs/' + os.environ['GITHUB_RUN_ID'])
    fetch('https://api.github.com/repos/RsyncProject/rsync/statuses/' + context()['source_sha'], os.environ['GH_TOKEN'],
          json.dumps(payload).encode(), {'Content-Type': 'application/json'}, operation='Publish interoperability status')


def metadata(root):
    state = context()
    source = workflow_event()['workflow_run']
    body, _ = fetch('https://api.github.com/repos/RsyncProject/rsync/actions/runs/' + str(source['id']),
                    os.environ['GH_TOKEN'], operation='Validate upstream CI run')
    run = decode(body)
    if (run.get('path') != '.github/workflows/ci.yml' or run.get('name') != 'CI'
            or run.get('head_sha') != state['source_sha'] or run.get('run_attempt') != source['run_attempt']
            or run.get('repository', {}).get('id') != 266877886):
        raise NetworkError('Upstream CI run does not match its event')
    write(root, 'context', upstream_run_id=str(source['id']), upstream_attempt=str(source['run_attempt']),
          control_sha=os.environ['GITHUB_WORKFLOW_SHA'])
    publish_status('pending')


def cleanup(root, api):
    state = context()
    path = root / 'lease.json'
    if path.is_symlink() or path.stat().st_size > 65536:
        raise NetworkError('Invalid admission lease')
    lease = json.loads(path.read_text())
    control_sha = lease.get('control_sha', '')
    if not isinstance(control_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', control_sha):
        raise NetworkError('Admission lease has an invalid control revision')
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'GH_TOKEN') if key in os.environ}
    env['GH_PROMPT_DISABLED'] = '1'
    result = subprocess.run(['gh', 'attestation', 'verify', str(root / 'lease.json'), '--repo', 'RsyncProject/rsync',
                             '--signer-workflow', 'RsyncProject/rsync/.github/workflows/interoperability.yml',
                             '--signer-digest', control_sha,
                             '--source-ref', 'refs/heads/master', '--deny-self-hosted-runners'],
                            capture_output=True, timeout=60, env=env)
    if result.returncode:
        raise NetworkError('Admission lease attestation failed')
    if any(lease.get(key) != value for key, value in state.items()):
        raise NetworkError('Cleanup lease belongs to a different run')
    members = {node['address'] for node in lease['nodes'].values()}
    owners = {node['node_id'] for node in lease['nodes'].values()}
    for device in api.devices():
        if members.intersection(device.get('addresses', [])) and device.get('nodeId') not in owners:
            raise NetworkError('Admission addresses have been reassigned; cleanup requires review')
    revoke(api, lease)
    for device in lease['devices']:
        api.delete(device['device_id'])
    for key_id in lease['keys'].values():
        api.delete_key(key_id)
    remaining = {device.get('nodeId') for device in api.devices()}
    if owners.intersection(remaining):
        raise NetworkError('Owned devices remain after recovery')


def recovery_records(root):
    source = workflow_event()['workflow_run']
    run_id, attempt = str(source['id']), source['run_attempt']
    for kind, filename in (('context', 'context.json'), ('lease', 'lease.json')):
        name = artifact_name(kind, attempt=attempt)
        record, raw = download_records(run_id, {name: None}, timeout=60)[None]
        if record.get('run_id') != run_id or record.get('run_attempt') != str(attempt):
            raise NetworkError('Recovery artifact belongs to a different run')
        (root / filename).write_bytes(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('metadata', 'status', 'preflight', 'broker', 'prepare', 'topology', 'admit', 'verify', 'revoke', 'finish', 'recovery-records', 'cleanup'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        recovery = workflow_event()['workflow']['path'] == '.github/workflows/interoperability.yml'
        if recovery != (args.phase in ('recovery-records', 'cleanup')):
            raise NetworkError('Operation does not match the triggering workflow')
        if args.phase == 'recovery-records':
            recovery_records(root)
            return 0
        context()
        if args.phase == 'metadata':
            metadata(root)
            return 0
        if args.phase == 'status':
            jobs = decode(os.environ['INTEROPERABILITY_JOBS'])
            success = set(jobs) == {'checks', 'metadata', 'workers', 'controller'} and all(job.get('result') == 'success' for job in jobs.values())
            if success:
                proof = artifacts('result')[None]
                success = proof.get('passed') is True and proof.get('slots') == list(SLOTS)
            publish_status('success' if success else 'failure')
            return 0 if success else 1
        path = root / 'lease.json'
        if not path.exists() and args.phase in ('revoke', 'finish'):
            keys_path = root / 'keys.json'
            if keys_path.exists():
                api = Tailnet()
                keys = json.loads(keys_path.read_text())
                if any(keys.get(key) != value for key, value in context().items()):
                    raise NetworkError('Key journal belongs to a different run')
                for key_id in keys['keys'].values():
                    api.delete_key(key_id)
            return 0
        api = Tailnet()
        if args.phase == 'preflight':
            api.devices()
            policy, _ = api.policy()
            try:
                check_policy(policy)
            except ValueError:
                raise NetworkError('Test tailnet policy does not have the required isolation baseline') from None
            write(root, 'preflight', api=TAILNET_API, readable=['acl', 'devices'])
        elif args.phase == 'broker':
            broker(root, api)
        elif args.phase == 'cleanup':
            cleanup(root, api)
        elif args.phase == 'prepare':
            prepare(root, api)
        else:
            lease = json.loads(path.read_text())
            if args.phase == 'topology':
                write(root, 'topology', nodes=lease['nodes'], start_at=time.time() + 60)
            elif args.phase == 'admit':
                admit(root, api, lease)
            elif args.phase == 'verify':
                receipts('exercise', lease['nodes'])
                write(root, 'verified', passed=True)
            elif args.phase == 'revoke':
                version = revoke(api, lease)
                now = time.time()
                start = now + 60
                write(root, 'revocation', policy=version, revoked_at=now, start_at=start, end_at=start + 90)
            else:
                finish(root, api, lease)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, NetworkError) else type(error).__name__
        print('Interoperability controller failed during ' + args.phase + ': ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
