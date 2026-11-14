import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from interop_ci import ROLES, NotSelected, PeerUnavailable, bind, check_ci, find_controller, identity, records, source, write
from interop_enrol import seal
from interop_network import NetworkError, Tailnet, download_records
from interop_policy import TAILNET, admission, check_policy, update_policy


def load(root, kind):
    path = root / (kind + '.json')
    if path.is_symlink() or path.stat().st_size > 65536:
        raise NetworkError('Invalid controller journal')
    value = json.loads(path.read_text())
    if (value.get('control_run_id') != os.environ['GITHUB_RUN_ID']
            or value.get('control_attempt') != os.environ['GITHUB_RUN_ATTEMPT']
            or value.get('control_sha') != os.environ['GITHUB_WORKFLOW_SHA']):
        raise NetworkError('Controller journal belongs to another attempt')
    return value


def save(root, kind, **data):
    write(root, kind, control_run_id=os.environ['GITHUB_RUN_ID'], control_attempt=os.environ['GITHUB_RUN_ATTEMPT'],
          control_sha=os.environ['GITHUB_WORKFLOW_SHA'], **data)


def verify_cases(receipts, source_sha):
    expected = {(a, b, client, transport) for a in ROLES for b in ROLES if a != b
                for client in ('push', 'pull') for transport in ('ssh', 'daemon')}
    commands, received = set(), set()
    if set(receipts) != set(ROLES):
        raise NetworkError('Missing endpoint result')
    for role, record in receipts.items():
        if (not isinstance(record, dict) or not isinstance(record.get('identity'), dict)
                or record['identity'].get('platform') != role or record['identity'].get('source_sha') != source_sha
                or record.get('passed') is not True):
            raise NetworkError('Endpoint result has the wrong native identity')
        for field, collected in (('commands', commands), ('verified', received)):
            cases = record.get(field)
            assigned = {case for case in expected if (case[0] if case[2] == 'push' else case[1]) == role} if field == 'commands' else {
                case for case in expected if case[1] == role}
            if (not isinstance(cases, list) or len(cases) != len(assigned)
                    or any(not isinstance(case, list) or len(case) != 4 or any(not isinstance(value, str) for value in case) for case in cases)
                    or {tuple(case) for case in cases} != assigned):
                raise NetworkError('Missing, duplicate or incorrectly assigned transfer case')
            collected.update(assigned)
    if commands != expected or received != expected:
        raise NetworkError('Directed transfer coverage is incomplete')


def prepare(root, api):
    nodes, devices, keys = records('ready'), api.devices(), load(root, 'keys')['keys']
    owned, ids, addresses = [], set(), set()
    for role, node in nodes.items():
        if (node.get('role') != role or node.get('key_id') != keys[role]
                or node.get('oidc_available') is not False or node.get('identity', {}).get('platform') != role
                or node['identity'].get('source_sha') != identity()['source_sha']):
            raise NetworkError('Invalid endpoint readiness')
        node_id, address = node['node_id'], node['address']
        matches = [device for device in devices if device.get('nodeId') == node_id]
        wanted = 'ci-' + identity()['run_id'] + '-' + identity()['run_attempt'] + '-' + role
        if len(matches) != 1 or matches[0].get('hostname') != wanted or node_id in ids or address in addresses:
            raise NetworkError('Endpoint node is not unique to this CI attempt')
        ids.add(node_id)
        addresses.add(address)
        owned.append(dict(node_id=node_id, device_id=matches[0]['id']))
    manifest = dict(identity(), repository_id='266877886', tailnet=TAILNET,
                    nodes={role: {key: nodes[role][key] for key in ('node_id', 'address')} for role in ROLES})
    grant = admission(manifest, identity(), devices, ROLES)
    save(root, 'lease', nodes=nodes, devices=owned, keys=keys, grant=grant)
    write(root, 'topology', nodes=nodes)


def revoke(api, lease):
    grant = lease['grant']
    def remove(policy):
        if grant in policy.get('grants', []):
            return update_policy(policy, grant, previous=grant, remove=True)
        check_policy(policy)
        return policy
    return api.change(remove)


def cleanup(api, lease):
    owners = {device['node_id'] for device in lease['devices']}
    errors = []
    try:
        revoke(api, lease)
    except ValueError:
        errors.append('Grant revocation failed')
    try:
        current = {device['id']: device.get('nodeId') for device in api.devices()}
    except ValueError:
        current = None
        errors.append('Device ownership could not be checked')
    if current is not None:
        for device in lease['devices']:
            actual = current.get(device['device_id'])
            if actual is not None and actual != device['node_id']:
                errors.append('Device identity changed')
                continue
            try:
                api.delete(device['device_id'])
            except ValueError:
                errors.append('Device removal failed')
    for key in lease['keys'].values():
        try:
            api.delete_key(key)
        except ValueError:
            errors.append('Key revocation failed')
    if owners.intersection(device.get('nodeId') for device in api.devices()):
        errors.append('Endpoint nodes remain after cleanup')
    if errors:
        raise NetworkError('; '.join(errors))


def recover(root):
    from interop_network import fetch, decode
    run_id = os.environ['INTEROP_RECOVER_RUN']
    attempt = os.environ['INTEROP_RECOVER_ATTEMPT']
    if not re.fullmatch(r'[1-9][0-9]*', run_id) or not re.fullmatch(r'[1-9][0-9]*', attempt):
        raise NetworkError('Invalid recovery run')
    body, _ = fetch('https://api.github.com/repos/RsyncProject/rsync/actions/runs/' + run_id + '/attempts/' + attempt,
                    os.environ['GH_TOKEN'], operation='Read recovery run')
    run = decode(body)
    if (run.get('path') != '.github/workflows/interoperability.yml' or run.get('head_branch') != 'master'
            or run.get('id') != int(run_id) or run.get('run_attempt') != int(attempt)
            or run.get('repository', {}).get('id') != 266877886
            or run.get('event') != 'workflow_run'
            or run.get('status') != 'completed'
            or run.get('conclusion') not in ('failure', 'cancelled', 'timed_out')
            or not re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', ''))):
        raise NetworkError('Recovery run is not a trusted controller')
    body, _ = fetch('https://api.github.com/repos/RsyncProject/rsync/actions/runs/' + run_id + '/attempts/' + attempt + '/jobs',
                    os.environ['GH_TOKEN'], operation='Read controller cleanup result')
    listing = decode(body)
    jobs = listing.get('jobs')
    count = listing.get('total_count')
    if type(count) is not int or count < 0 or not isinstance(jobs, list) or count != len(jobs):
        raise NetworkError('Invalid controller job listing')
    if not jobs:
        print('Controller never started')
        return
    if len(jobs) != 1:
        raise NetworkError('Unexpected controller jobs')
    job = jobs[0]
    if (job.get('name') == 'Admission and cleanup'
            and job.get('run_id') == int(run_id) and job.get('run_attempt') == int(attempt)
            and job.get('status') == 'completed'):
        if any(step.get('name') == 'Verify revocation and remove devices' and step.get('conclusion') == 'success'
               for step in job.get('steps', [])):
            print('Controller cleanup already completed')
            return
        issuance = [step for step in job.get('steps', []) if step.get('name') == 'Issue single-use enrolment keys']
        if (len(issuance) == 1 and issuance[0].get('status') == 'completed'
                and issuance[0].get('conclusion') == 'skipped'):
            print('Controller did not issue enrolment keys')
            return
    body, _ = fetch('https://api.github.com/repos/RsyncProject/rsync/actions/runs/' + run_id + '/artifacts?per_page=100',
                    os.environ['GH_TOKEN'], operation='Read recovery artifacts')
    listing = decode(body)
    if type(listing.get('total_count')) is not int or not 0 <= listing['total_count'] <= 100:
        raise NetworkError('Recovery artifact listing exceeds limit')
    name = 'interoperability-lease-attempt-' + attempt
    names = {name, 'interoperability-lease-' + attempt}
    matches = [item for item in listing['artifacts'] if item.get('name') in names and item.get('expired') is False]
    if len(matches) > 1:
        raise NetworkError('Duplicate recovery lease')
    if matches:
        name = matches[0]['name']
    lease, raw = download_records(run_id, {name: None}, timeout=60)[None]
    path = root / 'recovery-lease.json'
    path.write_bytes(raw)
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'GH_TOKEN') if key in os.environ}
    env['GH_PROMPT_DISABLED'] = '1'
    result = subprocess.run(['gh', 'attestation', 'verify', str(path), '--repo', 'RsyncProject/rsync',
                             '--signer-workflow', 'RsyncProject/rsync/.github/workflows/interoperability.yml',
                             '--signer-digest', run['head_sha'], '--source-ref', 'refs/heads/master',
                             '--deny-self-hosted-runners'], capture_output=True, timeout=60, env=env)
    if (result.returncode or lease.get('control_run_id') != run_id
            or lease.get('control_attempt') != str(run['run_attempt']) or lease.get('control_sha') != run['head_sha']):
        raise NetworkError('Recovery lease is not attested by the expected controller')
    cleanup(Tailnet(), lease)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('metadata', 'broker', 'prepare', 'admit', 'verify', 'revoke', 'finish', 'recover'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if args.phase == 'recover':
            recover(root)
            return 0
        if args.phase != 'metadata' and (root / 'context.json').exists():
            bind(load(root, 'context'))
        try:
            state = identity()
            if args.phase not in ('revoke', 'finish'):
                check_ci()
        except (NotSelected, PeerUnavailable) as error:
            if args.phase != 'metadata':
                raise
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                    stream.write('enabled=false\n')
            print(str(error))
            return 0
        if args.phase == 'metadata':
            if find_controller(exclude=os.environ['GITHUB_RUN_ID']) is not None:
                if os.environ.get('GITHUB_OUTPUT'):
                    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                        stream.write('enabled=false\n')
                print('A controller is already registered for this CI attempt')
                return 0
            save(root, 'context', head_sha=source()['head_sha'])
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                    stream.write('enabled=true\nattempt=' + state['run_attempt'] + '\n')
            return 0
        if args.phase in ('revoke', 'finish') and not any((root / (name + '.json')).exists() for name in ('lease', 'keys')):
            if args.phase == 'finish':
                write(root, 'result', passed=False, cleaned=True, roles=[])
            return 0
        api = Tailnet()
        if args.phase == 'broker':
            check_policy(api.policy()[0])
            recipients, keys = records('public', timeout=1800), {}
            if len({record.get('public_key') for record in recipients.values()}) != len(ROLES):
                raise NetworkError('Endpoints share an enrolment recipient')
            encrypted = {}
            save(root, 'keys', keys=keys, pending=None)
            for role in ROLES:
                check_ci()
                save(root, 'keys', keys=keys, pending=role)
                key_id, key = api.create_key(role, state)
                keys[role] = key_id
                save(root, 'keys', keys=keys, pending=None)
                encrypted[role] = seal(root, role, recipients[role], key_id, key, state)
            check_ci()
            write(root, 'enrolment', recipients=encrypted)
        elif args.phase == 'prepare':
            prepare(root, api)
        elif args.phase == 'admit':
            lease = load(root, 'lease')
            probes = records('quarantine')
            if any(record.get('passed') is not True or record.get('node_id') != lease['nodes'][role]['node_id']
                   for role, record in probes.items()):
                raise NetworkError('Endpoint quarantine checks failed')
            check_ci()
            policy = api.change(lambda current: update_policy(current, lease['grant']))
            write(root, 'admission', nodes=lease['nodes'], policy=policy)
        elif args.phase == 'verify':
            lease = load(root, 'lease')
            results = records('result', timeout=1800)
            if any(record.get('node_id') != lease['nodes'][role]['node_id'] for role, record in results.items()):
                raise NetworkError('Transfer result has a different node identity')
            verify_cases(results, state['source_sha'])
            write(root, 'verified', passed=True)
        elif args.phase == 'revoke':
            if (root / 'lease.json').exists():
                revoke(api, load(root, 'lease'))
                write(root, 'revocation', revoked=True)
        else:
            lease = load(root, 'lease') if (root / 'lease.json').exists() else None
            if lease:
                passed = (root / 'verified.json').exists()
                try:
                    if passed:
                        probes = records('revoked', timeout=120)
                        passed = all(
                            record.get('passed') is True
                            and record.get('node_id') == lease['nodes'][role]['node_id']
                            for role, record in probes.items())
                except NetworkError as error:
                    passed = False
                    print('Endpoint revocation was not confirmed: ' + str(error), file=sys.stderr)
                finally:
                    cleanup(api, lease)
                write(root, 'result', passed=passed, cleaned=True, roles=list(ROLES))
            elif (root / 'keys.json').exists():
                journal = load(root, 'keys')
                errors = []
                for key in journal['keys'].values():
                    try:
                        api.delete_key(key)
                    except NetworkError as error:
                        errors.append(str(error))
                if errors:
                    raise NetworkError('; '.join(errors))
                if 'pending' not in journal or journal['pending'] is not None:
                    raise NetworkError('An enrolment key request has an unknown outcome')
                if (root / 'enrolment.json').exists():
                    raise NetworkError('Enrolment may have been delivered; devices need reconciliation')
                write(root, 'result', passed=False, cleaned=True, roles=[])
            else:
                raise NetworkError('No admission lease was created')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, NetworkError) else type(error).__name__
        print('Network controller failed: ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
