import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

from interop_network import NetworkError, SLOTS, TAILNET_API, Tailnet, artifacts, context, validate_preflight
from interop_policy import TAILNET, admission, update_policy


def write(root, name, **data):
    (root / (name + '.json')).write_text(json.dumps(dict(context(), **data)) + '\n')


def receipts(phase, nodes):
    records = artifacts(phase, SLOTS, timeout=300)
    if set(records) != set(SLOTS):
        raise ValueError('Missing worker receipt')
    for slot, record in records.items():
        if record.get('slot') != slot or record.get('node_id') != nodes[slot]['node_id'] or record.get('passed') is not True:
            raise ValueError('Invalid worker receipt')
    return records


def prepare(root, api):
    validate_preflight(json.loads((root / 'preflight.json').read_text()))
    nodes = artifacts('ready', SLOTS, timeout=300)
    devices = api.devices()
    ids, ips, grants, owned = set(), set(), [], []
    for slot, node in nodes.items():
        if (node.get('slot') != slot or node.get('controller_exchange_denied') is not True
                or node.get('preferences') != {'RouteAll': False, 'CorpDNS': False}
                or node.get('worker_exchange_succeeded') is not True
                or set(node.get('authority_denials', {})) != {'controller_exchange', 'acl', 'devices'}
                or any(type(code) is not int or code not in ((401, 403) if key == 'controller_exchange' else (401, 403, 404))
                       for key, code in node['authority_denials'].items())
                or not isinstance(node.get('nonce'), str) or not re.fullmatch(r'[0-9a-f]{48}', node['nonce'])):
            raise ValueError('Invalid worker readiness')
        node_id, ip = node['node_id'], node['address']
        if node_id in ids or ip in ips:
            raise ValueError('Duplicate worker identity')
        ids.add(node_id)
        ips.add(ip)
        matches = [device for device in devices if device.get('nodeId') == node_id]
        hostname = 'rsync-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + slot
        if len(matches) != 1 or matches[0].get('hostname') != hostname:
            raise ValueError('Worker device does not belong to this attempt')
        owned.append(dict(node_id=node_id, device_id=matches[0]['id']))
    for cohort in ('a', 'b'):
        roles = (cohort + '1', cohort + '2')
        manifest = dict(context(), tailnet=TAILNET, repository_id='266877886',
                        nodes={slot: {key: nodes[slot][key] for key in ('node_id', 'address')} for slot in roles})
        grants.append(admission(manifest, context(), devices, roles=roles))
    write(root, 'lease', nodes=nodes, grants=grants, devices=owned)
    # Validate the existing policy without granting access before quarantine probes.
    def unchanged(policy):
        if policy.get('grants', []):
            raise ValueError('Remove outstanding admissions before running the isolation proof')
        for grant in grants:
            update_policy(policy, grant)
        return policy
    api.change(unchanged)
    write(root, 'topology', nodes=nodes, start_at=time.time() + 60)


def admit(root, api, lease):
    receipts('quarantine', lease['nodes'])
    def add(policy):
        for grant in lease['grants']:
            policy = update_policy(policy, grant)
        return policy
    api.change(add)
    write(root, 'admission', nodes=lease['nodes'], start_at=time.time() + 60)


def revoke(api, lease):
    def remove(policy):
        for grant in lease['grants']:
            if grant in policy.get('grants', []):
                policy = update_policy(policy, grant, previous=grant, remove=True)
            else:
                update_policy(policy, grant)
        return policy
    api.change(remove)


def finish(root, api, lease):
    error = None
    try:
        if all((root / (name + '.json')).exists() for name in ('admission', 'verified', 'revocation')):
            records = receipts('revoked', lease['nodes'])
            window = json.loads((root / 'revocation.json').read_text())
            if any(not window['end_at'] <= record.get('finished_at', 0) <= window['end_at'] + 30
                   for record in records.values()):
                raise ValueError('Revocation receipt is outside the live-listener window')
        else:
            raise ValueError('Admission or revocation did not complete')
    except (NetworkError, ValueError):
        error = True
    finally:
        try:
            revoke(api, lease)
        except (NetworkError, ValueError):
            error = True
        for device in lease['devices']:
            try:
                api.delete(device['device_id'])
            except (NetworkError, ValueError):
                error = True
        remaining = {device.get('nodeId') for device in api.devices()}
        if any(device['node_id'] in remaining for device in lease['devices']):
            raise ValueError('Worker devices remain after cleanup')
    if error:
        raise ValueError('Live isolation proof or cleanup did not complete')
    write(root, 'result', passed=True, scope='two live cohorts in one GitHub run',
          concurrent_github_runs_exercised=False, slots=list(SLOTS))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('preflight', 'prepare', 'admit', 'verify', 'revoke', 'finish'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        context()
        path = root / 'lease.json'
        if not path.exists() and args.phase in ('revoke', 'finish'):
            return 0
        api = Tailnet()
        if args.phase == 'preflight':
            api.devices()
            api.policy()
            write(root, 'preflight', api=TAILNET_API, readable=['acl', 'devices'])
        elif args.phase == 'prepare':
            prepare(root, api)
        else:
            lease = json.loads(path.read_text())
            if args.phase == 'admit':
                admit(root, api, lease)
            elif args.phase == 'verify':
                receipts('exercise', lease['nodes'])
                write(root, 'verified', passed=True)
            elif args.phase == 'revoke':
                revoke(api, lease)
                start = time.time() + 60
                write(root, 'revocation', start_at=start, end_at=start + 90)
            else:
                finish(root, api, lease)
    except (NetworkError, ValueError, KeyError, OSError) as error:
        detail = str(error) if isinstance(error, NetworkError) else type(error).__name__
        print('Interop controller failed during ' + args.phase + ': ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
